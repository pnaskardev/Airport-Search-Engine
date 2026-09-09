"""
Airport enrichment via the Claude API, as one batch job.

Adds the things a plain text index cannot derive from OurAirports: informal aliases,
tourist-region names, and city/airport names in 12 languages. Everything derivable
(region, country, geo) is done deterministically in backfill.py instead.

The prompt lives in prompts/enrich_v2.md. Read it before changing the schema below —
each field there has a bug behind it.

Usage:
    python enrich.py --estimate           # token/cost estimate, no API call
    python enrich.py                      # submit a batch, poll, write results
    python enrich.py --batch-id msgbatch_01...   # resume polling an existing batch

Needs ANTHROPIC_API_KEY (or an `ant auth login` profile).
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import time
from pathlib import Path
from typing import Any

import anthropic
from anthropic.types.message_create_params import MessageCreateParamsNonStreaming
from anthropic.types.messages.batch_create_params import Request

from backfill import RAW, ENRICHED, commercial_airports, read_csv

HERE = Path(__file__).resolve().parent

MODEL = "claude-opus-5"
MAX_TOKENS = 2048

# 12 languages. Korean is here because v1 asked for 11 in prose, left Korean out, and
# nobody noticed until "서울" returned nothing. Every one is `required` in the schema
# below, so a missing language now fails validation instead of shipping as a hole.
LANGUAGES = {
    "en": "English",
    "zh": "Chinese",
    "ja": "Japanese",
    "ko": "Korean",
    "ar": "Arabic",
    "ru": "Russian",
    "hi": "Hindi",
    "bn": "Bengali",
    "es": "Spanish",
    "fr": "French",
    "pt": "Portuguese",
    "id": "Indonesian",
}

SYSTEM_PROMPT = (HERE / "prompts" / "enrich_v2.md").read_text(encoding="utf-8")

SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "city": {
            "type": "string",
            "description": "The city travellers associate with this airport, in English. "
            "The friendly name, not the dataset's municipality: Brussels, not Zaventem.",
        },
        "country": {"type": "string", "description": "Country name in English."},
        "aliases": {
            "type": "array",
            "items": {"type": "string"},
            "minItems": 3,
            "maxItems": 8,
            "description": "Other names a traveller might type, including tourist regions "
            "(DPS serves Bali) and informal names (Heathrow).",
        },
        "translations": {
            "type": "object",
            "properties": {
                code: {
                    "type": "array",
                    "items": {"type": "string"},
                    "minItems": 2,
                    "maxItems": 3,
                    "description": f"City and airport name in {name}, in its own script.",
                }
                for code, name in LANGUAGES.items()
            },
            "required": list(LANGUAGES),
            "additionalProperties": False,
        },
        "popularity": {
            "type": "integer",
            "minimum": 0,
            "maximum": 1000,
            "description": "How likely this airport is the one a traveller means.",
        },
    },
    "required": ["city", "country", "aliases", "translations", "popularity"],
    "additionalProperties": False,
}

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
logger = logging.getLogger("enrich")


def airport_payload(raw: dict[str, str]) -> str:
    """The raw row, trimmed to what the model needs to identify the airport."""
    return json.dumps(
        {
            "iata_code": raw["iata_code"].strip(),
            "icao_code": raw["icao_code"].strip(),
            "name": raw["name"],
            "municipality": raw["municipality"],
            "iso_country": raw["iso_country"],
            "iso_region": raw["iso_region"],
            "keywords": raw["keywords"],
        },
        ensure_ascii=False,
    )


def build_request(raw: dict[str, str]) -> Request:
    return Request(
        custom_id=raw["iata_code"].strip(),
        params=MessageCreateParamsNonStreaming(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            # The instruction block is byte-identical across every request, so it is
            # the cacheable prefix. The volatile airport row goes in the user message,
            # after the breakpoint.
            system=[
                {
                    "type": "text",
                    "text": SYSTEM_PROMPT,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            messages=[{"role": "user", "content": airport_payload(raw)}],
            output_config={"format": {"type": "json_schema", "schema": SCHEMA}},
        ),
    )


def estimate(client: anthropic.Anthropic, requests: list[Request]) -> None:
    """Price the job before spending anything on it."""
    sample = requests[0]["params"]
    counted = client.messages.count_tokens(
        model=MODEL, system=sample["system"], messages=sample["messages"]
    )
    n = len(requests)
    # Batch is 50% of standard. Opus 5 standard is $5/MTok in, $25/MTok out.
    in_cost = counted.input_tokens * n / 1e6 * 2.50
    out_cost = MAX_TOKENS * 0.6 * n / 1e6 * 12.50  # assume ~60% of the output cap
    logger.info("%d airports, ~%d input tokens each", n, counted.input_tokens)
    logger.info(
        "rough batch cost: $%.2f input + $%.2f output = $%.2f (before cache savings)",
        in_cost,
        out_cost,
        in_cost + out_cost,
    )


def submit(client: anthropic.Anthropic, requests: list[Request]) -> str:
    batch = client.messages.batches.create(requests=requests)
    logger.info("submitted batch %s (%d requests)", batch.id, len(requests))
    return batch.id


def wait(client: anthropic.Anthropic, batch_id: str) -> None:
    while True:
        batch = client.messages.batches.retrieve(batch_id)
        if batch.processing_status == "ended":
            logger.info(
                "batch ended: %d succeeded, %d errored",
                batch.request_counts.succeeded,
                batch.request_counts.errored,
            )
            return
        logger.info(
            "status=%s processing=%d succeeded=%d",
            batch.processing_status,
            batch.request_counts.processing,
            batch.request_counts.succeeded,
        )
        time.sleep(60)


def collect(client: anthropic.Anthropic, batch_id: str) -> dict[str, dict[str, Any]]:
    """Map IATA code to enrichment. Failures are logged, not raised - a batch of 3,000
    should not be thrown away because nine of them errored."""
    enriched: dict[str, dict[str, Any]] = {}
    failed: list[tuple[str, str]] = []

    for result in client.messages.batches.results(batch_id):
        if result.result.type != "succeeded":
            failed.append((result.custom_id, result.result.type))
            continue
        # output_config.format guarantees the first text block is schema-valid JSON.
        text = next(b.text for b in result.result.message.content if b.type == "text")
        enriched[result.custom_id] = json.loads(text)

    if failed:
        logger.warning("%d requests did not succeed", len(failed))
        (ENRICHED / "enrich_failed.json").write_text(
            json.dumps(failed, indent=2), encoding="utf-8"
        )

    return enriched


def to_record(iata: str, out: dict[str, Any]) -> dict[str, Any]:
    """Field names the rest of the pipeline already expects."""
    return {
        "iata_code": iata,
        "CITY_NAME": out["city"],
        "COUNTRY_NAME": out["country"],
        "ALIASES": out["aliases"],
        "TRANSLATIONS": out["translations"],
        "POPULARITY_SCORE": out["popularity"],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--airports", type=Path, default=RAW / "airports_raw.csv")
    parser.add_argument("--out", type=Path, default=ENRICHED / "airport_enriched.json")
    parser.add_argument("--limit", type=int, default=None, help="First N airports only")
    parser.add_argument("--batch-id", help="Resume polling an already-submitted batch")
    parser.add_argument(
        "--estimate", action="store_true", help="Print a cost estimate and exit"
    )
    args = parser.parse_args()

    rows = commercial_airports(read_csv(args.airports))
    if args.limit:
        rows = rows[: args.limit]
    requests = [build_request(r) for r in rows]

    client = anthropic.Anthropic()

    if args.estimate:
        estimate(client, requests)
        return

    batch_id = args.batch_id or submit(client, requests)
    # Written before polling: a crash here loses the wait, not the batch.
    (ENRICHED / "last_batch_id.txt").write_text(batch_id, encoding="utf-8")

    wait(client, batch_id)
    enriched = collect(client, batch_id)

    records = [to_record(iata, out) for iata, out in sorted(enriched.items())]
    args.out.write_text(
        json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    logger.info("wrote %d records to %s", len(records), args.out)
    logger.info("next: python backfill.py && python seed.py && python eval.py")


if __name__ == "__main__":
    main()
