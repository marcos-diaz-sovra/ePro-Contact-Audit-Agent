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

NAME_LABEL_RE = re.compile(
    r"(?i)(?:^|\n)\s*(?:"
    r"contact(?:\s+(?:name|person|rep(?:resentative)?))?|"
    r"representative|account\s+manager|vendor\s+rep(?:resentative)?|"
    r"attn|attention|prepared\s+by|submitted\s+by"
    r")\s*[:\-]\s*([A-Z][A-Za-z.'\-]+(?:\s+[A-Z][A-Za-z.'\-]+){0,4})"
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


def extract_contacts(
    text: str,
    *,
    mode: str = "hybrid",
    source: str = "",
    api_key: str | None = None,
) -> list[Contact]:
    """Run regex / llm / hybrid extraction over attachment text."""
    mode = (mode or "hybrid").strip().lower()
    if not (text or "").strip():
        return []
    if mode == "regex":
        return extract_regex(text, source=source)
    if mode == "llm":
        return extract_llm(text, source=source, api_key=api_key)
    return extract_hybrid(text, source=source, api_key=api_key)


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
        if lower in _NON_PERSON_WORDS:
            return False
        if not re.fullmatch(r"[A-Z][a-z]+(?:['\-][A-Z]?[a-z]+)?|[A-Z]\.?", word):
            return False
        if len(lower) >= 2:
            alpha += 1
    return alpha >= 2


def extract_llm(text: str, source: str = "", api_key: str | None = None) -> list[Contact]:
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
        "- Skip government buyers (.gov / .mil emails) unless they are the only contact.\n"
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


def extract_hybrid(text: str, source: str = "", api_key: str | None = None) -> list[Contact]:
    """Regex owns email/phone. LLM fills names and leftover blocks when needed."""
    regex_contacts = extract_regex(text, source=source)
    needs_llm = False
    if not regex_contacts:
        needs_llm = True
    elif any((c.email or c.phone) and not is_person_name(c.name) for c in regex_contacts):
        needs_llm = True
    if not needs_llm:
        return regex_contacts

    llm_contacts = extract_llm(text, source=source, api_key=api_key)
    if not regex_contacts:
        return [c for c in llm_contacts if is_person_name(c.name) or c.email]
    return _merge_hybrid(regex_contacts, llm_contacts)


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
    """Person name and valid phone from the window around an email or phone."""
    idx = text.lower().find(needle.lower())
    if idx < 0:
        return "", ""
    before = 420
    after = 180
    start = max(0, idx - before)
    window = text[start: idx + len(needle) + after]
    phone = ""
    if "@" in needle:
        phone_window = _ID_MASK_RE.sub(" ", window)
        needle_at = idx - start
        best: tuple[int, str] | None = None
        for match in PHONE_RE.finditer(phone_window):
            candidate = _normalize_phone_raw(match.group(0))
            if not candidate:
                continue
            dist = abs(match.start() - needle_at)
            if best is None or dist < best[0]:
                best = (dist, candidate)
        phone = best[1] if best else ""
    name = _name_for_value(window, needle, idx - start)
    return name, phone


def _name_for_value(window: str, needle: str, needle_at: int) -> str:
    """Pick a person name near needle, preferring last names that match an email."""
    hints = _email_name_hints(needle) if "@" in needle else []
    labelled = []
    for match in NAME_LABEL_RE.finditer(window):
        candidate = _clean_name(match.group(1))
        if is_person_name(candidate):
            labelled.append((abs(match.start() - needle_at), candidate))
    if labelled:
        labelled.sort()
        if hints:
            for _, candidate in labelled:
                if _name_matches_hints(candidate, hints):
                    return candidate
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
