"""
Build the index-ready airport documents.

Takes the raw OurAirports CSVs plus whatever the Bedrock enrichment produced
and merges them into one JSON file for seed.py.

Three things this fixes that the enrichment pass got wrong:

1. Coverage. The original filter required a non-empty `home_link`, which is a
   proxy for "a volunteer added a URL to OurAirports", not for commercial
   service. It removed 60% of airports, including ATL, FCO, TUL, CAI and KUL,
   and it removed them unevenly: China kept 25 of 223, Cuba and Mozambique
   kept none. Dropping that condition restores them. Airports the LLM never
   saw keep their raw names and still become findable by city, name and IATA
   code - they only lose aliases and translations.

2. REGION_NAME. The LLM silently returned nothing for this field on all 1,258
   records, so state and province searches ("Hawaii", "Ontario") matched
   nothing. It is a lookup, not world knowledge: regions.csv maps iso_region
   directly. Same for COUNTRY_NAME via countries.csv.

3. Three records where the agent asked a clarifying question instead of
   returning JSON. They were indexed anyway, with no city and no country.

Usage:
    python backfill.py
    python backfill.py --out ../enriched/airports_indexed.json
"""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

from airport_contract import violations

HERE = Path(__file__).resolve().parent
RAW = HERE.parent / "raw"
ENRICHED = HERE.parent / "enriched"

# ponytail: a flat prior by airport size, used only for airports the LLM never
# scored. Replace with real passenger volume (Wikipedia/ACI) when ranking
# quality matters more than coverage.
DEFAULT_POPULARITY = {"large_airport": 700, "medium_airport": 400}


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8") as f:
        return list(csv.DictReader(f))


def commercial_airports(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    """Airports a passenger can actually book a scheduled flight from.

    Deliberately does NOT filter on home_link - see module docstring.
    """
    return [
        r
        for r in rows
        if r["type"] in ("large_airport", "medium_airport")
        and r["scheduled_service"] == "yes"
        and r["iata_code"].strip()
    ]


def flatten_translations(translations: object) -> list[str]:
    """Meilisearch wants one flat list of searchable strings.

    enrich.py v2 returns an object keyed by language, because a flat list is exactly
    what hid the missing Korean in v1 - you cannot count per-language coverage in a
    list of 22 unlabelled strings. The keys stay in the enriched file for auditing and
    are flattened only here, at the index boundary. v1's flat list still loads.
    """
    if isinstance(translations, dict):
        return [s for names in translations.values() for s in names]
    if isinstance(translations, list):
        return translations
    return []


def build_document(
    raw: dict[str, str],
    enriched: dict | None,
    regions: dict[str, str],
    countries: dict[str, str],
) -> dict:
    """Merge one raw row with its enrichment, if any.

    Raw fields are the floor: every airport is findable by name, city and IATA
    code even with no enrichment at all. Enrichment adds aliases, translations
    and a popularity score on top.
    """
    enriched = enriched or {}

    # The agent returned a question instead of JSON for a handful of airports.
    # Those records carry no usable city or country, so ignore the enrichment.
    if "question" in enriched:
        enriched = {}

    doc = {
        "id": int(raw["id"]),
        "iata_code": raw["iata_code"].strip(),
        "icao_code": raw["icao_code"].strip(),
        "name": raw["name"],
        "CITY_NAME": enriched.get("CITY_NAME") or raw["municipality"] or raw["name"],
        "COUNTRY_NAME": enriched.get("COUNTRY_NAME")
        or countries.get(raw["iso_country"], raw["iso_country"]),
        "REGION_NAME": regions.get(raw["iso_region"], ""),
        "TYPE": "LARGE" if raw["type"] == "large_airport" else "MEDIUM",
        "ALIASES": enriched.get("ALIASES") or [],
        "TRANSLATIONS": flatten_translations(enriched.get("TRANSLATIONS")),
        # OurAirports' own alternate-name column, which the enrichment pass
        # discarded. It is hand-curated and free, and it already carries the
        # things the LLM was asked to invent: native scripts (NRT has 東京,
        # PEK has 北京) and multi-airport city codes (TYO, BJS, LON).
        "KEYWORDS": [k.strip() for k in raw["keywords"].split(",") if k.strip()],
        "POPULARITY_SCORE": enriched.get("POPULARITY_SCORE")
        or DEFAULT_POPULARITY[raw["type"]],
        "ENRICHED": bool(enriched),
    }

    try:
        doc["_geo"] = {
            "lat": float(raw["latitude_deg"]),
            "lng": float(raw["longitude_deg"]),
        }
    except (TypeError, ValueError):
        pass  # Meilisearch only needs _geo for geosearch; skip it if unparseable.

    return doc


def build(
    airports_csv: Path, enriched_json: Path, regions_csv: Path, countries_csv: Path
) -> tuple[list[dict], list[dict]]:
    """Index-ready documents, plus the rows the Contract rejected and why."""
    regions = {r["code"]: r["name"] for r in read_csv(regions_csv)}
    countries = {c["code"]: c["name"] for c in read_csv(countries_csv)}

    enriched_by_iata = {
        r["iata_code"]: r for r in json.loads(enriched_json.read_text(encoding="utf-8"))
    }

    rows = commercial_airports(read_csv(airports_csv))
    docs: list[dict] = []
    rejected: list[dict] = []
    for r in rows:
        doc = build_document(
            r, enriched_by_iata.get(r["iata_code"].strip()), regions, countries
        )
        problems = violations(doc)
        if problems:
            rejected.append({"iata_code": doc["iata_code"], "violations": problems})
        else:
            docs.append(doc)
    return docs, rejected


def report(docs: list[dict], rejected: list[dict]) -> None:
    enriched = sum(1 for d in docs if d["ENRICHED"])
    regions = sum(1 for d in docs if d["REGION_NAME"])
    print(f"documents:      {len(docs)}")
    print(f"rejected:       {len(rejected)}")
    print(f"enriched:       {enriched} ({enriched / len(docs):.0%})")
    print(f"REGION_NAME:    {regions} ({regions / len(docs):.0%})")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--airports", type=Path, default=RAW / "airports_raw.csv")
    parser.add_argument("--regions", type=Path, default=RAW / "regions.csv")
    parser.add_argument("--countries", type=Path, default=RAW / "countries.csv")
    parser.add_argument(
        "--enriched", type=Path, default=ENRICHED / "airport_enriched.json"
    )
    parser.add_argument("--out", type=Path, default=ENRICHED / "airports_indexed.json")
    parser.add_argument(
        "--rejected", type=Path, default=ENRICHED / "airports_rejected.json"
    )
    args = parser.parse_args()

    docs, rejected = build(args.airports, args.enriched, args.regions, args.countries)
    args.out.write_text(
        json.dumps(docs, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    args.rejected.write_text(
        json.dumps(rejected, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    report(docs, rejected)
    print(f"wrote {args.out}")
    if rejected:
        print(f"wrote {args.rejected} - fix these before they reach the index")


if __name__ == "__main__":
    main()
