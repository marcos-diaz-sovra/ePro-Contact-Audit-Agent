"""Normalize and compare contract-page contacts vs attachment contacts."""

from __future__ import annotations

import re
from difflib import SequenceMatcher

from epro.contacts import is_person_name
from epro.models import Contact, ContractAudit

MATCH = "MATCH"
NEW_CONTACTS = "NEW_CONTACTS"
NO_CONTACTS = "NO_CONTACTS"
ERROR = "ERROR"
CONTRACT_ONLY = "CONTRACT_ONLY"

NAME_RATIO = 0.85


def normalize_email(raw: str) -> str:
    return (raw or "").strip().lower()


def normalize_phone(raw: str) -> str:
    digits = re.sub(r"\D", "", raw or "")
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    if len(digits) == 10:
        return f"({digits[:3]}) {digits[3:6]}-{digits[6:]}"
    return (raw or "").strip()


def normalize_name(raw: str) -> str:
    tokens = re.findall(r"[a-z0-9]+", (raw or "").lower())
    return " ".join(sorted(tokens))


def names_match(a: str, b: str) -> bool:
    if not a or not b:
        return False
    ka, kb = normalize_name(a), normalize_name(b)
    if not ka or not kb:
        return False
    if ka == kb:
        return True
    return SequenceMatcher(None, ka, kb).ratio() >= NAME_RATIO


def phones_match(a: str, b: str) -> bool:
    da = re.sub(r"\D", "", a or "")
    db = re.sub(r"\D", "", b or "")
    if len(da) == 11 and da.startswith("1"):
        da = da[1:]
    if len(db) == 11 and db.startswith("1"):
        db = db[1:]
    return bool(da) and da == db


def emails_match(a: str, b: str) -> bool:
    ea, eb = normalize_email(a), normalize_email(b)
    return bool(ea) and ea == eb


def same_person(a: Contact, b: Contact) -> bool:
    """True when two contacts share an email, phone, or (when both named) a name."""
    if a.email and b.email and emails_match(a.email, b.email):
        return True
    if a.phone and b.phone and phones_match(a.phone, b.phone):
        return True
    if a.name and b.name and names_match(a.name, b.name):
        return True
    return False


_GOV_EMAIL = re.compile(r"@[a-z0-9.-]+\.(gov|mil)\b", re.IGNORECASE)


def is_government_contact(contact: Contact) -> bool:
    """State/buyer contacts (e.g. das.oregon.gov) are not vendor discrepancies."""
    if contact.email and _GOV_EMAIL.search(contact.email):
        return True
    if re.search(r"(?i)\b(contract administrator|purchaser)\b", contact.name or ""):
        return True
    return False


def is_company_name(name: str, vendor_name: str = "") -> bool:
    text = (name or "").strip()
    if not text:
        return False
    if vendor_name and names_match(text, vendor_name):
        return True
    return bool(re.search(r"(?i)\b(inc|llc|ltd|corp|corporation|company|co)\b\.?", text))


def new_attachment_contacts(audit: ContractAudit) -> list[Contact]:
    """Attachment contacts that are not already on the vendor profile."""
    profile = audit.contract_contact
    new: list[Contact] = []
    for att in audit.attachment_contacts:
        if att.is_empty():
            continue
        if att.name and not is_person_name(att.name):
            att.name = ""
        if not (att.email or att.phone):
            continue
        if not att.email and not is_person_name(att.name):
            continue
        if is_government_contact(att):
            continue
        if is_company_name(att.name, audit.vendor_name):
            continue
        if not profile.is_empty() and same_person(profile, att):
            continue
        new.append(att)
    return new


def compare(audit: ContractAudit) -> ContractAudit:
    """Set match_status, new_contacts, and notes. Always deterministic."""
    attachments = [c for c in audit.attachment_contacts if not c.is_empty()]
    has_contract = not audit.contract_contact.is_empty()
    has_attach = bool(attachments)
    audit.new_contacts = new_attachment_contacts(audit)

    if audit.error and not has_contract and not has_attach:
        audit.match_status = ERROR
        if audit.error not in audit.notes:
            audit.notes.append(audit.error)
        return audit
    if not has_contract and not has_attach:
        audit.match_status = NO_CONTACTS
        audit.notes.append("No contacts found on the vendor profile or in agency attachments")
        return audit
    if has_contract and not has_attach:
        audit.match_status = CONTRACT_ONLY
        audit.notes.append("No contacts found in agency attachments")
        return audit
    if audit.new_contacts:
        audit.match_status = NEW_CONTACTS
        n = len(audit.new_contacts)
        audit.notes.append(
            f"{n} new supplier contact{'s' if n != 1 else ''} in agency attachments"
        )
        return audit
    audit.match_status = MATCH
    audit.notes.append("No additional supplier contacts in agency attachments")
    return audit
