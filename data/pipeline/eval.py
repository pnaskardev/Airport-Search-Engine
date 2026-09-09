"""
Search eval harness.

Every query below is a real failure case from the brief. Each one asserts two
things: the airports that must appear near the top, and the airports that must
NOT (the fuzzy-match traps - "Bali" must not pull in Balikpapan, "Florida"
must not pull in La Florida, Chile).

The same cases also run against a naive substring matcher over the airport
name field. That is the baseline the real engine has to beat, and printing
both numbers is the only honest way to claim it does.

Run it against a seeded stack:
    python eval.py
    python eval.py --url https://your-host    # any host serving /api/search

Exit code is 0 only when every case passes, so CI can gate on it.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEFAULT_DOCS = HERE.parent / "enriched" / "airports_indexed.json"


@dataclass
class Case:
    query: str
    expect: list[str] = field(default_factory=list)
    forbid: list[str] = field(default_factory=list)
    top: int = 10
    why: str = ""


CASES = [
    # IATA codes, both directions. The brief's "TUL returns nothing but Tulsa
    # works" asymmetry.
    Case("JFK", expect=["JFK"], top=1, why="exact IATA wins outright"),
    Case("TUL", expect=["TUL"], top=1, why="IATA code, not a fuzzy name match"),
    Case("Tulsa", expect=["TUL"], top=1, why="city name resolves to its airport"),
    Case("CTA", expect=["CTA"], top=1, why="same asymmetry, Catania"),
    Case("Catania", expect=["CTA"], top=1),
    # City name over municipality name from the raw dataset.
    Case("Brussels", expect=["BRU"], top=3, why="not just 'Zaventem'"),
    Case("Manama", expect=["BAH"], top=3, why="city-to-airport mapping"),
    Case("Bengaluru", expect=["BLR"], top=3),
    # Region and state search. Needs REGION_NAME, which the LLM never filled.
    Case("Hawaii", expect=["HNL", "OGG", "KOA", "LIH"], why="US state"),
    Case("Ontario", expect=["YYZ"], why="Canadian province"),
    Case(
        "Florida",
        expect=["MCO", "MIA", "TPA"],
        forbid=["LSC", "ZCO"],
        why="US state beats 'La Florida' in Chile",
    ),
    # Tourism alias that is not the municipality, and its fuzzy trap.
    Case("Bali", expect=["DPS"], forbid=["BPN"], top=5, why="not Balikpapan"),
    Case("Goa", expect=["GOI"], top=5),
    # Typo tolerance, deliberately not magical.
    Case("Londn", expect=["LHR"], top=5, why="one edit from London"),
    # Multi-airport city code, and the ambiguous city name.
    Case(
        "LON",
        expect=["LHR", "LGW", "STN", "LCY", "LTN"],
        why="city code, all five London airports",
    ),
    Case(
        "London",
        expect=["LHR", "YXU"],
        why="London UK and London, Ontario both surface",
    ),
    # Accents, endonyms, exonyms.
    Case("Sao Paulo", expect=["GRU"], top=5, why="accent dropped"),
    Case("São Paulo", expect=["GRU"], top=5, why="accent kept, same result"),
    Case("Roma", expect=["FCO"], top=5, why="endonym"),
    Case("Rome", expect=["FCO"], top=5, why="exonym"),
    Case("München", expect=["MUC"], top=5),
    Case("Munich", expect=["MUC"], top=5),
    # Non-Latin scripts.
    Case("東京", expect=["HND", "NRT"], why="Japanese"),
    Case("北京", expect=["PEK"], why="Chinese"),
    Case("서울", expect=["ICN"], why="Korean - never generated, expected to fail"),
    Case("دبي", expect=["DXB"], top=5, why="Arabic"),
    # Coverage regressions: these were dropped by the home_link filter.
    Case("Atlanta", expect=["ATL"], top=3, why="busiest airport on earth"),
    Case("Cairo", expect=["CAI"], top=3),
    Case("Kuala Lumpur", expect=["KUL"], top=3),
]


def api_search(base: str, query: str) -> list[str]:
    url = f"{base}/api/search?q={urllib.parse.quote(query)}"
    try:
        with urllib.request.urlopen(url, timeout=15) as resp:
            body = json.loads(resp.read())
    except (urllib.error.URLError, TimeoutError) as exc:
        raise SystemExit(f"cannot reach the search API at {base}: {exc}")
    return [hit["iata_code"] for hit in body.get("hits", [])]


def naive_search(docs: list[dict], query: str, limit: int = 10) -> list[str]:
    """The baseline: case-insensitive substring over the airport name only."""
    q = query.casefold()
    return [d["iata_code"] for d in docs if q in d["name"].casefold()][:limit]


def check(case: Case, hits: list[str]) -> str:
    """Return an empty string when the case passes, else the reason it failed."""
    top = hits[: case.top]
    missing = [code for code in case.expect if code not in top]
    if missing:
        return f"missing {','.join(missing)} from top {case.top}"
    present = [code for code in case.forbid if code in top]
    if present:
        return f"should not surface {','.join(present)}"
    return ""


def run(cases: list[Case], search, label: str) -> int:
    print(f"\n{label}")
    print("-" * 72)
    failures = 0
    for case in cases:
        hits = search(case.query)
        reason = check(case, hits)
        if reason:
            failures += 1
            print(f"FAIL  {case.query:<14} {reason}")
            print(f"      {'got:':<14} {', '.join(hits[:8]) or '(nothing)'}")
        else:
            print(f"pass  {case.query:<14} {', '.join(hits[: case.top])}")
    passed = len(cases) - failures
    print("-" * 72)
    print(f"{label}: {passed}/{len(cases)} passed")
    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://localhost:8080")
    parser.add_argument("--docs", type=Path, default=DEFAULT_DOCS)
    parser.add_argument(
        "--baseline-only",
        action="store_true",
        help="Run only the naive matcher; does not need a running API.",
    )
    args = parser.parse_args()

    docs = json.loads(args.docs.read_text(encoding="utf-8"))
    run(CASES, lambda q: naive_search(docs, q), "naive substring over name (baseline)")

    if args.baseline_only:
        return 0

    base = args.url.rstrip("/")
    return 1 if run(CASES, lambda q: api_search(base, q), f"search API ({base})") else 0


if __name__ == "__main__":
    sys.exit(main())
