from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from typing import Any

REGISTRY_PATH = Path(__file__).with_name("registry.json")


@dataclass(frozen=True)
class PricingEntry:
    pricing_id: str
    provider: str
    canonical_model_id: str
    aliases: tuple[str, ...]
    variant: str
    input_usd_per_million: Decimal
    output_usd_per_million: Decimal
    cache_read_usd_per_million: Decimal | None
    cache_write_usd_per_million: Decimal | None
    cache_write_covered_by: str | None
    currency: str
    rate_unit: str
    effective_date: str | None
    verified_date: str
    source_url: str
    billing_mode: str
    requires_additional_conditions: bool
    conditions_note: str
    input_surcharges: tuple[dict[str, Any], ...]
    registry_version: str


def _decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    return Decimal(str(value))


@lru_cache(maxsize=1)
def load_registry() -> dict[str, Any]:
    payload: dict[str, Any] = json.loads(REGISTRY_PATH.read_text())
    return payload


def registry_version() -> str:
    return str(load_registry()["registry_version"])


def _entries() -> list[PricingEntry]:
    payload = load_registry()
    version = str(payload["registry_version"])
    entries: list[PricingEntry] = []
    for raw in payload["entries"]:
        entries.append(
            PricingEntry(
                pricing_id=raw["pricing_id"],
                provider=raw["provider"],
                canonical_model_id=raw["canonical_model_id"],
                aliases=tuple(item.lower() for item in raw.get("aliases", [])),
                variant=raw["variant"],
                input_usd_per_million=Decimal(str(raw["input_usd_per_million"])),
                output_usd_per_million=Decimal(str(raw["output_usd_per_million"])),
                cache_read_usd_per_million=_decimal(raw.get("cache_read_usd_per_million")),
                cache_write_usd_per_million=_decimal(raw.get("cache_write_usd_per_million")),
                cache_write_covered_by=raw.get("cache_write_covered_by"),
                currency=raw.get("currency", "USD"),
                rate_unit=raw.get("rate_unit", "per_million_tokens"),
                effective_date=raw.get("effective_date"),
                verified_date=raw["verified_date"],
                source_url=raw["source_url"],
                billing_mode=raw["billing_mode"],
                requires_additional_conditions=bool(raw.get("requires_additional_conditions")),
                conditions_note=str(raw.get("conditions_note") or ""),
                input_surcharges=tuple(raw.get("input_surcharges") or ()),
                registry_version=version,
            )
        )
    return entries


def normalize_model_id(*values: str | None) -> str | None:
    for value in values:
        if not value:
            continue
        text = value.strip().lower().replace("_", "-")
        text = " ".join(text.split())
        if text:
            return text.replace(" ", "-")
    return None


def lookup_pricing(
    *,
    model_id: str | None,
    model_display: str | None = None,
    variant: str | None,
    registry_version: str | None = None,
) -> PricingEntry | None:
    if not variant:
        return None
    wanted = (registry_version or load_registry()["registry_version"]).strip()
    needle = normalize_model_id(model_id, model_display)
    if needle is None:
        return None
    matches = [
        entry
        for entry in _entries()
        if entry.registry_version == wanted
        and entry.variant == variant
        and (needle == entry.canonical_model_id or needle in entry.aliases)
    ]
    if len(matches) != 1:
        return None
    return matches[0]


def variants_for_model(
    *, model_id: str | None, model_display: str | None = None
) -> list[PricingEntry]:
    needle = normalize_model_id(model_id, model_display)
    if needle is None:
        return []
    version = load_registry()["registry_version"]
    return [
        entry
        for entry in _entries()
        if entry.registry_version == version
        and (needle == entry.canonical_model_id or needle in entry.aliases)
    ]
