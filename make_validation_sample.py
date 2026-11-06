"""
Phase 2b - Build a blind hand-labelling workbook to check Gemini's ABSA quality.

Samples up to 15 already-analysed reviews per brand into validation_sample.xlsx
WITHOUT Gemini's labels, so your judgement is not influenced by them.
Re-run after more reviews are analysed: new brands are appended and any labels
you have already entered are kept.

When you have finished labelling, run compare_labels.py.
"""

from pathlib import Path

import pandas as pd
from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.worksheet.datavalidation import DataValidation

from run_absa import OUTPUT_FILE, load_reviews

WORKBOOK = Path("validation_sample.xlsx")
SHEET = "Labels"
PER_BRAND = 15
RANDOM_SEED = 42
MAX_ROWS = 500  # dropdowns are pre-applied this far down so appended rows get them

PILLARS = [
    "Texture & Finish",
    "Packaging & Dispensing",
    "Skin Compatibility & Fragrance",
    "Sun Protection & Value",
    "Overall Experience",
]
HEADERS = ["Review_ID", "Brand", "Review_Text", *PILLARS, "Churn_Risk", "Notes"]

FONT = Font(name="Arial", size=10)
BOLD = Font(name="Arial", size=10, bold=True)
HEADER_FILL = PatternFill("solid", start_color="D9D9D9")
INPUT_FILL = PatternFill("solid", start_color="FFFF00")
WRAP = Alignment(wrap_text=True, vertical="top")


def build_new_workbook() -> Workbook:
    wb = Workbook()
    guide = wb.active
    guide.title = "How To Label"
    lines = [
        ("How to label (do this BEFORE looking at Gemini's output)", BOLD),
        ("1. Go to the 'Labels' sheet. Read each review and fill only the yellow cells.", FONT),
        ("2. For each pillar the reviewer gives an opinion on, choose Positive / Neutral / Negative.", FONT),
        ("   Leave a pillar blank if the review does not mention it.", FONT),
        ("3. If one pillar has both praise and complaints, pick the stronger feeling.", FONT),
        ("4. Use 'Overall Experience' only when the review has no specific aspect (e.g. 'Good product').", FONT),
        ("5. Churn_Risk = High if they won't rebuy, returned it, warn others, or had a skin reaction. Else Low.", FONT),
        ("6. Always fill Churn_Risk: that marks the review as labelled.", FONT),
        ("", FONT),
        ("Pillar guide", BOLD),
        ("Texture & Finish: greasy, sticky, lightweight, white cast, dewy/matte finish, absorption", FONT),
        ("Packaging & Dispensing: pump, tube, cap, leaks, used/tampered pack, damaged delivery", FONT),
        ("Skin Compatibility & Fragrance: breakouts, irritation, allergy, eye stinging, skin type fit, smell", FONT),
        ("Sun Protection & Value: SPF works or not, tanning, price, quantity for price, fake product", FONT),
        ("", FONT),
        ("Example (made-up review, not part of the sample)", BOLD),
    ]
    for row, (text, font) in enumerate(lines, start=1):
        guide.cell(row=row, column=1, value=text).font = font

    example_row = len(lines) + 1
    example = {
        "Review_ID": 0, "Brand": "Example", "Review_Text":
        "Very light and no white cast, but it broke me out in two days. Won't buy again.",
        "Texture & Finish": "Positive", "Skin Compatibility & Fragrance": "Negative",
        "Churn_Risk": "High", "Notes": "Texture praised, breakouts = adverse reaction",
    }
    for col, header in enumerate(HEADERS, start=1):
        guide.cell(row=example_row, column=col, value=header).font = BOLD
        cell = guide.cell(row=example_row + 1, column=col, value=example.get(header))
        cell.font = FONT
        cell.alignment = WRAP
    guide.column_dimensions["A"].width = 12
    guide.column_dimensions["C"].width = 50
    for col in "DEFGH":
        guide.column_dimensions[col].width = 16

    ws = wb.create_sheet(SHEET)
    for col, header in enumerate(HEADERS, start=1):
        cell = ws.cell(row=1, column=col, value=header)
        cell.font = BOLD
        cell.fill = HEADER_FILL
        cell.alignment = WRAP
    ws.freeze_panes = "D2"

    widths = {"A": 10, "B": 12, "C": 60, "D": 14, "E": 14, "F": 16, "G": 14, "H": 14, "I": 11, "J": 30}
    for col, width in widths.items():
        ws.column_dimensions[col].width = width

    sentiment_dv = DataValidation(type="list", formula1='"Positive,Neutral,Negative"', allow_blank=True)
    churn_dv = DataValidation(type="list", formula1='"High,Low"', allow_blank=True)
    ws.add_data_validation(sentiment_dv)
    ws.add_data_validation(churn_dv)
    sentiment_dv.add(f"D2:H{MAX_ROWS}")
    churn_dv.add(f"I2:I{MAX_ROWS}")
    return wb


def main() -> None:
    if not OUTPUT_FILE.exists():
        raise SystemExit(f"{OUTPUT_FILE} not found. Run run_absa.py first.")

    reviews = load_reviews()
    done_ids = set(pd.read_csv(OUTPUT_FILE)["Review_ID"].astype(int))
    done = reviews[reviews["Review_ID"].isin(done_ids)]

    wb = load_workbook(WORKBOOK) if WORKBOOK.exists() else build_new_workbook()
    ws = wb[SHEET]
    existing = [(r[0], r[1]) for r in ws.iter_rows(min_row=2, max_col=2, values_only=True) if r[0]]
    existing_ids = {review_id for review_id, _ in existing}
    existing_per_brand = pd.Series([brand for _, brand in existing]).value_counts()

    added = []
    for brand, group in done.groupby("Brand"):
        need = PER_BRAND - int(existing_per_brand.get(brand, 0))
        candidates = group[~group["Review_ID"].isin(existing_ids)]
        if need <= 0 or candidates.empty:
            continue
        added.append(candidates.sample(n=min(need, len(candidates)), random_state=RANDOM_SEED))

    if not added:
        print("Nothing new to add: every analysed brand already has its sample.")
        return

    new_rows = pd.concat(added).sort_values("Review_ID")
    for row in new_rows.itertuples(index=False):
        r = ws.max_row + 1
        for col, value in enumerate([row.Review_ID, row.Brand, row.Review_Text], start=1):
            cell = ws.cell(row=r, column=col, value=value)
            cell.font = FONT
            cell.alignment = WRAP
        for col in range(4, len(HEADERS) + 1):
            cell = ws.cell(row=r, column=col)
            cell.fill = INPUT_FILL
            cell.font = FONT
            cell.alignment = WRAP

    wb.save(WORKBOOK)
    print(f"Added {len(new_rows)} reviews to {WORKBOOK}:")
    print(new_rows["Brand"].value_counts().to_string())
    missing = sorted(set(reviews["Brand"]) - set(done["Brand"]))
    if missing:
        print(f"Not analysed yet: {', '.join(missing)}. Re-run this script after run_absa.py finishes.")


if __name__ == "__main__":
    main()
