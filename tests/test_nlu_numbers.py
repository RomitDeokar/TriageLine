"""Regression tests for numeric / place / filter normalisation fixes (live argument accuracy).

These reproduce bugs that showed up in the official FDB-v3 audio run: thousands separators and k
suffixes were destroyed ("1,500" -> 1, "2k" -> 2), spoken compound budgets in filter values were
truncated to the first word, addresses kept spoken number words, and the loose "<key> to <value>"
filter fallback accepted verb phrases as filter keys. Values here are synthetic (never benchmark
items). Run: pytest tests/
"""
from agent import nlu


def test_compound_normalize_preserves_numeric_tokens():
    assert nlu._compound_normalize("1,500") == "1500"
    assert nlu._compound_normalize("under 2k") == "under 2000"
    assert nlu._compound_normalize("1,250.50 units") == "1250.5 units"
    # ordinals / plain counts must NOT be split ("5th" must stay "5th", "5" must stay "5")
    assert nlu._compound_normalize("unit 5th floor") == "unit 5th floor"
    assert nlu._compound_normalize("apartment 5") == "apartment 5"


def test_compound_normalize_spoken_amounts():
    assert nlu._compound_normalize("thirty five hundred") == "3500"
    assert nlu._compound_normalize("two hundred fifty") == "250"
    assert nlu._compound_normalize("fifteen hundred") == "1500"


def test_extract_number_handles_separators_and_suffix():
    spec = {"type": "number"}
    assert nlu.extract_number("keep it under 1,500", "max_price", spec) == 1500
    assert nlu.extract_number("under 2k", "max_price", spec) == 2000
    assert nlu.extract_number("budget of 1,250.50", "amount", spec) == 1250.5


def test_clean_place_normalizes_spoken_number_but_rejects_bare_number():
    assert nlu._clean_place("five hundred Elm Road") == "500 Elm Road"
    # "close to one" is a pronoun, not a place value
    assert nlu._clean_place("one") is None
    assert nlu._clean_place("1") is None


def test_extract_filters_normalizes_compound_value():
    got = dict(nlu.extract_filters("raise the price cap to twenty five hundred"))
    assert got.get("max_price") == 2500, got


def test_extract_filters_rejects_verb_phrase_key():
    # a loose "X to Y" match must not turn a sentence into a filter key
    assert nlu.extract_filters("yeah i want to update it to something") == []
