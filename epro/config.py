"""Load per-state ePro site configuration from YAML."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from epro.paths import resource_root

PROJECT_ROOT = resource_root()
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config" / "states.yaml"

DEFAULT_LABELS = {
    "vendor": [
        "Vendor", "Vendor Name", "Supplier", "Supplier Name",
        "Contractor", "Contractor Name", "Organization Name", "Legal Name",
    ],
    "name": [
        "Contact Name", "Primary Contact Name", "Primary Contact",
        "Vendor Contact", "Vendor Representative",
        "Emergency Contact Name",
    ],
    "phone": [
        "Phone", "Contact Phone", "Telephone", "Phone Number",
        "Vendor Phone", "Emergency Phone",
    ],
    "email": [
        "Email", "Vendor Email", "Contact Email", "E-mail",
        "Email Address", "Emergency Email",
    ],
}


def load_states(path: Path | None = None) -> dict[str, dict[str, Any]]:
    """Return the full states mapping from YAML."""
    cfg_path = path or DEFAULT_CONFIG_PATH
    if not cfg_path.is_file():
        raise FileNotFoundError(f"State config not found: {cfg_path}")
    data = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ValueError(f"State config must be a mapping: {cfg_path}")
    return data


def list_states(path: Path | None = None) -> list[str]:
    """Return configured state names, sorted."""
    return sorted(load_states(path).keys())


def default_site() -> dict[str, Any]:
    """Site used when the spreadsheet has contract IDs and no URLs.

    Builds public PO pages as:
    {origin}/bso/external/purchaseorder/poSummary.sdo?docId={id}&releaseNbr=0&parentUrl=close
    """
    try:
        return resolve_state("Oregon")
    except Exception:
        return resolve_state(base_url="https://oregonbuys.gov", ext="sdo")


def resolve_state(
    name: str | None = None,
    *,
    base_url: str | None = None,
    ext: str | None = None,
    config_path: Path | None = None,
) -> dict[str, Any]:
    """Build a site config from a named preset or an ePro origin URL."""
    cfg: dict[str, Any] = {}
    if name:
        states = load_states(config_path)
        key = _match_state_name(name, states)
        if key is None:
            known = ", ".join(sorted(states)) or "(none)"
            raise KeyError(f"Unknown state '{name}'. Known: {known}")
        cfg = dict(states[key])
        cfg["name"] = key
    elif base_url:
        cfg["name"] = "site"
    else:
        raise ValueError("Provide an ePro site URL")

    if base_url:
        from epro.urls import site_from_url

        origin, inferred_ext = site_from_url(base_url)
        cfg["base_url"] = origin
        if not ext:
            ext = inferred_ext
    if ext:
        cfg["ext"] = ext.lstrip(".")

    if not cfg.get("base_url"):
        raise ValueError("Could not determine the ePro site URL")
    cfg["base_url"] = str(cfg["base_url"]).rstrip("/")
    cfg["ext"] = str(cfg.get("ext") or "sdo").lstrip(".")
    cfg["contact_mode"] = cfg.get("contact_mode") or "vendor_profile"
    labels = cfg.get("labels") or {}
    merged = {}
    for field, defaults in DEFAULT_LABELS.items():
        merged[field] = list(labels.get(field) or defaults)
    cfg["labels"] = merged
    cfg.setdefault("name", name or "site")
    return cfg


def _match_state_name(name: str, states: dict[str, Any]) -> str | None:
    needle = name.strip().lower()
    for key in states:
        if key.lower() == needle:
            return key
    aliases = {"new jersey": "NJ", "njstart": "NJ", "oregonbuys": "Oregon"}
    alias = aliases.get(needle)
    if alias and alias in states:
        return alias
    return None
