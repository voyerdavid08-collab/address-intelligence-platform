"""Shared flood-zone normalization helpers."""

import re


_ZONE_BASE = re.compile(r"^([A-Z]+)")


def flood_zone_base(code: str | None) -> str:
    """Strip suffixes and numeric subzone details from a flood-zone code."""
    if not isinstance(code, str):
        return ""
    match = _ZONE_BASE.match(code.strip().upper())
    return match.group(1) if match else ""


def risk_tier(base: str | None) -> str:
    """Map normalized flood-zone bases to a coarse risk tier."""
    if not isinstance(base, str):
        return "Unknown"
    normalized = base.strip().upper()
    if normalized.startswith(("A", "V")):
        return "High"
    if normalized == "SHX":
        return "Moderate"
    if normalized == "X":
        return "Minimal"
    return "Unknown"
