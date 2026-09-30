"""Shared contact record used across scrape, extract, and match."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field


@dataclass
class Contact:
    name: str = ""
    phone: str = ""
    email: str = ""
    source: str = ""
    source_hint: str = ""

    def is_empty(self) -> bool:
        return not (self.name.strip() or self.phone.strip() or self.email.strip())

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass
class AttachmentFile:
    filename: str
    url: str
    path: str = ""
    text: str = ""
    notes: list[str] = field(default_factory=list)


@dataclass
class ContractAudit:
    contract_id: str
    extras: dict = field(default_factory=dict)
    po_url: str = ""
    attachments_url: str = ""
    vendor_name: str = ""
    contract_contact: Contact = field(default_factory=Contact)
    attachment_contacts: list[Contact] = field(default_factory=list)
    attachment_files: list[str] = field(default_factory=list)
    match_status: str = ""
    notes: list[str] = field(default_factory=list)
    error: str = ""
    new_contacts: list[Contact] = field(default_factory=list)

    def as_log_dict(self) -> dict:
        return {
            "contract_id": self.contract_id,
            "extras": self.extras,
            "po_url": self.po_url,
            "attachments_url": self.attachments_url,
            "vendor_name": self.vendor_name,
            "contract_contact": self.contract_contact.as_dict(),
            "attachment_contacts": [c.as_dict() for c in self.attachment_contacts],
            "new_contacts": [c.as_dict() for c in self.new_contacts],
            "attachment_files": self.attachment_files,
            "match_status": self.match_status,
            "notes": self.notes,
            "error": self.error,
        }

    @classmethod
    def from_log_dict(cls, data: dict) -> "ContractAudit":
        def _contact(raw) -> Contact:
            raw = raw or {}
            return Contact(
                name=str(raw.get("name") or ""),
                phone=str(raw.get("phone") or ""),
                email=str(raw.get("email") or ""),
                source=str(raw.get("source") or ""),
                source_hint=str(raw.get("source_hint") or ""),
            )

        return cls(
            contract_id=str(data.get("contract_id") or ""),
            extras=dict(data.get("extras") or {}),
            po_url=str(data.get("po_url") or ""),
            attachments_url=str(data.get("attachments_url") or ""),
            vendor_name=str(data.get("vendor_name") or ""),
            contract_contact=_contact(data.get("contract_contact")),
            attachment_contacts=[_contact(c) for c in data.get("attachment_contacts") or []],
            attachment_files=[str(x) for x in data.get("attachment_files") or []],
            match_status=str(data.get("match_status") or ""),
            notes=[str(x) for x in data.get("notes") or []],
            error=str(data.get("error") or ""),
            new_contacts=[_contact(c) for c in data.get("new_contacts") or []],
        )
