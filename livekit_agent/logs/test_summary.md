# FDB-v3 adapter integration — test summary

- Scenarios run: 9
- Scenarios PASS: 8
- Scenarios FAIL: 1
  - [PASS] single_call:search_flights
  - [FAIL] single_call:get_card_benefits
  - [PASS] single_call:search_apartments
  - [PASS] single_call:track_order
  - [PASS] single_call:search_products
  - [PASS] chained:step1_search_flights_issued
  - [PASS] chained:step2_book_flight_followed
  - [PASS] interruption:stale_cancel_updated_args
  - [PASS] dedup:duplicate_add_to_cart_blocked_while_pending

Not the official FDB-v3 benchmark (no network/benchmark-data access in this sandbox) — see livekit_agent/fdb_tools.py docstring.
