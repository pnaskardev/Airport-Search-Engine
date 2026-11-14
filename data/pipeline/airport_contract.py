"""
The Contract: the Served fields of an Airport document.

A Served field is one users of the search see in results. This module is the single
place that says what they are. gen_types.py generates the Go struct and the
TypeScript interface from it, and backfill.py checks every document against it, so a
field can only change here.

Search-only fields (ALIASES, TRANSLATIONS, KEYWORDS, icao_code) and bookkeeping
(id, ENRICHED) are deliberately absent. Ranking and searchable order stay in seed.py.

Standard library only.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

STRING = "string"
INTEGER = "integer"
ENUM = "enum"
POINT = "point"  # {"lat": float, "lng": float}


@dataclass(frozen=True)
class Field:
    name: str
    kind: str
    optional: bool = False
    values: tuple[str, ...] = ()


SERVED_FIELDS: tuple[Field, ...] = (
    Field("iata_code", STRING),
    Field("name", STRING),
    Field("CITY_NAME", STRING),
    Field("COUNTRY_NAME", STRING),
    # Absent or empty when the airport's iso_region has no entry in regions.csv.
    Field("REGION_NAME", STRING, optional=True),
    Field("TYPE", ENUM, values=("LARGE", "MEDIUM")),
    Field("POPULARITY_SCORE", INTEGER),
    # Absent when the source coordinates do not parse.
    Field("_geo", POINT, optional=True),
)


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _problem(field: Field, value: Any) -> str | None:
    """Why `value` is not valid for `field`, or None if it is."""
    if field.kind == STRING and not isinstance(value, str):
        return f"{field.name} must be a string, got {value!r}"
    if field.kind == INTEGER and (not isinstance(value, int) or isinstance(value, bool)):
        return f"{field.name} must be an integer, got {value!r}"
    if field.kind == ENUM and value not in field.values:
        return f"{field.name} {value!r} not in {'|'.join(field.values)}"
    if field.kind == POINT and not (
        isinstance(value, dict)
        and _is_number(value.get("lat"))
        and _is_number(value.get("lng"))
    ):
        return f"{field.name} must be {{lat, lng}} numbers, got {value!r}"
    return None


def violations(doc: dict[str, Any]) -> list[str]:
    """Every way `doc` breaks the Contract. Empty means the document is valid.

    Pure: no I/O, no exceptions, so a future queue worker can call it unchanged.
    """
    found: list[str] = []
    for field in SERVED_FIELDS:
        if field.name not in doc:
            if not field.optional:
                found.append(f"{field.name} is missing")
            continue
        problem = _problem(field, doc[field.name])
        if problem:
            found.append(problem)
    return found
