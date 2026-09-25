"""Empirically check whether agent/nlu.py's existing schema-driven arg-filling
(build_args/_fill/_arg_for/parse_field_answer) generalizes to the real FDB-v3
tool schemas, without changing nlu.py. Exercises one representative utterance
per tool. Prints PASS/FAIL per tool and writes livekit_agent/logs/tool_integration.log.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent import nlu  # noqa: E402
from livekit_agent.fdb_tools import FDB_TOOLS  # noqa: E402

CASES = [
    ("search_flights", "search flights to Boston tomorrow"),
    ("book_flight", "book it for John Smith"),
    ("update_identity_doc", "update my passport number to X123456"),
    ("get_card_benefits", "what are the benefits of my platinum card"),
    ("get_exchange_rate", "convert 100 USD to EUR"),
    ("modify_autopay", "turn on autopay for utilities from checking"),
    ("search_apartments", "find a 2 bedroom apartment in Denver under 2000"),
    ("calculate_commute", "commute time from 1 Main St to 2 Oak Ave driving"),
    ("update_search_filter", "set price_range filter to under_500"),
    ("track_order", "track order BOB12"),
    ("search_products", "search for headphones under 50 dollars"),
    ("add_to_cart", "add product PROD1 quantity 2 to cart"),
]

LOG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs", "tool_integration.log")


def main():
    lines = ["=== FDB-v3 schema compatibility check against existing agent/nlu.py (unmodified) ===\n"]
    ok, bad = 0, 0
    for api, utter in CASES:
        spec = FDB_TOOLS[api]
        args, missing = nlu.build_args(spec, utter, {})
        # score_tools should also rank this tool #1 for its own utterance
        ranked = nlu.score_tools(utter, FDB_TOOLS)
        top = ranked[0][1] if ranked else None
        status = "PASS" if not missing and top == api else "FAIL"
        if status == "PASS":
            ok += 1
        else:
            bad += 1
        line = f"[{status}] {api:22s} utter={utter!r} -> args={args} missing={missing} top_ranked={top}"
        print(line)
        lines.append(line)
    lines.append(f"\n{ok}/{ok+bad} tools PASS")
    with open(LOG_PATH, "a") as f:
        f.write("\n".join(lines) + "\n")
    print(f"\n{ok}/{ok+bad} PASS. log written to {LOG_PATH}")


if __name__ == "__main__":
    main()
