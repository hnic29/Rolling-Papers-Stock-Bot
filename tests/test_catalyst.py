from app.services.catalyst import (
    CATEGORY_NONE,
    CATEGORY_OTHER,
    SENTIMENT_NEGATIVE,
    SENTIMENT_NEUTRAL,
    SENTIMENT_POSITIVE,
    classify_catalyst,
    describe_catalyst,
)


def test_no_headline_is_none_and_neutral():
    assert classify_catalyst(None) == (CATEGORY_NONE, SENTIMENT_NEUTRAL)
    assert classify_catalyst("") == (CATEGORY_NONE, SENTIMENT_NEUTRAL)


def test_the_grml_headline_is_flagged_as_dilution_risk():
    """The concrete lesson this module exists for - Ross Cameron's 2026-08-25 GRML
    trade lost $8,000 on exactly this kind of headline."""
    category, sentiment = classify_catalyst(
        "Greenland Mines Announces Rare Earth Acquisition and Proposed Public Offering"
    )
    assert category == "offering_dilution"
    assert sentiment == SENTIMENT_NEGATIVE


def test_reverse_split_is_flagged_negative():
    category, sentiment = classify_catalyst("Company Announces 1-for-50 Reverse Stock Split")
    assert category == "reverse_split"
    assert sentiment == SENTIMENT_NEGATIVE


def test_fda_headline_is_positive():
    category, sentiment = classify_catalyst("Company Receives FDA Breakthrough Therapy Designation")
    assert category == "fda_clinical"
    assert sentiment == SENTIMENT_POSITIVE


def test_contract_headline_is_positive():
    """The Aug 25 RCON trade - his big +$17,000 winner - had exactly this shape of
    catalyst (an oilfield services contract)."""
    category, sentiment = classify_catalyst("Recon Technology Lands $1.3 Million Oilfield Services Contract")
    assert category == "contract"
    assert sentiment == SENTIMENT_POSITIVE


def test_record_operational_update_is_classified_as_earnings():
    """Ross Cameron's 2026-08-26 CRE trade ("Breaking News Sends Chinese Stock Up 225%
    in 15-minutes") was driven by a record-results operational update - not literally
    the word "earnings" - that a narrower pattern would have missed entirely."""
    category, sentiment = classify_catalyst(
        "Cre8 Enterprise Reports Record H1 2026 Customer Count, Up 34.8% Year-Over-Year"
    )
    assert category == "earnings"
    assert sentiment == SENTIMENT_POSITIVE


def test_negative_pattern_wins_over_a_positive_one_in_the_same_headline():
    """A genuinely good clinical result doesn't cancel out the dilution risk of an
    offering announced in the same breath - the offering is the more actionable
    signal for how hard to press size, so it must take priority."""
    category, sentiment = classify_catalyst(
        "Company Reports Positive Phase 2 Results and Announces Proposed Public Offering"
    )
    assert category == "offering_dilution"
    assert sentiment == SENTIMENT_NEGATIVE


def test_unrecognized_headline_is_other_not_none():
    """Still 'has news' - just not a pattern this classifier knows yet. Must not be
    conflated with genuinely having no catalyst at all."""
    category, sentiment = classify_catalyst("Company Announces New Website Redesign")
    assert category == CATEGORY_OTHER
    assert sentiment == SENTIMENT_NEUTRAL


def test_describe_catalyst_none_has_no_reason_line():
    assert describe_catalyst(CATEGORY_NONE, SENTIMENT_NEUTRAL) is None


def test_describe_catalyst_negative_carries_a_visible_warning():
    text = describe_catalyst("offering_dilution", SENTIMENT_NEGATIVE)
    assert "⚠" in text
    assert "offering" in text.lower()


def test_describe_catalyst_positive_is_a_plain_reason():
    text = describe_catalyst("contract", SENTIMENT_POSITIVE)
    assert "⚠" not in text
    assert "contract" in text.lower()
