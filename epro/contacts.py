"""Extract name / phone / email from attachment text (regex, llm, hybrid)."""

from __future__ import annotations

import json
import os
import re

from epro.models import Contact

EMAIL_RE = re.compile(r"\b[A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,}\b", re.IGNORECASE)

# US-centric: (xxx) xxx-xxxx, xxx-xxx-xxxx, xxx.xxx.xxxx, +1 ...
PHONE_RE = re.compile(
    r"(?:\+?1[\s.\-]?)?(?:\(?\d{3}\)?[\s.\-]?)\d{3}[\s.\-]?\d{4}\b"
)

# Contract / document IDs get mistaken for phone numbers (PO-10700-00056653).
_ID_MASK_RE = re.compile(
    r"\b(?:PO|DOC|REQ|SOL|MA|PA)[-_]?\d{3,6}[-_]\d{5,}\b|\b\d{5}[-_]\d{7,}\b",
    re.IGNORECASE,
)

_FIELD_LABEL = r"(?:key\s+person(?:\s*#?\s*\d+)?|name|phone|e-?mail|title|fax)"
_NAME_CAPTURE = (
    r"([A-Z][A-Za-z.'\-]+(?:\s+(?!" + _FIELD_LABEL + r"\s*[:\-])[A-Z][A-Za-z.'\-]+){0,3})"
)

NAME_LABEL_RE = re.compile(
    r"(?i)(?:^|\n)\s*(?:"
    r"contact(?:\s+(?:name|person|rep(?:resentative)?))?|"
    r"key\s+person(?:\s*#?\s*\d+)?|"
    r"name|"
    r"representative|account\s+manager|vendor\s+rep(?:resentative)?|"
    r"attn|attention|prepared\s+by|submitted\s+by"
    r")\s*[:\-]\s*" + _NAME_CAPTURE
)

# Starts the next person block, including a "Name:" that shares a table row.
_BLOCK_SPLIT_RE = re.compile(
    r"(?i)(?:^|\n|\s)(?:key\s+person(?:\s*#?\s*\d+)?|name)\s*[:\-]"
)

_INLINE_NAME_RE = re.compile(
    r"(?i)(?:^|\n|(?<=\s))name\s*[:\-]\s*" + _NAME_CAPTURE
)

_BAD_NAME = re.compile(
    r"(?i)\b(phone|email|e-mail|fax|inc|llc|ltd|corp|company|vendor|address|http|"
    r"manager|director|assistant|assisant|coordinator|specialist|analyst|"
    r"officer|president|operations|emergency|data|administrator|purchaser)\b"
)

PERSON_NAME_RE = re.compile(
    r"\b([A-Z][a-z]+(?:[^\S\n]+[A-Z]\.)?(?:[^\S\n]+(?:de|da|del|der|di|la|le|van|von))?"
    r"(?:[^\S\n]+[A-Z][a-z.'\-]+))\b"
)

_NAME_PARTICLES = {"de", "da", "del", "der", "di", "la", "le", "van", "von", "bin", "al"}

# Heading / service-category words that look like First Last in PDFs.
_NON_PERSON_WORDS = {
    "monitoring", "debris", "event", "natural", "disaster", "state", "procurement",
    "public", "policy", "service", "services", "agreement", "contract", "exhibit",
    "attachment", "guide", "buyers", "buyer", "supplier", "vendor", "information",
    "department", "division", "office", "program", "project", "section", "category",
    "price", "list", "table", "page", "phone", "fax", "email", "website", "available",
    "name", "title", "person", "key",
    "including", "please", "contact", "customer", "support", "sales", "team", "group",
    "unit", "agency", "government", "executed", "master", "participating",
    "facilitator", "provider", "selection", "form", "template", "cost", "proposal",
    "terms", "conditions", "addendum", "amendment", "schedule", "appendix",
    "general", "special", "technical", "professional", "consulting", "management",
    "construction", "engineering", "equipment", "portland", "administrator",
    "administration", "purchase", "order", "summary", "document", "file", "cell",
    "mail", "county", "city", "university", "college", "school", "district",
    "board", "committee", "commission", "council", "authority", "bureau", "center",
    "institute", "association", "international", "national", "federal", "regional",
    "statewide", "municipal", "oregon", "naspo", "periscope", "epro",
}

DEFAULT_MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-20250514")
_MAX_LLM_CHARS = 8000


# PO-10700-00058569 – Culinary Depot
_AWARD_HEADING_RE = re.compile(
    r"(?i)((?:PO|PA|MA|DOC)\s*[-–—]\s*\d[\d\-]*\s*[-–—]\s*[A-Z0-9][^\n]{1,120})"
)
_COMPANY_SUFFIX_RE = re.compile(
    r"\b(incorporated|inc|l\.?l\.?c|llc|ltd|limited|corp|corporation|company|co)\b\.?",
    re.IGNORECASE,
)


def scope_to_contract(text: str, *, vendor_name: str = "", contract_id: str = "") -> str:
    """Keep the award block for this contract when one file lists several vendors.

    A price-agreement attachment often prints every awarded company. Contacts for
    the other companies are not part of this contract.
    """
    raw = text or ""
    if not raw.strip() or not (vendor_name or contract_id):
        return raw
    matches = list(_AWARD_HEADING_RE.finditer(raw))
    if len(matches) < 2:
        return raw
    sections: list[tuple[str, str]] = []
    for index, match in enumerate(matches):
        start = match.start()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(raw)
        sections.append((match.group(1).strip(), raw[start:end]))

    chosen = _sections_for_contract(sections, vendor_name=vendor_name, contract_id=contract_id)
    if chosen is None:
        return raw
    return "\n\n".join(chosen)


def _sections_for_contract(
    sections: list[tuple[str, str]],
    *,
    vendor_name: str,
    contract_id: str,
) -> list[str] | None:
    """Matching section bodies, [] when other vendors are listed, None to keep all text."""
    id_hits = [body for heading, body in sections if _heading_matches_contract(heading, contract_id)]
    if contract_id and 0 < len(id_hits) < len(sections):
        return id_hits
    vendor_hits = [body for heading, body in sections if _heading_matches_vendor(heading, vendor_name)]
    if vendor_name and 0 < len(vendor_hits) < len(sections):
        return vendor_hits
    if contract_id or vendor_name:
        return []
    return None


def _heading_matches_contract(heading: str, contract_id: str) -> bool:
    if not contract_id:
        return False
    compact_id = re.sub(r"[^a-z0-9]", "", contract_id.lower())
    compact_heading = re.sub(r"[^a-z0-9]", "", heading.lower())
    if len(compact_id) >= 6 and compact_id in compact_heading:
        return True
    numbers = [part for part in re.split(r"[^A-Za-z0-9]+", contract_id) if part.isdigit() and len(part) >= 5]
    if not numbers:
        return False
    tail = max(numbers, key=len)
    return re.search(rf"(?<!\d){re.escape(tail)}(?!\d)", heading) is not None


def _heading_matches_vendor(heading: str, vendor_name: str) -> bool:
    if not vendor_name:
        return False
    company = _company_from_heading(heading)
    vendor_tokens = _company_tokens(vendor_name)
    company_tokens = _company_tokens(company)
    if not vendor_tokens or not company_tokens:
        return False
    short, long = (
        (vendor_tokens, company_tokens)
        if len(vendor_tokens) <= len(company_tokens)
        else (company_tokens, vendor_tokens)
    )
    if not short <= long:
        return False
    return max(len(token) for token in short) >= 4


def _company_from_heading(heading: str) -> str:
    match = re.search(r"(?i)\d[\d\-]*\s*[-–—]\s*(.+)$", heading.strip())
    company = match.group(1) if match else heading
    company = re.split(r"(?i)\b(?:name|phone|e-?mail|website|fax)\s*:", company)[0]
    return company.strip(" .;-")


def _company_tokens(name: str) -> set[str]:
    text = (name or "").lower().replace("'", "").replace("’", "")
    text = _COMPANY_SUFFIX_RE.sub(" ", text)
    words = re.sub(r"[^a-z0-9]+", " ", text).split()
    return {word for word in words if len(word) >= 3 and word not in {"the", "and", "for"}}


def extract_contacts(
    text: str,
    *,
    mode: str = "hybrid",
    source: str = "",
    api_key: str | None = None,
    vendor_name: str = "",
    contract_id: str = "",
) -> list[Contact]:
    """Run regex / llm / hybrid extraction over attachment text."""
    mode = (mode or "hybrid").strip().lower()
    text = scope_to_contract(text, vendor_name=vendor_name, contract_id=contract_id)
    if not (text or "").strip():
        return []
    if mode == "regex":
        return extract_regex(text, source=source)
    if mode == "llm":
        return extract_llm(
            text, source=source, api_key=api_key, vendor_name=vendor_name, contract_id=contract_id
        )
    return extract_hybrid(
        text, source=source, api_key=api_key, vendor_name=vendor_name, contract_id=contract_id
    )


def extract_regex(text: str, source: str = "") -> list[Contact]:
    """Emails + valid US phones; person names from the text next to each email."""
    emails = list(dict.fromkeys(m.group(0) for m in EMAIL_RE.finditer(text)))
    phone_text = _ID_MASK_RE.sub(" ", text)
    phones = list(
        dict.fromkeys(
            p
            for m in PHONE_RE.finditer(phone_text)
            if (p := _normalize_phone_raw(m.group(0)))
        )
    )

    contacts: list[Contact] = []
    used_phones: set[str] = set()

    for email in emails:
        name, nearby_phone = _neighbors(text, email)
        phone = nearby_phone
        if phone:
            used_phones.add(_digits(phone))
        contacts.append(Contact(name=name, phone=phone, email=email, source=source))

    for phone in phones:
        if _digits(phone) in used_phones:
            continue
        name, _ = _neighbors(text, phone)
        if not is_person_name(name):
            continue
        contacts.append(Contact(name=name, phone=phone, email="", source=source))

    return [c for c in contacts if not c.is_empty()]


def is_person_name(name: str) -> bool:
    """True for First Last style names, not service headings like 'Debris Monitoring'."""
    cleaned = _clean_name(name)
    if not cleaned or "@" in cleaned or any(ch.isdigit() for ch in cleaned):
        return False
    words = [w.strip(".,") for w in cleaned.replace(",", " ").split() if w.strip(".,")]
    if not (2 <= len(words) <= 4):
        return False
    alpha = 0
    for word in words:
        lower = word.lower().rstrip(".")
        if lower in _NAME_PARTICLES:
            continue
        # "Page" is a heading word and a real surname (Morgan Page).
        if lower == "page":
            if words[0].lower().rstrip(".") == "page":
                return False
            alpha += 1
            continue
        if lower in _NON_PERSON_WORDS:
            return False
        if not re.fullmatch(r"[A-Z][a-z]+(?:['\-][A-Z]?[a-z]+)?|[A-Z]\.?", word):
            return False
        if len(lower) >= 2:
            alpha += 1
    return alpha >= 2


def extract_llm(
    text: str,
    source: str = "",
    api_key: str | None = None,
    vendor_name: str = "",
    contract_id: str = "",
) -> list[Contact]:
    """Ask Claude for contacts as JSON. Returns [] if the API is unavailable."""
    key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")
    if not key:
        return []
    snippet = text[:_MAX_LLM_CHARS]
    prompt = (
        "Extract every person contact from this procurement attachment text.\n"
        "Return JSON only, no markdown, with this shape:\n"
        '{"contacts": [{"name": "", "phone": "", "email": "", "source_hint": "short quote"}]}\n'
        "Rules:\n"
        "- name must be a person's name (First Last). Never use a service, "
        "category, heading, company, or job title as the name "
        '(not "Debris Monitoring", "State Procurement", "Public Policy").\n'
        "- Do not invent emails or phone numbers. Use empty string if missing.\n"
        "- Ignore struck-through or replaced text. When a name, phone, or email "
        "is crossed out and a replacement is shown, keep only the replacement.\n"
        "- A person who only changed company email or phone is still that same person, "
        "not an additional contact.\n"
        "- If several companies are listed, keep only the company awarded this contract. "
        "Ignore contacts printed for the other companies.\n"
        + _vendor_focus_rule(vendor_name, contract_id)
        + "- Skip government buyers (.gov / .mil emails) unless they are the only contact.\n"
        "- Ignore mailing addresses, DUNS, FEIN, and contract numbers.\n\n"
        f"Source file: {source or 'attachment'}\n\n"
        f"{snippet}"
    )
    try:
        import anthropic

        client = anthropic.Anthropic(api_key=key)
        message = client.messages.create(
            model=DEFAULT_MODEL,
            max_tokens=2000,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = "".join(
            block.text for block in message.content if getattr(block, "type", "") == "text"
        )
    except Exception:
        return []
    return _contacts_from_json(raw, source)


def extract_hybrid(
    text: str,
    source: str = "",
    api_key: str | None = None,
    vendor_name: str = "",
    contract_id: str = "",
) -> list[Contact]:
    """Regex owns email/phone. LLM fills names and leftover blocks when needed."""
    regex_contacts = extract_regex(text, source=source)
    needs_llm = False
    if not regex_contacts:
        needs_llm = True
    elif any((c.email or c.phone) and not is_person_name(c.name) for c in regex_contacts):
        needs_llm = True
    if not needs_llm:
        return regex_contacts

    llm_contacts = extract_llm(
        text, source=source, api_key=api_key, vendor_name=vendor_name, contract_id=contract_id
    )
    if not regex_contacts:
        return [c for c in llm_contacts if is_person_name(c.name) or c.email]
    return _merge_hybrid(regex_contacts, llm_contacts)


def _vendor_focus_rule(vendor_name: str, contract_id: str) -> str:
    if not vendor_name and not contract_id:
        return ""
    who = vendor_name.strip() or "the awarded vendor"
    number = f" ({contract_id.strip()})" if contract_id.strip() else ""
    return f"- This contract is {who}{number}. Do not return anyone from another company.\n"


def _merge_hybrid(regex_contacts: list[Contact], llm_contacts: list[Contact]) -> list[Contact]:
    """Keep regex email/phone; copy names from LLM when they match email or phone."""
    merged: list[Contact] = []
    for rc in regex_contacts:
        name = rc.name if is_person_name(rc.name) else ""
        if not name:
            for lc in llm_contacts:
                if not is_person_name(lc.name):
                    continue
                if rc.email and lc.email and rc.email.lower() == lc.email.lower():
                    name = lc.name
                    break
                if rc.phone and lc.phone and _digits(rc.phone) == _digits(lc.phone):
                    name = lc.name
                    break
        merged.append(Contact(
            name=name,
            phone=rc.phone,
            email=rc.email,
            source=rc.source,
            source_hint=rc.source_hint,
        ))
    return merged


def _contacts_from_json(raw: str, source: str) -> list[Contact]:
    blob = raw.strip()
    if blob.startswith("```"):
        blob = re.sub(r"^```(?:json)?\s*|\s*```$", "", blob, flags=re.IGNORECASE).strip()
    try:
        data = json.loads(blob)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", blob, re.DOTALL)
        if not match:
            return []
        try:
            data = json.loads(match.group(0))
        except json.JSONDecodeError:
            return []
    items = data.get("contacts") if isinstance(data, dict) else data
    if not isinstance(items, list):
        return []
    contacts: list[Contact] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "")
        if name and not is_person_name(name):
            name = ""
        c = Contact(
            name=_clean_name(name) if name else "",
            phone=str(item.get("phone") or "").strip(),
            email=str(item.get("email") or "").strip(),
            source=source,
            source_hint=str(item.get("source_hint") or "").strip(),
        )
        if not c.is_empty():
            contacts.append(c)
    return contacts


def _neighbors(text: str, needle: str) -> tuple[str, str]:
    """Person name and valid phone from the same person block as an email or phone."""
    idx = text.lower().find(needle.lower())
    if idx < 0:
        return "", ""
    start, end = _block_around(text, idx, len(needle))
    window = text[start:end]
    name = _name_for_value(window, needle, idx - start)
    phone = _phone_for_person(text, idx, needle, name) if "@" in needle else ""
    return name, phone


def _block_around(text: str, idx: int, length: int) -> tuple[int, int]:
    """Window around a value, cut off at the next person's name label."""
    start = max(0, idx - 420)
    end = min(len(text), idx + length + 180)
    for match in _BLOCK_SPLIT_RE.finditer(text, idx + length, end):
        end = match.start()
        break
    return start, end


def _phone_for_person(text: str, email_idx: int, email: str, name: str) -> str:
    """Phone that belongs to this person, not the next person in the same table."""
    owner = name or _preceding_labelled_name(text, email_idx)
    start, end = _block_around(text, email_idx, len(email))
    phone_window = _ID_MASK_RE.sub(" ", text)
    best: tuple[int, str] | None = None
    for match in PHONE_RE.finditer(phone_window):
        if match.start() < start or match.start() >= end:
            continue
        candidate = _normalize_phone_raw(match.group(0))
        if not candidate:
            continue
        phone_owner = _preceding_labelled_name(text, match.start())
        if owner and phone_owner and not _same_name_tokens(owner, phone_owner):
            continue
        dist = abs(match.start() - email_idx)
        if best is None or dist < best[0]:
            best = (dist, candidate)
    return best[1] if best else ""


def _preceding_labelled_name(text: str, idx: int) -> str:
    """Last 'Name:' person at or before idx, including a second name on the same line."""
    found = ""
    for match in _INLINE_NAME_RE.finditer(text, 0, max(idx, 0) + 1):
        if match.start() > idx:
            break
        candidate = _clean_name(match.group(1))
        if is_person_name(candidate):
            found = candidate
    return found


def _same_name_tokens(a: str, b: str) -> bool:
    left = [w.lower().strip(".") for w in a.split()]
    right = [w.lower().strip(".") for w in b.split()]
    return left == right


def _name_for_value(window: str, needle: str, needle_at: int) -> str:
    """Pick a person name near needle, preferring last names that match an email."""
    hints = _email_name_hints(needle) if "@" in needle else []
    labelled = []
    for pattern in (NAME_LABEL_RE, _INLINE_NAME_RE):
        for match in pattern.finditer(window):
            candidate = _clean_name(match.group(1))
            if is_person_name(candidate):
                labelled.append((abs(match.start() - needle_at), candidate))
    if labelled:
        labelled.sort()
        if hints:
            for _, candidate in labelled:
                if _name_matches_hints(candidate, hints):
                    return candidate
        else:
            return labelled[0][1]

    scored: list[tuple[int, int, str]] = []
    for match in PERSON_NAME_RE.finditer(window):
        candidate = _clean_name(match.group(1))
        if not is_person_name(candidate):
            continue
        dist = abs(match.start() - needle_at)
        score = max(0, 8 - dist // 50)
        if _name_matches_hints(candidate, hints):
            score += 20
        scored.append((score, -dist, candidate))
    if not scored:
        return ""
    scored.sort(reverse=True)
    best_score, _, best_name = scored[0]
    if hints and best_score >= 20:
        return best_name
    if best_score >= 4:
        return best_name
    return ""


def _email_name_hints(email: str) -> list[str]:
    """Tokens from the email local-part: JJohnson -> johnson; r.berger -> berger."""
    local = (email or "").split("@", 1)[0]
    parts = re.split(r"[._+\-]+", local)
    tokens: list[str] = []
    for part in parts:
        pieces = re.findall(r"[A-Z]?[a-z]+|[A-Z]+(?![a-z])", part) or [part]
        tokens.extend(pieces)
    return [t.lower() for t in tokens if len(t) >= 2]


def _name_matches_hints(name: str, hints: list[str]) -> bool:
    if not hints:
        return False
    words = [w.lower().strip(".") for w in name.split()]
    return any(word in hints for word in words if word not in _NAME_PARTICLES)


def _clean_name(raw: str) -> str:
    name = re.sub(r"\s+", " ", (raw or "")).strip(" ,;:-")
    name = re.sub(r"\b(Mr|Mrs|Ms|Dr)\.?\s+", "", name, flags=re.IGNORECASE)
    if not name or len(name) < 2 or len(name) > 80:
        return ""
    if "@" in name or _BAD_NAME.search(name):
        return ""
    if re.search(
        r"(?i)\b(guide|buyers|supplier|contact|information|services|"
        r"attachment|agreement|addendum|naspo|oregon)\b",
        name,
    ):
        return ""
    if not re.search(r"[A-Za-z]", name):
        return ""
    words = name.split()
    if len(words) > 5:
        return ""
    return name


def _normalize_phone_raw(raw: str) -> str:
    digits = _digits(raw)
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    if len(digits) != 10:
        return ""
    npa, nxx = digits[:3], digits[3:6]
    if npa[0] in "01" or nxx[0] in "01":
        return ""
    return f"({digits[:3]}) {digits[3:6]}-{digits[6:]}"


def _digits(raw: str) -> str:
    return re.sub(r"\D", "", raw or "")
