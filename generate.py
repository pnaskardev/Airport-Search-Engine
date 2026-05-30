import json
import os
import time
import uuid
import pandas as pd
import boto3
from botocore.config import Config

from dotenv import load_dotenv
load_dotenv()

# ==========================================
# CONFIGURATION
# ==========================================

AWS_REGION = "ap-south-1"

AGENT_ID = os.getenv("AGENT_ID", "YOUR_AGENT_ID")
AGENT_ALIAS_ID = os.getenv("AGENT_ALIAS_ID", "YOUR_AGENT_ALIAS_ID")
AWS_ACCESS_KEY_ID = os.getenv("AWS_ACCESS_KEY_ID")
AWS_SECRET_ACCESS_KEY = os.getenv("AWS_SECRET_KEY_ID")

INPUT_FILE = "airports.csv"
OUTPUT_FILE = "airports.json"

# ==========================================
# BEDROCK CLIENT
# ==========================================

bedrock_agent_runtime = boto3.client(
    "bedrock-agent-runtime",
    region_name=AWS_REGION,
    aws_access_key_id=AWS_ACCESS_KEY_ID,
    aws_secret_access_key=AWS_SECRET_ACCESS_KEY,
    config=Config(
        retries={
            "max_attempts": 5,
            "mode": "adaptive"
        }
    )
)

# ==========================================
# DEFENSIVE JSON PARSER
# ==========================================

def extract_json(raw: str) -> dict:
    """
    Robustly extract a JSON object from a raw string that may contain
    markdown code fences, preamble text, or escape sequences.
    """
    if not raw or not raw.strip():
        raise ValueError("Empty response from agent")

    # Unescape if the entire response is a Python-escaped string literal
    # e.g. 'I\'ll normalize...\n{\n  "IATA_CODE": ...\n}'
    if raw.startswith("'") and raw.endswith("'"):
        try:
            raw = raw[1:-1].encode("utf-8").decode("unicode_escape")
        except Exception:
            pass

    # Strip markdown code fences (```json ... ``` or ``` ... ```)
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.split("```", 2)[-1] if raw.count("```") >= 2 else raw
        raw = raw.lstrip("json").lstrip("\n")
        if raw.endswith("```"):
            raw = raw[: raw.rfind("```")]
        raw = raw.strip()

    # Find the first { and last } to isolate the JSON object,
    # ignoring any surrounding preamble or postamble text
    start = raw.find("{")
    end = raw.rfind("}")

    if start == -1 or end == -1:
        raise ValueError(f"No JSON object found in response: {repr(raw[:200])}")

    json_str = raw[start : end + 1]

    try:
        return json.loads(json_str)
    except json.JSONDecodeError as e:
        raise ValueError(f"JSON parse error: {e} | Extracted: {repr(json_str[:200])}")


# ==========================================
# LOAD DATA
# ==========================================

print("Loading airport data...")

df = pd.read_csv(INPUT_FILE)

filtered_df = df[
    (df["type"].isin(["large_airport","medium_airport"])) &
    (df["scheduled_service"] == "yes") &
    (df["iata_code"].notna()) &
    (df["iata_code"] != "") &
    (df["home_link"].notna()) &
    (df["home_link"] != "")
]

filtered_df = filtered_df.fillna("NaN")

records = filtered_df.to_dict(orient="records")

print(f"Found {len(records)} airports")

# ==========================================
# ENRICH AIRPORTS USING AGENT
# ==========================================

output_records = []
failed_records = []

for idx, airport in enumerate(records, start=1):

    iata = airport.get("iata_code", "UNKNOWN")
    print(f"[{idx}/{len(records)}] Processing {iata}")

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
        response = bedrock_agent_runtime.invoke_agent(
            agentId=AGENT_ID,
            agentAliasId=AGENT_ALIAS_ID,
            sessionId=str(uuid.uuid4()),
            inputText=json.dumps(payload, ensure_ascii=False),
        )

        output_text = ""
        for event in response["completion"]:
            if "chunk" in event:
                output_text += event["chunk"]["bytes"].decode("utf-8")

        print("RAW RESPONSE:")
        print(repr(output_text))

        # Defensively parse — handles fences, preamble, escape sequences
        generated_data = extract_json(output_text)

        airport.update(generated_data)

        # Build _geo from original CSV coordinates
        lat = airport.get("latitude_deg")
        lng = airport.get("longitude_deg")

        if lat != "NaN" and lng != "NaN":
            airport["_geo"] = {
                "lat": float(lat),
                "lng": float(lng)
            }

        output_records.append(airport)

        with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
            json.dump(output_records, f, ensure_ascii=False, indent=2)

        print(f"Saved {iata}")

    except ValueError as e:
        # JSON parsing failed — log and continue
        print(f"Parse error for {iata}: {e}")
        failed_records.append({"iata_code": iata, "reason": str(e)})

    except Exception as e:
        print(f"Failed for {iata}: {str(e)}")
        failed_records.append({"iata_code": iata, "reason": str(e)})

    time.sleep(30)

# ==========================================
# SUMMARY
# ==========================================

print(f"\nDone. {len(output_records)} saved, {len(failed_records)} failed.")

if failed_records:
    print("\nFailed airports:")
    for f in failed_records:
        print(f"  {f['iata_code']}: {f['reason']}")