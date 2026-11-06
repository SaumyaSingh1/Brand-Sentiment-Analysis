"""
Phase 1b - Clean and balance reviews.csv before the Gemini ABSA pipeline.

Steps:
  1. Back up the original collected reviews to reviews_raw.csv (only on first run).
  2. Repair garbled emoji/punctuation (UTF-8 text that was decoded as Windows-1252).
  3. Collapse line breaks and extra spaces inside each review.
  4. Standardise brand names (DotandKey -> Dot & Key).
  5. Drop empty reviews and duplicates.
  6. Downsample every brand to the size of the smallest brand (fixed seed) so
     brand-level counts are directly comparable.

Re-running is safe: it always rebuilds reviews.csv from reviews_raw.csv.
"""

import re
import shutil
from pathlib import Path

import pandas as pd

RAW_FILE = Path("reviews_raw.csv")
CLEAN_FILE = Path("reviews.csv")
RANDOM_SEED = 42

BRAND_MAP = {
    "dotandkey": "Dot & Key",
    "dot & key": "Dot & Key",
    "dot and key": "Dot & Key",
    "aqualogica": "Aqualogica",
    "minimalist": "Minimalist",
}

# Characters that only appear when UTF-8 bytes were read as Windows-1252
MOJIBAKE_MARKERS = ("Ã", "Â", "â€", "ð", "â˜", "âœ", "â¤", "ï¸")


def fix_mojibake(text: str) -> str:
    """Reverse UTF-8 -> Windows-1252 garbling, e.g. 'ðŸŒŸ' -> '🌟'."""
    if not any(marker in text for marker in MOJIBAKE_MARKERS):
        return text
    raw = bytearray()
    for ch in text:
        try:
            raw += ch.encode("cp1252")
        except UnicodeEncodeError:
            if ord(ch) < 256:
                # The 5 bytes cp1252 leaves undefined survive as latin-1 chars
                raw.append(ord(ch))
            else:
                # Genuine non-garbled character: keep its real UTF-8 bytes
                raw += ch.encode("utf-8")
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("utf-8", errors="ignore") or text


def clean_text(text: str) -> str:
    text = fix_mojibake(str(text))
    text = re.sub(r"\s+", " ", text)  # newlines, tabs, repeated spaces
    return text.strip()


def main() -> None:
    if not RAW_FILE.exists():
        shutil.copyfile(CLEAN_FILE, RAW_FILE)
        print(f"Backed up original data to {RAW_FILE}")

    df = pd.read_csv(RAW_FILE, encoding="utf-8")
    print(f"Raw rows: {len(df)}")
    print(df["Brand"].value_counts().to_string(), "\n")

    df["Brand"] = df["Brand"].str.strip().str.lower().map(BRAND_MAP).fillna(df["Brand"])
    df["Review_Text"] = df["Review_Text"].fillna("").map(clean_text)

    before = len(df)
    df = df[df["Review_Text"].str.len() > 0]
    print(f"Dropped {before - len(df)} empty reviews")

    before = len(df)
    df = df.drop_duplicates(subset=["Brand", "Review_Text"])
    print(f"Dropped {before - len(df)} duplicate reviews")

    per_brand = df["Brand"].value_counts().min()
    df = (
        df.groupby("Brand", group_keys=False)
        .apply(lambda g: g.sample(n=per_brand, random_state=RANDOM_SEED).sort_index())
        .reset_index(drop=True)
    )

    df.to_csv(CLEAN_FILE, index=False, encoding="utf-8")
    print(f"\nBalanced to {per_brand} reviews per brand -> {len(df)} total")
    print(df["Brand"].value_counts().to_string())
    print(f"Saved clean data to {CLEAN_FILE}")


if __name__ == "__main__":
    main()
