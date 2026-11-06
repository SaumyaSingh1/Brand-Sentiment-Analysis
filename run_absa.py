"""
Phase 2 - Aspect-Based Sentiment Analysis (ABSA) with Gemini 3.5 Flash.

Reads reviews.csv (1,140 reviews, 380 per brand), sends them to Gemini in
batches of 60, and writes one row per aspect mentioned to
enriched_beauty_sentiment.csv.

Free-tier safe:
  - 60 reviews per call (19 calls total), thinking disabled, 7 s pause between calls
  - auto-saves after every batch
  - resumable: re-run the script and it skips reviews already in the output file

Setup:
  Put your key in a .env file next to this script (never commit it):
    GEMINI_API_KEY=your-key-here
  Optionally switch model there too (default gemini-3.5-flash):
    GEMINI_MODEL=gemini-3.8-flash
  then run:
    python run_absa.py
"""

import json
import os
import sys
import time
from pathlib import Path
from typing import Literal

import httpx
import pandas as pd
from dotenv import load_dotenv
from google import genai
from google.genai import errors, types
from pydantic import BaseModel

INPUT_FILE = Path("reviews.csv")
OUTPUT_FILE = Path("enriched_beauty_sentiment.csv")
load_dotenv()

# gemini-2.5-flash is closed to new API users; 3.5 Flash was the stable Flash model when tested
MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.5-flash")
# Free tier allows only 20 requests/day per model, so 60 reviews per call keeps
# the full run (19 calls) inside one day's quota with a little room for retries
BATCH_SIZE = 60
PAUSE_SECONDS = 7
OVERLOAD_WAIT_SECONDS = 300  # 503/504 spikes last minutes, and retries may use daily quota
MAX_RETRIES = 3
REQUEST_TIMEOUT_MS = 120_000

OUTPUT_COLUMNS = ["Review_ID", "Brand", "Brand_Pillar", "Sub_Issue", "Sentiment_Score", "Churn_Risk"]
SENTIMENT_TO_SCORE = {"Positive": 1, "Neutral": 0, "Negative": -1}

Pillar = Literal[
    "Texture & Finish",
    "Packaging & Dispensing",
    "Skin Compatibility & Fragrance",
    "Sun Protection & Value",
    "Overall Experience",
]


class AspectRow(BaseModel):
    review_id: int
    brand_pillar: Pillar
    sub_issue: str
    sentiment: Literal["Positive", "Neutral", "Negative"]
    churn_risk: Literal["High", "Low"]


SYSTEM_PROMPT = """You are an FMCG consumer-insights analyst performing Aspect-Based Sentiment Analysis
on Indian e-commerce reviews of sunscreens. Reviews may be positive, neutral, negative, or mixed.
Do not assume any review is negative: judge each aspect only on what the reviewer actually says.

For EVERY review, return one row per distinct aspect the reviewer gives an opinion on.
Every review must produce at least one row. A review praising texture but complaining about
breakouts produces two rows with opposite sentiment.

brand_pillar (choose exactly one per row):
- "Texture & Finish": feel on skin, greasy/oily, sticky, heavy/lightweight, absorption, white cast,
  dewy/matte finish, pilling, works under makeup.
- "Packaging & Dispensing": pump, tube, cap, nozzle, leakage, product hard to dispense, seal or
  tamper issues, used/opened product received, damaged delivery, pack design.
- "Skin Compatibility & Fragrance": breakouts, acne, pimples, irritation, burning, rashes, allergy,
  eye stinging, darkening, suitability for a skin type, fragrance or smell.
- "Sun Protection & Value": SPF effectiveness, tanning, sunburn, protection claims, price,
  quantity for the price, value for money, authenticity.
- "Overall Experience": ONLY when the review gives a general opinion with no specific aspect
  (e.g. "Good product", "Waste of money", "Not satisfied").

sub_issue: a short 2-to-4 word Title Case label for the specific aspect. Reuse these labels
whenever one fits, so labels stay consistent across batches; create a new one only if none fit:
  Texture & Finish: "Greasy Oily Feel", "Lightweight Texture", "Sticky Residue", "White Cast Issue",
    "No White Cast", "Dewy Finish", "Matte Finish", "Quick Absorption", "Pilling Under Makeup"
  Packaging & Dispensing: "Faulty Pump Dispenser", "Leaking Packaging", "Used Or Tampered Product",
    "Damaged On Delivery", "Convenient Packaging"
  Skin Compatibility & Fragrance: "Causes Breakouts", "Skin Irritation Burning", "Allergic Reaction",
    "Eye Stinging", "Skin Darkening", "Suits Oily Skin", "Suits Sensitive Skin",
    "Unpleasant Fragrance", "Pleasant Or No Fragrance"
  Sun Protection & Value: "Ineffective Sun Protection", "Effective Sun Protection", "Overpriced",
    "Small Quantity For Price", "Good Value Price", "Suspected Fake Product"
  Overall Experience: "General Satisfaction", "General Dissatisfaction"

sentiment: "Positive", "Neutral", or "Negative" for that aspect only.

churn_risk:
- "High" if the reviewer signals they will stop using or not repurchase, returned the product,
  warns others not to buy, or reports an adverse skin reaction.
- "Low" otherwise. Positive aspects are always "Low".

Use the exact review_id given for each review. Some reviews are cut off mid-sentence; analyse only
the text present."""


def load_reviews() -> pd.DataFrame:
    df = pd.read_csv(INPUT_FILE, encoding="utf-8")
    df.insert(0, "Review_ID", range(1, len(df) + 1))
    return df


def load_existing_output() -> pd.DataFrame:
    if OUTPUT_FILE.exists():
        return pd.read_csv(OUTPUT_FILE, encoding="utf-8")
    return pd.DataFrame(columns=OUTPUT_COLUMNS)


def call_gemini(client: genai.Client, batch: pd.DataFrame) -> list[AspectRow]:
    payload = [
        {"review_id": int(row.Review_ID), "text": row.Review_Text}
        for row in batch.itertuples(index=False)
    ]
    config = types.GenerateContentConfig(
        system_instruction=SYSTEM_PROMPT,
        response_mime_type="application/json",
        response_schema=list[AspectRow],
        temperature=0,
        thinking_config=types.ThinkingConfig(thinking_budget=0),
        automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
    )

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            response = client.models.generate_content(
                model=MODEL,
                contents=json.dumps(payload, ensure_ascii=False),
                config=config,
            )
            if response.parsed is None:
                raise ValueError("Gemini returned no parseable JSON")
            return response.parsed
        except errors.APIError as e:
            if e.code == 429 and "PerDay" in str(e):
                # Daily quota is gone; retrying would only fail again
                raise RuntimeError("Daily free-tier quota used up. Re-run after it resets.") from e
            if e.code not in (429, 500, 503, 504) or attempt == MAX_RETRIES:
                raise
            wait = OVERLOAD_WAIT_SECONDS * attempt if e.code in (503, 504) else PAUSE_SECONDS * 2 ** attempt
            print(f"    API error {e.code}, retrying in {wait}s (attempt {attempt}/{MAX_RETRIES})", flush=True)
            time.sleep(wait)
        except httpx.TransportError as e:
            # Wi-Fi or DNS blip: the request never reached Google
            if attempt == MAX_RETRIES:
                raise
            wait = PAUSE_SECONDS * 2 ** attempt
            print(f"    Network error ({e}), retrying in {wait}s (attempt {attempt}/{MAX_RETRIES})")
            time.sleep(wait)
        except ValueError as e:
            if attempt == MAX_RETRIES:
                raise
            print(f"    {e}, retrying (attempt {attempt}/{MAX_RETRIES})")
            time.sleep(PAUSE_SECONDS)


def to_output_rows(aspects: list[AspectRow], batch: pd.DataFrame) -> pd.DataFrame:
    """Attach Brand from the source data and keep only rows for reviews in this batch."""
    brand_by_id = dict(zip(batch["Review_ID"], batch["Brand"]))
    rows = [
        {
            "Review_ID": a.review_id,
            "Brand": brand_by_id[a.review_id],
            "Brand_Pillar": a.brand_pillar,
            "Sub_Issue": a.sub_issue.strip().title(),
            "Sentiment_Score": SENTIMENT_TO_SCORE[a.sentiment],
            "Churn_Risk": a.churn_risk,
        }
        for a in aspects
        if a.review_id in brand_by_id
    ]
    return pd.DataFrame(rows, columns=OUTPUT_COLUMNS)


def main() -> None:
    api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        sys.exit("No API key found. Create a .env file in this folder containing:  GEMINI_API_KEY=your-key-here")

    client = genai.Client(api_key=api_key, http_options=types.HttpOptions(timeout=REQUEST_TIMEOUT_MS))
    reviews = load_reviews()
    output = load_existing_output()

    done_ids = set(output["Review_ID"].astype(int))
    pending = reviews[~reviews["Review_ID"].isin(done_ids)]
    batches = [pending.iloc[i:i + BATCH_SIZE] for i in range(0, len(pending), BATCH_SIZE)]

    print(f"Model: {MODEL}")
    print(f"{len(reviews)} reviews | {len(done_ids)} already done | "
          f"{len(pending)} pending in {len(batches)} batches")

    for n, batch in enumerate(batches, start=1):
        first_id, last_id = batch["Review_ID"].iloc[0], batch["Review_ID"].iloc[-1]
        print(f"[{n}/{len(batches)}] Reviews {first_id}-{last_id} ...", flush=True)
        try:
            aspects = call_gemini(client, batch)
        except Exception as e:
            print(f"\nStopped on batch {n}: {e}")
            print("Progress is saved. Re-run the script later to continue from here.")
            break

        new_rows = to_output_rows(aspects, batch)
        missing = set(batch["Review_ID"]) - set(new_rows["Review_ID"])
        if missing:
            print(f"    {len(missing)} review(s) got no aspects; they will be retried on the next run")

        output = pd.concat([output, new_rows], ignore_index=True)
        output.to_csv(OUTPUT_FILE, index=False, encoding="utf-8")
        print(f"    +{len(new_rows)} aspect rows (total {len(output)}) saved")

        if n < len(batches):
            time.sleep(PAUSE_SECONDS)

    covered = output["Review_ID"].nunique()
    print(f"\nDone: {covered}/{len(reviews)} reviews analysed, {len(output)} aspect rows in {OUTPUT_FILE}")
    if covered == len(reviews):
        print("\nSentiment split:")
        print(output["Sentiment_Score"].map({1: "Positive", 0: "Neutral", -1: "Negative"})
              .value_counts().to_string())
        print("\nAspect rows by brand and pillar:")
        print(pd.crosstab(output["Brand_Pillar"], output["Brand"]).to_string())


if __name__ == "__main__":
    main()
