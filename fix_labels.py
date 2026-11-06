"""
Phase 2c - Give Sub_Issue labels a name that matches their sentiment score.

Gemini often reused a label in the opposite sense: "Good Value Price" scored
negative means poor value, and "Greasy Oily Feel" scored positive means not
greasy. The scores are correct, but the names mislead in the Power BI
decomposition tree.

Reads enriched_beauty_sentiment.csv (left untouched) and writes
enriched_beauty_sentiment_clean.csv with the same rows and scores. Sub_Issue
holds the clear name and Original_Sub_Issue keeps Gemini's label. Neutral rows
keep their label.

Re-running is safe: it always rebuilds the clean file from Gemini's output.
"""

import re
from pathlib import Path

import pandas as pd

from run_absa import OUTPUT_FILE, load_reviews

CLEAN_FILE = Path("enriched_beauty_sentiment_clean.csv")

# Labels whose opposite has one clear name
SIMPLE_RENAMES = {
    ("Sticky Residue", 1): "Non Sticky Texture",
    ("Greasy Oily Feel", 1): "Non Greasy Texture",
    ("Pilling Under Makeup", 1): "No Pilling Makeup Friendly",
    ("Causes Breakouts", 1): "No Breakouts",
    ("Skin Irritation Burning", 1): "No Skin Irritation",
    ("Quick Absorption", -1): "Slow Absorption Or Blending",
    ("Convenient Packaging", -1): "Disliked Packaging Design",
    ("Matte Finish", -1): "Drying Finish",
    ("Good Value Price", -1): "Poor Value Price",
    ("Dewy Finish", -1): "No Dewy Glow",
}

# Rows checked by reading the review, where the opposite name would be wrong
ROW_OVERRIDES = {
    (124, "Dewy Finish"): "Too Dewy Shiny",
    (345, "Dewy Finish"): "Too Dewy Shiny",
    (932, "Dewy Finish"): "Too Dewy Shiny",
    (1033, "Dewy Finish"): "Too Dewy Shiny",
    (375, "Dewy Finish"): "Disliked Finish",
    (117, "Lightweight Texture"): "Drying Finish",
    (566, "Lightweight Texture"): "Too Watery Texture",
    (830, "Lightweight Texture"): "Poor Texture Consistency",
    (860, "Lightweight Texture"): "Slow Absorption Or Blending",
}

SKIN_TYPE_MISMATCH = "Unsuitable For Skin Type"


def clear_name(review_id: int, label: str, score: int, text: str) -> str:
    """Return the name that matches the score; the review text decides the ambiguous labels."""
    if score == 0:
        return label
    if score == -1 and (review_id, label) in ROW_OVERRIDES:
        return ROW_OVERRIDES[review_id, label]
    if (label, score) in SIMPLE_RENAMES:
        return SIMPLE_RENAMES[label, score]

    lowered = text.lower()
    if (label, score) == ("Suits Oily Skin", -1):
        # "Oly" is a spelling that appears in the reviews
        return "Unsuitable For Oily Skin" if re.search(r"oil|oly", lowered) else SKIN_TYPE_MISMATCH
    if (label, score) == ("Suits Sensitive Skin", -1):
        return "Unsuitable For Sensitive Skin" if "sensit" in lowered else SKIN_TYPE_MISMATCH
    if (label, score) == ("Lightweight Texture", -1):
        return "Too Watery Texture" if re.search(r"water|runny|drippy|liquid", lowered) else "Heavy Thick Texture"
    return label


def main() -> None:
    df = pd.read_csv(OUTPUT_FILE, encoding="utf-8")
    text_by_id = load_reviews().set_index("Review_ID")["Review_Text"]

    df.insert(df.columns.get_loc("Sub_Issue") + 1, "Original_Sub_Issue", df["Sub_Issue"])
    df["Sub_Issue"] = [
        clear_name(row.Review_ID, row.Original_Sub_Issue, row.Sentiment_Score, text_by_id[row.Review_ID])
        for row in df.itertuples(index=False)
    ]

    df.to_csv(CLEAN_FILE, index=False, encoding="utf-8")
    renamed = df[df["Sub_Issue"] != df["Original_Sub_Issue"]]
    print(f"Renamed {len(renamed)} of {len(df)} rows -> {CLEAN_FILE}\n")
    print(renamed.groupby(["Original_Sub_Issue", "Sub_Issue"]).size().to_string())


if __name__ == "__main__":
    main()
