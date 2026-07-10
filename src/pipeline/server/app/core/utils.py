"""Small shared helpers used across API routes, services, and domain logic."""

from __future__ import annotations


def normalize_bearing_type(value: str | None) -> str:
    """Return 'underhang' or 'overhang'; default to 'underhang'."""
    if not value:
        return "underhang"
    normalized = value.strip().lower()
    return normalized if normalized in {"underhang", "overhang"} else "underhang"
