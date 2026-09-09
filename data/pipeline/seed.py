"""
Seed Meilisearch: index settings first, then documents.

The settings are the search engine. Without them Meilisearch comes up on
defaults - every field searchable in arbitrary order, no popularity signal -
so a fresh deploy silently ranks differently from a tuned one. Keeping them
here means the index is reproducible from the repo instead of from whatever
was typed into a running instance.

Usage:
    python seed.py
    MEILISEARCH_URL=http://localhost:7700 MEILI_MASTER_KEY=masterKey python seed.py
"""

from __future__ import annotations

import argparse
import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEFAULT_DOCS = HERE.parent / "enriched" / "airports_indexed.json"

# Order matters: Meilisearch's `attribute` ranking rule prefers matches in
# fields listed earlier. IATA code first means "TUL" resolves to the airport
# whose code is TUL, not to an airport with "Tul" somewhere in its name.
SEARCHABLE_ATTRIBUTES = [
    "iata_code",
    "CITY_NAME",
    "ALIASES",
    "KEYWORDS",
    "name",
    "TRANSLATIONS",
    "REGION_NAME",
    "COUNTRY_NAME",
    "icao_code",
]

# Order decided by eval.py, not by taste. Two rules fight over every query:
#
#   exactness  - did the query match a whole word, or only a prefix? This is
#                what keeps "Florida" (the US state) above "La Florida" in
#                Chile, and "Bali" above "Balikpapan".
#   attribute  - which field matched? Meilisearch scores an earlier field
#                higher, so a match in ALIASES outranks one in REGION_NAME.
#
# `attribute` above popularity is actively harmful here, because enrichment
# coverage is uneven: Lakeland has "Florida" in its LLM-written aliases and
# Tampa does not, so Lakeland matched in a stronger field and buried TPA at
# rank 18. Popularity above `attribute` fixes that; exactness stays above
# popularity so a busy fuzzy match still cannot beat a perfect one.
RANKING_RULES = [
    "words",
    "typo",
    "proximity",
    "exactness",
    "POPULARITY_SCORE:desc",
    "attribute",
    "sort",
]

SETTINGS = {
    "searchableAttributes": SEARCHABLE_ATTRIBUTES,
    "rankingRules": RANKING_RULES,
    "sortableAttributes": ["POPULARITY_SCORE"],
    "filterableAttributes": ["COUNTRY_NAME", "REGION_NAME", "TYPE"],
    # Typo tolerance starts at 5 characters. Below that it does more harm than
    # good: "Bali" is one edit from "Bari" and "Bali"kpapan, and a 3-letter
    # IATA code must never fuzzy-match another one. "Londn" is 5, so the
    # brief's typo case still works.
    "typoTolerance": {"minWordSizeForTypos": {"oneTypo": 5, "twoTypos": 9}},
}


def request(method: str, url: str, key: str, body: object = None) -> dict:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", f"Bearer {key}")
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            return json.loads(resp.read() or "{}")
    except urllib.error.HTTPError as exc:
        raise SystemExit(f"{method} {url} failed: {exc.code} {exc.read().decode()}")
    except urllib.error.URLError as exc:
        raise SystemExit(f"cannot reach Meilisearch at {url}: {exc.reason}")


def wait_for_task(base: str, key: str, task_uid: int, label: str) -> None:
    """Meilisearch writes are async - a 202 means queued, not applied."""
    while True:
        task = request("GET", f"{base}/tasks/{task_uid}", key)
        status = task.get("status")
        if status == "succeeded":
            print(f"  {label}: ok")
            return
        if status == "failed":
            raise SystemExit(f"  {label}: failed - {task.get('error')}")
        time.sleep(0.5)


def wait_for_meilisearch(base: str, timeout: float = 120.0) -> None:
    """Meilisearch takes a few seconds to open its port on a cold container."""
    deadline = time.monotonic() + timeout
    while True:
        try:
            with urllib.request.urlopen(f"{base}/health", timeout=5):
                return
        except (urllib.error.URLError, TimeoutError) as exc:
            if time.monotonic() > deadline:
                raise SystemExit(f"Meilisearch never came up at {base}: {exc}")
            time.sleep(2)


def seed(base: str, key: str, index: str, docs_path: Path) -> None:
    docs = json.loads(docs_path.read_text(encoding="utf-8"))
    print(f"seeding {len(docs)} documents into '{index}' at {base}")
    wait_for_meilisearch(base)

    # No explicit index creation: Meilisearch creates it on first write and
    # infers `id` as the primary key, so a re-seed and a cold start take the
    # same path.
    task = request("PATCH", f"{base}/indexes/{index}/settings", key, SETTINGS)
    wait_for_task(base, key, task["taskUid"], "settings")

    task = request("POST", f"{base}/indexes/{index}/documents", key, docs)
    wait_for_task(base, key, task["taskUid"], "documents")

    stats = request("GET", f"{base}/indexes/{index}/stats", key)
    print(f"  indexed: {stats.get('numberOfDocuments')} documents")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--docs", type=Path, default=DEFAULT_DOCS)
    parser.add_argument(
        "--url", default=os.getenv("MEILISEARCH_URL", "http://localhost:7700")
    )
    parser.add_argument("--index", default=os.getenv("MEILISEARCH_INDEX", "airports"))
    args = parser.parse_args()

    key = os.getenv("MEILI_MASTER_KEY")
    if not key:
        raise SystemExit("MEILI_MASTER_KEY is not set")

    seed(args.url.rstrip("/"), key, args.index, args.docs)


if __name__ == "__main__":
    main()
