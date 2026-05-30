import pandas as pd
import json

# Load airport dataset
df = pd.read_csv("airports.csv")

# Filter airports
filtered_df = df[
    (df["type"].isin(["large_airport", "medium_airport"])) &
    (df["scheduled_service"] == "yes") &
    (df["iata_code"].notna()) &
    (df["iata_code"] != "") &
    (df["home_link"].notna()) &
    (df["home_link"] != "")
]

# Replace all NaN values with the string "NaN"
filtered_df = filtered_df.fillna("NaN")

# Convert dataframe to list of dictionaries
records = filtered_df.to_dict(orient="records")

# Transform lat/lng into _geo field
for item in records:
    lat = (
        item.pop("LAT", None)
        if "LAT" in item
        else item.pop("latitude_deg", None)
    )

    lng = (
        item.pop("LONG", None)
        if "LONG" in item
        else item.pop("longitude_deg", None)
    )

    # Don't create _geo if coordinates are missing
    if lat != "NaN" and lng != "NaN":
        item["_geo"] = {
            "lat": float(lat),
            "lng": float(lng)
        }

# Save final JSON
with open("airports.json", "w", encoding="utf-8") as f:
    json.dump(records, f, ensure_ascii=False, indent=2)

print(f"Saved {len(records)} airports to airports.json")


import boto3
from botocore.config import Config

AGENT_ID = "YOUR_AGENT_ID"
AGENT_ALIAS_ID = "YOUR_ALIAS_ID"
AWS_REGION = "us-east-1"

bedrock_agent_runtime = boto3.client(
    "bedrock-agent-runtime",
    region_name=AWS_REGION,
    config=Config(retries={"max_attempts": 3})
)
