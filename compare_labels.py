"""
Phase 2b - Compare your blind hand labels with Gemini's ABSA output.

Reads validation_sample.xlsx (only rows where you filled Churn_Risk) and
enriched_beauty_sentiment.csv, prints agreement metrics, and writes every
disagreement to validation_disagreements.csv so you can judge who was right.
"""

import pandas as pd

from make_validation_sample import PILLARS, SHEET, WORKBOOK
from run_absa import OUTPUT_FILE

SCORE_TO_LABEL = {1: "Positive", 0: "Neutral", -1: "Negative"}


def gemini_pillar_sentiment(scores: pd.Series) -> str:
    """Most frequent sentiment for a pillar; ties go to the more negative one."""
    counts = scores.value_counts()
    top = counts[counts == counts.max()].index
    return SCORE_TO_LABEL[int(min(top))]


def main() -> None:
    human = pd.read_excel(WORKBOOK, sheet_name=SHEET)
    human = human[human["Churn_Risk"].notna()]
    if human.empty:
        raise SystemExit("No labelled rows yet: fill Churn_Risk for each review you label.")

    gemini = pd.read_csv(OUTPUT_FILE)
    gemini = gemini[gemini["Review_ID"].isin(human["Review_ID"])]
    g_sent = gemini.groupby(["Review_ID", "Brand_Pillar"])["Sentiment_Score"].agg(gemini_pillar_sentiment)
    g_churn = gemini.groupby("Review_ID")["Churn_Risk"].agg(lambda s: "High" if (s == "High").any() else "Low")
    g_issues = gemini.groupby("Review_ID")["Sub_Issue"].agg(", ".join)

    both = human_only = gemini_only = sentiment_match = churn_match = 0
    disagreements = []
    # iterrows, not itertuples: pillar names contain spaces and '&'
    for _, record in human.iterrows():
        rid = int(record["Review_ID"])
        for pillar in PILLARS:
            mine = record[pillar] if pd.notna(record[pillar]) else None
            theirs = g_sent.get((rid, pillar))
            if mine and theirs:
                both += 1
                sentiment_match += mine == theirs
                if mine != theirs:
                    disagreements.append((rid, pillar, mine, theirs))
            elif mine:
                human_only += 1
                disagreements.append((rid, pillar, mine, "(not found)"))
            elif theirs:
                gemini_only += 1
                disagreements.append((rid, pillar, "(not marked)", theirs))
        churn_match += record["Churn_Risk"] == g_churn.get(rid)
        if record["Churn_Risk"] != g_churn.get(rid):
            disagreements.append((rid, "Churn_Risk", record["Churn_Risk"], g_churn.get(rid)))

    n = len(human)
    pct = lambda a, b: f"{a / b:.0%}" if b else "n/a"
    print(f"Reviews compared: {n}  ({human['Brand'].value_counts().to_dict()})\n")
    print(f"Aspect recall     (pillars you marked that Gemini also found): {pct(both, both + human_only)}")
    print(f"Aspect precision  (pillars Gemini found that you also marked):  {pct(both, both + gemini_only)}")
    print(f"Sentiment match   (same sentiment where you both found it):     {pct(sentiment_match, both)}")
    print(f"Churn risk match  (same High/Low per review):                   {pct(churn_match, n)}")

    report = pd.DataFrame(disagreements, columns=["Review_ID", "Field", "Your_Label", "Gemini_Label"])
    texts = human.set_index("Review_ID")["Review_Text"]
    report["Gemini_Sub_Issues"] = report["Review_ID"].map(g_issues)
    report["Review_Text"] = report["Review_ID"].map(texts)
    report.to_csv("validation_disagreements.csv", index=False, encoding="utf-8-sig")
    print(f"\n{len(report)} disagreements written to validation_disagreements.csv")
    print("Review each one: was Gemini wrong, or were you? Both happen.")


if __name__ == "__main__":
    main()
