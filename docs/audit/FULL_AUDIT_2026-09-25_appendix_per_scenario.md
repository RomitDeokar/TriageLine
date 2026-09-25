# Appendix — per-scenario official FDB-v3 results (text-mode offline replay, exact-match evaluator, 2026-09-25)

Overall strict pass: 16/100. Generated from `evaluate_pass_rate.py` output; see main report §4.

| # | Scenario | Domain | Diff | Disfluency | Rollback | Result | Why it failed (official evaluate_pass_rate.py, exact match) |
|---|---|---|---|---|---|---|---|
| 1 | ecommerce_01 | ecommerce | easy |  |  | FAIL | Missing tools: ['track_order'] |
| 2 | ecommerce_01 | ecommerce | easy |  |  | FAIL | Missing tools: ['track_order'] |
| 3 | ecommerce_02 | ecommerce | easy |  |  | FAIL | Wrong arguments for: ['search_products'] — Mismatch 'query': expected=wireless headphones, got=Hmm... um... so i'm looking for a pair of wireless headphones... and |
| 4 | ecommerce_04 | ecommerce | easy | PAUSE |  | FAIL | Missing tools: ['track_order'] |
| 5 | ecommerce_05 | ecommerce | easy | FILLER,HESITATION |  | FAIL | Missing tools: ['search_products'] |
| 6 | ecommerce_06 | ecommerce | easy |  |  | FAIL | Wrong arguments for: ['add_to_cart'] — Missing argument: quantity |
| 7 | ecommerce_07 | ecommerce | easy | FALSE_START |  | FAIL | Missing tools: ['track_order'] |
| 8 | ecommerce_08 | ecommerce | easy | PAUSE |  | FAIL | Missing tools: ['search_products'] |
| 9 | ecommerce_08 | ecommerce | easy | PAUSE |  | FAIL | Missing tools: ['search_products'] |
| 10 | ecommerce_09 | ecommerce | medium | SELF_CORRECTION | Y | FAIL | Wrong arguments for: ['search_products'] — Mismatch 'query': expected=hiking boots, got=Like well hmm could you search for running shoes — actually no um, I just r |
| 11 | ecommerce_10 | ecommerce | medium |  |  | FAIL | Missing tools: ['search_products'] |
| 12 | ecommerce_11 | ecommerce | medium | SELF_CORRECTION | Y | FAIL | Wrong arguments for: ['add_to_cart'] — Missing argument: quantity |
| 13 | ecommerce_12 | ecommerce | medium | HESITATION,PAUSE |  | FAIL | Missing tools: ['search_products'] |
| 14 | ecommerce_13 | ecommerce | medium |  |  | FAIL | Missing tools: ['track_order'] |
| 15 | ecommerce_13 | ecommerce | medium |  |  | FAIL | Missing tools: ['track_order'] |
| 16 | ecommerce_14 | ecommerce | medium | FILLER |  | FAIL | Wrong arguments for: ['add_to_cart'] — Missing argument: quantity |
| 17 | ecommerce_14 | ecommerce | medium | FILLER |  | FAIL | Wrong arguments for: ['add_to_cart'] — Missing argument: quantity |
| 18 | ecommerce_15 | ecommerce | medium | FILLER,PAUSE |  | PASS |  |
| 19 | ecommerce_15 | ecommerce | medium | FILLER,PAUSE |  | PASS |  |
| 20 | ecommerce_16 | ecommerce | medium | HESITATION,PAUSE |  | FAIL | Missing tools: ['search_products'] |
| 21 | ecommerce_17 | ecommerce | medium | FALSE_START |  | PASS |  |
| 22 | ecommerce_18 | ecommerce | hard |  |  | FAIL | Missing tools: ['search_products', 'add_to_cart', 'track_order'] |
| 23 | ecommerce_19 | ecommerce | hard | SELF_CORRECTION | Y | FAIL | Wrong arguments for: ['search_products', 'add_to_cart'] — Mismatch 'query': expected=tablet, got=Um so you know search for a laptop — wait no um um, I actually need a tablet, not |
| 24 | ecommerce_20 | ecommerce | hard |  |  | FAIL | Missing tools: ['search_products', 'add_to_cart'] |
| 25 | ecommerce_21 | ecommerce | hard | FILLER |  | FAIL | Missing tools: ['search_products', 'add_to_cart'] |
| 26 | ecommerce_21 | ecommerce | hard | FILLER |  | FAIL | Missing tools: ['search_products', 'add_to_cart'] |
| 27 | ecommerce_23 | ecommerce | hard |  |  | FAIL | Wrong arguments for: ['add_to_cart'] — Mismatch 'quantity': expected=1, got=-1 |
| 28 | ecommerce_25 | ecommerce | hard | FALSE_START |  | FAIL | Missing tools: ['search_products', 'add_to_cart'] |
| 29 | ecommerce_25 | ecommerce | hard | FALSE_START |  | FAIL | Missing tools: ['search_products', 'add_to_cart'] |
| 30 | finance_01 | finance | easy |  |  | FAIL | Wrong arguments for: ['get_exchange_rate'] — Mismatch 'from_currency': expected=USD, got=EUR |
| 31 | finance_01 | finance | easy |  |  | FAIL | Wrong arguments for: ['get_exchange_rate'] — Mismatch 'from_currency': expected=USD, got=EUR |
| 32 | finance_02 | finance | easy |  |  | PASS |  |
| 33 | finance_03 | finance | easy | FILLER |  | PASS |  |
| 34 | finance_03 | finance | easy | FILLER |  | PASS |  |
| 35 | finance_04 | finance | easy | PAUSE |  | FAIL | Wrong arguments for: ['get_exchange_rate'] — Mismatch 'from_currency': expected=USD, got=GBP |
| 36 | finance_06 | finance | easy | FILLER,PAUSE |  | FAIL | Missing tools: ['get_card_benefits'] |
| 37 | finance_07 | finance | easy | FALSE_START |  | PASS |  |
| 38 | finance_07 | finance | easy | FALSE_START |  | PASS |  |
| 39 | finance_08 | finance | easy | HESITATION |  | FAIL | Wrong arguments for: ['get_exchange_rate'] — Mismatch 'from_currency': expected=EUR, got=USD |
| 40 | finance_08 | finance | easy | HESITATION |  | FAIL | Wrong arguments for: ['get_exchange_rate'] — Mismatch 'from_currency': expected=EUR, got=USD |
| 41 | finance_10 | finance | medium |  |  | FAIL | Wrong arguments for: ['get_exchange_rate'] — Mismatch 'from_currency': expected=GBP, got=JPY |
| 42 | finance_11 | finance | medium | FILLER |  | PASS |  |
| 43 | finance_12 | finance | medium | SELF_CORRECTION | Y | FAIL | Missing tools: ['get_exchange_rate'] |
| 44 | finance_12 | finance | medium | SELF_CORRECTION | Y | FAIL | Missing tools: ['get_exchange_rate'] |
| 45 | finance_14 | finance | medium |  |  | FAIL | Wrong arguments for: ['get_exchange_rate', 'get_exchange_rate'] — Mismatch 'from_currency': expected=USD, got=EUR |
| 46 | finance_15 | finance | medium | SELF_CORRECTION | Y | FAIL | Wrong arguments for: ['modify_autopay'] — Mismatch 'source_account': expected=savings, got=checking |
| 47 | finance_16 | finance | medium | HESITATION,PAUSE |  | FAIL | Unexpected tools: ['search_products'] |
| 48 | finance_17 | finance | medium | FALSE_START |  | PASS |  |
| 49 | finance_18 | finance | hard |  |  | FAIL | Wrong arguments for: ['get_exchange_rate'] — Mismatch 'from_currency': expected=USD, got=EUR |
| 50 | finance_19 | finance | hard | SELF_CORRECTION | Y | FAIL | Wrong arguments for: ['get_exchange_rate'] — Mismatch 'to_currency': expected=GBP, got=JPY |
| 51 | finance_20 | finance | hard |  |  | PASS |  |
| 52 | finance_21 | finance | hard | FILLER |  | PASS |  |
| 53 | finance_22 | finance | hard | FILLER |  | PASS |  |
| 54 | finance_23 | finance | hard | FALSE_START,SELF_CORRECTION | Y | FAIL | Wrong arguments for: ['modify_autopay'] — Mismatch 'source_account': expected=savings, got=checking |
| 55 | housing_01 | housing | easy |  |  | PASS |  |
| 56 | housing_02 | housing | easy |  |  | FAIL | Wrong arguments for: ['calculate_commute'] — Mismatch 'destination_address': expected=Downtown Office, got=the Downtown Office during a normal day |
| 57 | housing_03 | housing | easy | FILLER |  | FAIL | Missing tools: ['update_search_filter'] |
| 58 | housing_04 | housing | easy | PAUSE |  | PASS |  |
| 59 | housing_05 | housing | easy | HESITATION,PAUSE |  | FAIL | Wrong arguments for: ['search_apartments'] — Missing argument: pets_allowed |
| 60 | housing_05 | housing | easy | HESITATION,PAUSE |  | FAIL | Wrong arguments for: ['search_apartments'] — Missing argument: pets_allowed |
| 61 | housing_06 | housing | easy |  |  | FAIL | Wrong arguments for: ['calculate_commute'] — Mismatch 'destination_address': expected=Gym, got=the Gym |
| 62 | housing_08 | housing | easy | FALSE_START |  | FAIL | Missing tools: ['update_search_filter'] |
| 63 | housing_09 | housing | medium | SELF_CORRECTION | Y | PASS |  |
| 64 | housing_10 | housing | medium |  |  | FAIL | Missing tools: ['update_search_filter']; Unexpected tools: ['search_flights'] |
| 65 | housing_11 | housing | medium | SELF_CORRECTION | Y | FAIL | Missing tools: ['search_apartments'] |
| 66 | housing_13 | housing | medium |  |  | FAIL | Missing tools: ['update_search_filter', 'update_search_filter']; Unexpected tools: ['searc |
| 67 | housing_14 | housing | medium | FILLER |  | FAIL | Wrong arguments for: ['search_apartments'] — Missing argument: pets_allowed |
| 68 | housing_14 | housing | medium | FILLER |  | FAIL | Wrong arguments for: ['search_apartments'] — Missing argument: pets_allowed |
| 69 | housing_15 | housing | medium | FALSE_START |  | FAIL | Missing tools: ['search_apartments']; Unexpected tools: ['calculate_commute'] |
| 70 | housing_15 | housing | medium | FALSE_START |  | FAIL | Missing tools: ['search_apartments']; Unexpected tools: ['calculate_commute'] |
| 71 | housing_17 | housing | medium | SELF_CORRECTION | Y | FAIL | Wrong arguments for: ['calculate_commute'] — Mismatch 'origin_address': expected=my house, got=there — wait, no, actually... um, I just remembered I'd be driving. So |
| 72 | housing_17 | housing | medium | SELF_CORRECTION | Y | FAIL | Wrong arguments for: ['calculate_commute'] — Mismatch 'origin_address': expected=my house, got=there — wait, no, actually... um, I just remembered I'd be driving. So |
| 73 | housing_18 | housing | hard |  |  | FAIL | Missing tools: ['search_apartments', 'calculate_commute']; Unexpected tools: ['search_prod |
| 74 | housing_19 | housing | hard | SELF_CORRECTION | Y | FAIL | Missing tools: ['calculate_commute'] |
| 75 | housing_20 | housing | hard |  |  | FAIL | Missing tools: ['search_apartments', 'calculate_commute', 'update_search_filter']; Unexpec |
| 76 | housing_21 | housing | hard | SELF_CORRECTION | Y | FAIL | Missing tools: ['search_apartments', 'calculate_commute'] |
| 77 | housing_22 | housing | hard | FILLER |  | FAIL | Missing tools: ['update_search_filter', 'search_apartments', 'calculate_commute'] |
| 78 | housing_24 | housing | hard | PAUSE,FILLER |  | FAIL | Missing tools: ['update_search_filter', 'calculate_commute']; Unexpected tools: ['search_p |
| 79 | housing_24 | housing | hard | PAUSE,FILLER |  | FAIL | Missing tools: ['update_search_filter', 'calculate_commute']; Unexpected tools: ['search_p |
| 80 | housing_25 | housing | hard | SELF_CORRECTION | Y | FAIL | Missing tools: ['update_search_filter', 'update_search_filter', 'search_apartments'] |
| 81 | travel_01 | travel | easy |  |  | FAIL | Wrong arguments for: ['search_flights'] — Mismatch 'date': expected=July 15, got=July 15th |
| 82 | travel_02 | travel | easy |  |  | FAIL | Wrong arguments for: ['update_identity_doc'] — Mismatch 'doc_number': expected=P9-9-9-90011, got=P88990011 |
| 83 | travel_03 | travel | easy | FILLER |  | FAIL | Wrong arguments for: ['search_flights'] — Mismatch 'date': expected=August 20, got=August 20th |
| 84 | travel_03 | travel | easy | FILLER |  | FAIL | Wrong arguments for: ['search_flights'] — Mismatch 'date': expected=August 20, got=August 20th |
| 85 | travel_05 | travel | easy | FILLER,HESITATION |  | FAIL | Wrong arguments for: ['search_flights'] — Mismatch 'date': expected=December 12, got=December 12th |
| 86 | travel_07 | travel | easy |  |  | FAIL | Wrong arguments for: ['update_identity_doc'] — Mismatch 'doc_type': expected=driver_license, got=drivers_license |
| 87 | travel_07 | travel | easy |  |  | FAIL | Wrong arguments for: ['update_identity_doc'] — Mismatch 'doc_type': expected=driver_license, got=drivers_license |
| 88 | travel_08 | travel | easy | FALSE_START |  | FAIL | Wrong arguments for: ['search_flights'] — Mismatch 'date': expected=November 1, got=November 1st |
| 89 | travel_10 | travel | medium | FILLER,SELF_CORRECTION | Y | FAIL | Wrong arguments for: ['search_flights'] — Mismatch 'date': expected=October 7, got=October 7th |
| 90 | travel_11 | travel | medium |  |  | FAIL | Missing tools: ['book_flight'] |
| 91 | travel_14 | travel | medium | HESITATION,PAUSE |  | FAIL | Wrong arguments for: ['search_flights'] — Mismatch 'date': expected=March 22, got=March 22nd |
| 92 | travel_16 | travel | medium | FILLER |  | FAIL | Missing tools: ['search_flights'] |
| 93 | travel_18 | travel | hard | FILLER |  | FAIL | Missing tools: ['book_flight', 'update_identity_doc'] |
| 94 | travel_19 | travel | hard | SELF_CORRECTION | Y | FAIL | Wrong arguments for: ['search_flights'] — Mismatch 'destination': expected=Milan, got=Rome |
| 95 | travel_20 | travel | hard |  |  | FAIL | Wrong arguments for: ['search_flights', 'update_identity_doc'] — Mismatch 'date': expected=July 20, got=July 20th |
| 96 | travel_21 | travel | hard | PAUSE,FILLER |  | FAIL | Missing tools: ['book_flight', 'update_identity_doc'] |
| 97 | travel_21 | travel | hard | PAUSE,FILLER |  | FAIL | Missing tools: ['book_flight', 'update_identity_doc'] |
| 98 | travel_23 | travel | hard | FILLER |  | FAIL | Wrong arguments for: ['update_identity_doc'] — Mismatch 'doc_type': expected=driver_license, got=drivers_license |
| 99 | travel_23 | travel | hard | FILLER |  | FAIL | Wrong arguments for: ['update_identity_doc'] — Mismatch 'doc_type': expected=driver_license, got=drivers_license |
| 100 | travel_24 | travel | hard |  |  | FAIL | Missing tools: ['book_flight', 'update_identity_doc'] |
