"""
Airport enrichment pipeline.

Reads raw OurAirports CSV, filters to commercial airports,
enriches each record via AWS Bedrock Agent (aliases, translations,
popularity score, region name), and writes a JSON file ready
to seed into Meilisearch.

Usage:
    python enrich.py
    python enrich.py --input airports_raw.csv --output airports_enriched.json
    python enrich.py --dry-run --limit 5   # smoke-test without Bedrock calls
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import boto3
import pandas as pd
from botocore.config import Config
from dotenv import load_dotenv

load_dotenv()

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
logger = logging.getLogger("enrichment")


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
@dataclass
class PipelineConfig:
    input_file: Path = Path("airports_raw.csv")
    output_file: Path = Path("airports_enriched.json")
    failed_file: Path = Path("airports_failed.jsonl")  # JSONL for easy appending
    aws_region: str = "ap-south-1"
    agent_id: str = field(default_factory=lambda: os.environ["AGENT_ID"])
    agent_alias_id: str = field(default_factory=lambda: os.environ["AGENT_ALIAS_ID"])
    aws_access_key_id: str | None = field(
        default_factory=lambda: os.getenv("AWS_ACCESS_KEY_ID")
    )
    aws_secret_access_key: str | None = field(
        default_factory=lambda: os.getenv("AWS_SECRET_KEY_ID")
    )
    # Rate limiting: Bedrock has per-second and per-minute quotas.
    # 30 s is safe for the free tier; drop to 2-3 s if on provisioned throughput.
    sleep_between_requests: float = 30.0
    dry_run: bool = False
    limit: int | None = None  # process only N airports; useful for smoke tests


# ---------------------------------------------------------------------------
# Bedrock client factory
# ---------------------------------------------------------------------------
def build_bedrock_client(cfg: PipelineConfig):
    return boto3.client(
        "bedrock-agent-runtime",
        region_name=cfg.aws_region,
        aws_access_key_id=cfg.aws_access_key_id,
        aws_secret_access_key=cfg.aws_secret_access_key,
        config=Config(retries={"max_attempts": 5, "mode": "adaptive"}),
    )


# ---------------------------------------------------------------------------
# JSON extraction
# ---------------------------------------------------------------------------
def extract_json(raw: str) -> dict[str, Any]:
    """
    Robustly extract a JSON object from a raw LLM response.

    Handles:
    - Markdown code fences (```json ... ```)
    - Preamble / postamble prose
    - Python-escaped string literals
    """
    if not raw or not raw.strip():
        raise ValueError("Empty response from agent")

    text = raw.strip()

    # Unwrap Python-escaped string literal, e.g. "'I\\'ll help...\\n{...}'"
    if text.startswith("'") and text.endswith("'"):
        try:
            text = text[1:-1].encode("utf-8").decode("unicode_escape")
        except Exception:
            pass  # Not a Python literal — proceed

    # Strip markdown code fences
    if text.startswith("```"):
        parts = text.split("```")
        # parts[1] is the fenced block; strip optional language tag
        text = parts[1].lstrip("json").strip() if len(parts) >= 2 else text

    # Isolate the outermost JSON object, ignoring surrounding prose
    start = text.find("{")
    end = text.rfind("}")

    if start == -1 or end == -1 or end < start:
        raise ValueError(f"No JSON object in response: {raw[:200]!r}")

    json_str = text[start : end + 1]

    try:
        return json.loads(json_str)
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"JSON parse error ({exc}): {json_str[:200]!r}"
        ) from exc


# ---------------------------------------------------------------------------
# Airport filtering
# ---------------------------------------------------------------------------
def filter_commercial_airports(df: pd.DataFrame) -> pd.DataFrame:
    """
    Keep only airports that are:
    - large or medium (eliminates heliports, seaplanes, military strips)
    - have scheduled commercial service
    - have a valid IATA code (required for flight search)
    - have a home link (strong proxy for active commercial operation)
    """
    mask = (
        df["type"].isin(["large_airport", "medium_airport"])
        & (df["scheduled_service"] == "yes")
        & df["iata_code"].notna()
        & (df["iata_code"].str.strip() != "")
        & df["home_link"].notna()
        & (df["home_link"].str.strip() != "")
    )
    result = df[mask].copy()
    logger.info("Filtered %d → %d commercial airports", len(df), len(result))
    return result


# ---------------------------------------------------------------------------
# Bedrock invocation
# ---------------------------------------------------------------------------
def invoke_agent(
    client,
    cfg: PipelineConfig,
    payload: dict[str, Any],
) -> str:
    """
    Invoke the Bedrock agent with a JSON payload.
    Returns the concatenated text chunks from the streaming response.
    """
    response = client.invoke_agent(
        agentId=cfg.agent_id,
        agentAliasId=cfg.agent_alias_id,
        sessionId=str(uuid.uuid4()),  # fresh session per airport; no cross-contamination
        inputText=json.dumps(payload, ensure_ascii=False),
    )

    chunks: list[str] = []
    for event in response["completion"]:
        if "chunk" in event:
            chunks.append(event["chunk"]["bytes"].decode("utf-8"))

    return "".join(chunks)


# ---------------------------------------------------------------------------
# Record builder
# ---------------------------------------------------------------------------
def build_output_record(
    airport: dict[str, Any],
    enriched: dict[str, Any],
) -> dict[str, Any]:
    """
    Merge raw airport row with Bedrock-enriched fields.
    Adds _geo for Meilisearch geosearch support.
    """
    record = {**airport, **enriched}

    lat = airport.get("latitude_deg")
    lng = airport.get("longitude_deg")

    if lat not in (None, "NaN", "") and lng not in (None, "NaN", ""):
        record["_geo"] = {"lat": float(lat), "lng": float(lng)}

    return record


# ---------------------------------------------------------------------------
# Failure logging
# Senior rationale: JSONL (one JSON object per line) is append-friendly.
# You can re-process only failed airports without touching the main output.
# ---------------------------------------------------------------------------
def log_failure(failed_file: Path, iata: str, reason: str) -> None:
    entry = json.dumps({"iata_code": iata, "reason": reason}, ensure_ascii=False)
    with failed_file.open("a", encoding="utf-8") as f:
        f.write(entry + "\n")


# ---------------------------------------------------------------------------
# Checkpoint writer
# Senior rationale: write after every successful record.
# If the process dies at record 847, you don't lose 846 records.
# ---------------------------------------------------------------------------
def checkpoint(output_file: Path, records: list[dict[str, Any]]) -> None:
    output_file.write_text(
        json.dumps(records, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# Pipeline entrypoint
# Senior rationale: all the I/O and orchestration lives here.
# Business logic (filter, parse, build) is in pure functions above.
# ---------------------------------------------------------------------------
def run(cfg: PipelineConfig) -> None:
    logger.info("Starting enrichment pipeline (dry_run=%s)", cfg.dry_run)

    # --- Load ---
    raw_df = pd.read_csv(cfg.input_file)
    filtered_df = filter_commercial_airports(raw_df).fillna("NaN")
    records = filtered_df.to_dict(orient="records")

    if cfg.limit:
        records = records[: cfg.limit]
        logger.info("Limiting to %d airports (--limit flag)", cfg.limit)

    # --- Bedrock client (skip in dry-run) ---
    client = None if cfg.dry_run else build_bedrock_client(cfg)

    output_records: list[dict[str, Any]] = []
    failed_count = 0

    for idx, airport in enumerate(records, start=1):
        iata = airport.get("iata_code", "UNKNOWN")
        log_ctx = f"[{idx}/{len(records)}] {iata}"

        logger.info("%s — enriching", log_ctx)

        if cfg.dry_run:
            # Emit a stub record so you can validate the pipeline shape
            # without spending Bedrock quota.
            output_records.append(build_output_record(airport, {"_dry_run": True}))
            continue

        payload = {
            "iata_code": iata,
            "icao_code": airport.get("ident"),
            "airport_name": airport.get("name"),
            "municipality": airport.get("municipality"),
            "country": airport.get("iso_country"),
            "region": airport.get("iso_region"),
            "latitude": airport.get("latitude_deg"),
            "longitude": airport.get("longitude_deg"),
        }

        try:
            raw_response = invoke_agent(client, cfg, payload)
            logger.debug("%s — raw response: %r", log_ctx, raw_response[:300])

            enriched = extract_json(raw_response)
            record = build_output_record(airport, enriched)
            output_records.append(record)

            checkpoint(cfg.output_file, output_records)
            logger.info("%s — saved (%d total)", log_ctx, len(output_records))

        except ValueError as exc:
            # JSON parsing failed — log and continue.
            # ValueError is only raised by extract_json; Bedrock errors
            # bubble up as Exception and are caught below.
            logger.warning("%s — parse error: %s", log_ctx, exc)
            log_failure(cfg.failed_file, iata, str(exc))
            failed_count += 1

        except Exception as exc:  # noqa: BLE001
            # Bedrock throttling, network errors, etc.
            logger.error("%s — unexpected error: %s", log_ctx, exc, exc_info=True)
            log_failure(cfg.failed_file, iata, str(exc))
            failed_count += 1

        time.sleep(cfg.sleep_between_requests)

    # --- Summary ---
    logger.info(
        "Pipeline complete. saved=%d failed=%d",
        len(output_records),
        failed_count,
    )
    if failed_count:
        logger.info("Failed airports logged to %s", cfg.failed_file)


# ---------------------------------------------------------------------------
# CLI
# Senior rationale: argparse makes the script self-documenting and lets
# CI/CD override defaults without editing source.
# ---------------------------------------------------------------------------
def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Enrich airport records via AWS Bedrock Agent."
    )
    parser.add_argument("--input", default="airports_raw.csv", help="Raw CSV path")
    parser.add_argument(
        "--output", default="airports_enriched.json", help="Enriched JSON output path"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run pipeline without making Bedrock calls (validates shape)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Process only N airports (useful for smoke tests)",
    )
    parser.add_argument(
        "--sleep",
        type=float,
        default=30.0,
        help="Seconds to sleep between Bedrock requests (default: 30)",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    config = PipelineConfig(
        input_file=Path(args.input),
        output_file=Path(args.output),
        dry_run=args.dry_run,
        limit=args.limit,
        sleep_between_requests=args.sleep,
    )
    run(config)