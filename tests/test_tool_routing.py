"""Regression: tool selection + slot filling on the 12-tool FDB-style manifest.

Guards the "wrong tool called" bug (e.g. a headphones query routed to track_order because
the word "track" appeared). Ranking must pick the right tool with a strict margin (no ties).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pytest  # noqa: E402

from agent import nlu  # noqa: E402
from livekit_agent.fdb_tools import FDB_TOOLS as T  # noqa: E402

ROUTING = {
    "I'm looking for a pair of wireless headphones and I'd like to track it under a hundred dollars "
    "as possible. What do you have?": "search_products",
    "Can you check where my package is, order BOB12?": "track_order",
    "I need a two bedroom place in Seattle for under 2500 a month": "search_apartments",
    "How long is the drive from downtown to the airport?": "calculate_commute",
    "Convert 100 dollars to euros": "get_exchange_rate",
    "How many euros is 250 dollars": "get_exchange_rate",
    "Set my price filter to 2000": "update_search_filter",
    "Change the bedrooms filter to 2": "update_search_filter",
    "Add two of PROD1 to my cart": "add_to_cart",
    "Put PROD1 in my basket": "add_to_cart",
    "Update my passport number to X1234567": "update_identity_doc",
    "Set up autopay for my credit card from checking": "modify_autopay",
    "Pay my utilities bill automatically from checking": "modify_autopay",
    "What perks come with a platinum card?": "get_card_benefits",
    "Book me on flight FL123, name John Doe": "book_flight",
    "Any flights to Miami on Friday?": "search_flights",
    "Where is my order BOB12": "track_order",
    "I want a cheap laptop": "search_products",
}


@pytest.mark.parametrize("utter,want", list(ROUTING.items()))
def test_routing(utter, want):
    r = nlu.score_tools(utter, T)
    assert r and r[0][1] == want, r[:3]
    assert len(r) == 1 or r[0][0] > r[1][0], f"tie: {r[:3]}"


ARGS = [
    ("How many euros is 250 dollars", "get_exchange_rate",
     {"amount": 250, "from_currency": "USD", "to_currency": "EUR"}),
    ("Convert 100 USD to EUR", "get_exchange_rate", {"amount": 100, "from_currency": "USD", "to_currency": "EUR"}),
    ("Commute time from Main Street to 5th Avenue by transit", "calculate_commute",
     {"origin_address": "Main Street", "destination_address": "5th Avenue", "mode": "transit"}),
    ("Update my passport number to X1234567", "update_identity_doc",
     {"doc_type": "passport", "doc_number": "X1234567"}),
    ("Set up autopay for my credit card from checking", "modify_autopay",
     {"bill_type": "credit_card", "source_account": "checking"}),
    # official mock: update_search_filter(filter_name, value: Any) -- numeric filters are sent as numbers
    ("Change the bedrooms filter to 2", "update_search_filter", {"filter_name": "bedrooms", "value": 2}),
    ("I need a two bedroom place in Seattle for under 2500 a month", "search_apartments",
     {"city": "Seattle", "bedrooms": 2, "max_price": 2500}),
]


@pytest.mark.parametrize("utter,tool,want", ARGS)
def test_args(utter, tool, want):
    args, missing = nlu.build_args(T[tool], utter, {})
    assert not missing, missing
    for k, v in want.items():
        assert args.get(k) == v, (k, args)
