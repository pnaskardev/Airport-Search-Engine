import json
import os
import time
import uuid
import pandas as pd
import boto3
from botocore.config import Config

# ==========================================
# CONFIGURATION
# ==========================================

AWS_REGION = "ap-south-1"

AGENT_ID = os.getenv("AGENT_ID", "YOUR_AGENT_ID")
AGENT_ALIAS_ID = os.getenv("AGENT_ALIAS_ID", "YOUR_AGENT_ALIAS_ID")

INPUT_FILE = "airports.csv"
OUTPUT_FILE = "airports.json"

# ==========================================
# BEDROCK CLIENT
# ==========================================

bedrock_agent_runtime = boto3.client(
    "bedrock-agent-runtime",
    region_name=AWS_REGION,
    aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID"),
    aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY"),
    config=Config(
        retries={
            "max_attempts": 5,
            "mode": "adaptive"
        }
    )
)

# ==========================================
# LOAD DATA
# ==========================================

print("Loading airport data...")

df = pd.read_csv(INPUT_FILE)

filtered_df = df[
    (df["type"].isin(["large_airport", "medium_airport"])) &
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

for idx, airport in enumerate(records, start=1):

    print(
        f"[{idx}/{len(records)}] Processing {airport.get('iata_code')}"
    )

    payload = {
        "iata_code": airport.get("iata_code"),
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
                output_text += (
                    event["chunk"]["bytes"]
                    .decode("utf-8")
                )

        generated_data = json.loads(output_text)

        airport.update(generated_data)

        # create _geo immediately
        lat = airport.get("latitude_deg")
        lng = airport.get("longitude_deg")

        if (
            lat != "NaN"
            and lng != "NaN"
        ):
            airport["_geo"] = {
                "lat": float(lat),
                "lng": float(lng)
            }

        generated_data = json.loads(output_text)

        airport.update(generated_data)

        output_records.append(airport)

        with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
            json.dump(
                output_records,
                f,
                ensure_ascii=False,
                indent=2
            )

        print(
            f"Saved {airport.get('iata_code')}"
        )

    except Exception as e:

        print(
            f"Failed for {airport.get('iata_code')} : {str(e)}"
        )

    time.sleep(30)
    


print(
    f"Successfully saved {len(records)} airports "
    f"to {OUTPUT_FILE}"
)