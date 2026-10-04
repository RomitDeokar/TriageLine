"""Regression tests for the safe score-maximisation batch (all values synthetic):

  * BUDGET_RE accepted only a few of the natural ways a caller states a price ceiling.
  * doc_number dropped joined, dotted and cue-less ids.
  * order_id was dropped when the id is a plain lower-case word after an explicit cue.
  * informal magnitudes ("two grand", "ten k") were not converted.
  * a run of >=3 spelled single digits was summed ("five five five" -> 15) instead of joined (555).
  * replay_live_failures accepted any non-empty value for a $RESULT_n reference.

Run: pytest tests/test_score_fixes.py
"""
from agent import nlu


def _max_price(text):
    return nlu.extract_number(text, "max_price", {"type": "number"})


def test_budget_re_accepts_natural_ceiling_phrasings():
    cases = {
        "nothing over three hundred": 300,
        "I can't go above fifteen hundred": 1500,
        "nothing above two thousand": 2000,
        "can't go over 200": 200,
        "no higher than 1800": 1800,
        "cap it at 2800": 2800,
        "budget around 900": 900,
        "keep it below fifty bucks": 50,
        "stay under 1200": 1200,
        "under a hundred dollars": 100,
    }
    for text, want in cases.items():
        assert _max_price(text) == want, (text, _max_price(text))


def test_budget_default_phrasings_still_work():
    assert _max_price("under 2k") == 2000
    assert _max_price("budget of 1,250.50") == 1250.5
    assert _max_price("raise it to twenty five hundred") == 2500


def test_doc_number_joined_and_dotted_forms():
    assert nlu._doc_number("update my permit to QX741") == "QX741"
    assert nlu._doc_number("license number to Q.X. 741.") == "QX741"
    assert nlu._doc_number("driver's license Q X seven four one") == "QX741"
    # nothing id-shaped -> still None (never invent a number)
    assert nlu._doc_number("update my permit") is None
    assert nlu._doc_number("my passport is expiring soon") is None


def test_order_id_letter_only_word_after_explicit_cue():
    # a short lower-case word after an explicit cue is a spoken id
    assert nlu.extract_id(nlu.normalize_asr("track order number zqx"), "order_id") == "ZQX"
    assert nlu.extract_id(nlu.normalize_asr("order number Zqx"), "order_id") == "ZQX"
    assert nlu.extract_id(nlu.normalize_asr("track order number 456"), "order_id") == "456"


def test_informal_magnitudes():
    assert nlu._compound_normalize("two grand") == "2000"
    assert nlu._compound_normalize("a grand") == "1000"
    assert nlu._compound_normalize("ten k") == "10000"
    assert nlu._compound_normalize("it costs two grand") == "it costs 2000"


def test_spelled_digit_run_is_joined_not_summed():
    assert nlu._compound_normalize("five five five") == "555"
    assert nlu._compound_normalize("code five five five") == "code 555"
    # genuine cardinals are untouched
    assert nlu._compound_normalize("thirty five hundred") == "3500"
    assert nlu._compound_normalize("one hundred and five") == "105"


def test_phonetic_spelling_as_in_non_nato_word():
    assert nlu.extract_id(nlu.normalize_asr("order B as in Berry, O, B"), "order_id") == "BOB"


def test_replay_resolves_result_references():
    from livekit_agent import replay_live_failures as replay
    prior = [{"items": [{"sku": "QQ7"}, {"sku": "QQ9"}]}]
    ref = "$RESULT_0.items[0].sku"
    assert replay.resolve_reference(ref, prior) == "QQ7"
    assert replay.resolve_reference("$RESULT_5.items[0].sku", prior) is None
    assert replay._args_match({"sku": ref}, {"sku": "QQ7"}, prior) is True
    # a non-empty but WRONG value must now fail (it used to pass)
    assert replay._args_match({"sku": ref}, {"sku": "WRONG"}, prior) is False
    # no prior results available -> fall back to the old non-empty check
    assert replay._args_match({"sku": ref}, {"sku": "X"}) is True
