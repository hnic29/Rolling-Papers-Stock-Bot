"""Classifies a news headline into a catalyst category and sentiment.

Keyword-based, not ML - deliberately so: every classification is traceable to the exact
phrase that triggered it, auditable in the reasons list a candidate already carries, and
doesn't need training data or a model dependency for what is fundamentally a small, well-
known vocabulary (day-trading catalyst language barely changes year to year).

The concrete lesson this exists for: Ross Cameron's 2026-08-25 GRML trade (Greenland
Mines) lost $8,000, chased right after a win, on a stock whose catalyst was a rare-earth
acquisition PLUS a proposed public offering announced right after a reverse split. A
proposed/registered offering is one of the most reliable "day trader beware" catalysts
in the small-cap world: it signals imminent share dilution, and informed participants
routinely sell into exactly this kind of news, producing the fast, sharp reversal GRML
showed. Recognizing that phrase and treating it as a genuine risk signal - not just
"has_news: true" indistinguishable from an FDA approval - is precisely the gap this
module closes.
"""

import re

# Checked FIRST and take priority over positive keywords in the same headline - a
# genuinely great clinical result announced alongside a dilutive offering is still a
# dilutive-offering trade risk-wise; the offering is the more actionable signal for a
# day trader deciding how hard to press size.
_NEGATIVE_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("offering_dilution", re.compile(r"\b(proposed|registered direct|public|underwritten|private placement)\b.{0,30}\boffering\b", re.I)),
    ("offering_dilution", re.compile(r"\bshelf registration\b", re.I)),
    ("offering_dilution", re.compile(r"\bwarrant(s)? exercise\b", re.I)),
    ("offering_dilution", re.compile(r"\bdilutive\b", re.I)),
    ("reverse_split", re.compile(r"\breverse (stock )?split\b", re.I)),
    ("going_concern", re.compile(r"\bgoing concern\b", re.I)),
    ("delisting", re.compile(r"\bdelist(ing|ed)?\b", re.I)),
    ("restatement", re.compile(r"\brestate(ment|s)?\b.{0,20}\b(financials|earnings|results)\b", re.I)),
]

_POSITIVE_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("fda_clinical", re.compile(r"\b(fda|clinical trial|phase (1|2|3|i|ii|iii)|breakthrough therapy)\b", re.I)),
    ("contract", re.compile(r"\b(contract|purchase order|deal worth|awarded)\b", re.I)),
    ("acquisition", re.compile(r"\b(acqui(re|res|sition)|merger|to be acquired|buyout)\b", re.I)),
    ("earnings", re.compile(
        r"\b(earnings|beats? estimates?|quarterly results|revenue (grew|jumped|surged)"
        r"|record (h1|h2|quarter|quarterly|revenue|customers?|results)|record-breaking)\b",
        re.I,
    )),
    ("partnership", re.compile(r"\b(partnership|collaborat|strategic alliance)\b", re.I)),
    ("patent", re.compile(r"\bpatent\b", re.I)),
    ("upgrade", re.compile(r"\b(upgrades?|price target (raised|increased))\b", re.I)),
]

CATEGORY_NONE = "none"
CATEGORY_OTHER = "other"
SENTIMENT_POSITIVE = "positive"
SENTIMENT_NEGATIVE = "negative"
SENTIMENT_NEUTRAL = "neutral"


def classify_catalyst(headline: str | None) -> tuple[str, str]:
    """Returns (category, sentiment). No headline -> (none, neutral). A headline that
    doesn't match any known pattern -> (other, neutral) - still "has news," just not a
    catalyst type this recognizes yet; nothing here should be read as "no catalyst
    exists," only "not one of the patterns currently tracked."""
    if not headline:
        return CATEGORY_NONE, SENTIMENT_NEUTRAL

    for category, pattern in _NEGATIVE_PATTERNS:
        if pattern.search(headline):
            return category, SENTIMENT_NEGATIVE

    for category, pattern in _POSITIVE_PATTERNS:
        if pattern.search(headline):
            return category, SENTIMENT_POSITIVE

    return CATEGORY_OTHER, SENTIMENT_NEUTRAL


_CATEGORY_LABELS = {
    "offering_dilution": "a proposed/registered stock offering",
    "reverse_split": "a reverse stock split",
    "going_concern": "a going-concern warning",
    "delisting": "a delisting notice",
    "restatement": "a financial restatement",
    "fda_clinical": "an FDA/clinical-trial result",
    "contract": "a new contract or purchase order",
    "acquisition": "an acquisition or merger",
    "earnings": "an earnings report",
    "partnership": "a strategic partnership",
    "patent": "a patent grant",
    "upgrade": "an analyst upgrade",
    "other": "a news catalyst",
}


def describe_catalyst(category: str, sentiment: str) -> str | None:
    """A short, human-readable reason string for the category/sentiment pair - None for
    CATEGORY_NONE, since "no catalyst" isn't worth a reason line."""
    if category == CATEGORY_NONE:
        return None
    label = _CATEGORY_LABELS.get(category, "a news catalyst")
    if sentiment == SENTIMENT_NEGATIVE:
        return f"⚠ dilution-risk catalyst: {label} - reversals after this pattern are common, size down"
    return f"has {label}"
