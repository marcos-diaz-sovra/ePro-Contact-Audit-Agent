"""Build public Periscope/ePro PO-summary and Attachments-tab URLs."""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import quote, unquote, urlparse
import re


@dataclass(frozen=True)
class ContractUrls:
    contract_id: str
    po_url: str
    attachments_url: str
    base_url: str


def build_po_url(base_url: str, ext: str, contract_id: str) -> str:
    """Public purchase-order summary page for ``contract_id``."""
    root = _origin(base_url)
    suffix = ext.lstrip(".")
    doc_id = quote(contract_id.strip(), safe="-_.")
    return (
        f"{root}/bso/external/purchaseorder/poSummary.{suffix}"
        f"?docId={doc_id}&releaseNbr=0&parentUrl=close"
    )


def build_attachments_url(base_url: str, ext: str, contract_id: str) -> str:
    """Public Attachments tab for ``contract_id`` (docType=P)."""
    root = _origin(base_url)
    suffix = ext.lstrip(".")
    doc_id = quote(contract_id.strip(), safe="-_.")
    return (
        f"{root}/bso/document/attachments/attachments.{suffix}"
        f"?docType=P&docId={doc_id}&releaseNbr=0"
    )


def build_urls(state: dict, contract_id: str) -> ContractUrls:
    """Return both public URLs for a contract on the given ePro site."""
    base = state["base_url"]
    ext = state["ext"]
    cid = contract_id.strip()
    return ContractUrls(
        contract_id=cid,
        po_url=build_po_url(base, ext, cid),
        attachments_url=build_attachments_url(base, ext, cid),
        base_url=_origin(base),
    )


def parse_contract_url(url: str) -> ContractUrls | None:
    """Parse a public PO-summary or attachments URL into origin, ext, and docId."""
    raw = (url or "").strip()
    if not raw.startswith("http"):
        return None
    parsed = urlparse(raw)
    if not parsed.netloc:
        return None
    origin = f"{parsed.scheme}://{parsed.netloc}".rstrip("/")
    ext = "sda" if ".sda" in raw.lower() else "sdo"
    qs = parsed.query
    match = re.search(r"(?:^|[&?])docId=([^&]+)", qs, re.IGNORECASE)
    if not match:
        match = re.search(r"docId=([^&]+)", raw, re.IGNORECASE)
    if not match:
        return None
    cid = unquote(match.group(1)).strip()
    if not cid:
        return None
    return ContractUrls(
        contract_id=cid,
        po_url=build_po_url(origin, ext, cid),
        attachments_url=build_attachments_url(origin, ext, cid),
        base_url=origin,
    )


def site_from_url(url: str) -> tuple[str, str]:
    """Return (origin, ext) from any ePro page URL."""
    raw = (url or "").strip()
    parsed = urlparse(raw if "://" in raw else f"https://{raw}")
    origin = f"{parsed.scheme or 'https'}://{(parsed.netloc or parsed.path.split('/')[0])}".rstrip("/")
    ext = "sda" if ".sda" in raw.lower() else "sdo"
    return origin, ext


def _origin(base_url: str) -> str:
    parsed = urlparse(base_url if "://" in base_url else f"https://{base_url}")
    scheme = parsed.scheme or "https"
    netloc = parsed.netloc or parsed.path.split("/")[0]
    return f"{scheme}://{netloc}".rstrip("/")
