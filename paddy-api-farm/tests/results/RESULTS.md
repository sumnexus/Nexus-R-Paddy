### Guardrail fuzzing

`python -m paddy fuzz --n 12000` pushed 12,000 random replies through validation, the guardrails and execution, each on a copy of a real farm state (372 moments of four built-in seasons, kharif and rabi, with pest outbreaks added so sprays have targets). The replies mix garbage (not JSON, wrong types, NaN, 5,000-character strings, "ignore previous instructions"), near-misses (squares off the grid, unknown products and pests, out-of-range times) and plausible but unsafe decisions (doses over the caps, unlabelled products, sprays in rain, wind, darkness or on flowering rice with bee-toxic products, harvests inside a waiting period, irrigation over the bund or during the drain-down). Decisions scheduled for later were run to their time and checked again. After every execution the farm was checked against the rules.

| | |
|---|---:|
| Replies | 12,000 |
| Decisions that passed the schema | 17,782 |
| Schema problems reported (decisions dropped or fields removed) | 55,042 |
| Accepted / with a warning / rejected by the guardrails | 5,320 / 2,645 / 9,817 |
| Carried out | 6,356 |
| Stopped by the second check at execution time | 32 |
| **Crashes** | **0** |
| **Rule violations found in the farm afterwards** | **0** |

| Action | accepted | warning | rejected |
|---|---:|---:|---:|
| wait | 958 | 619 | 467 |
| puddle | 42 | 0 | 1,821 |
| transplant | 0 | 21 | 1,765 |
| irrigate | 351 | 214 | 1,211 |
| soil_check | 1,359 | 0 | 388 |
| drain | 33 | 1,244 | 430 |
| hold | 1,302 | 0 | 359 |
| harvest | 20 | 51 | 1,453 |
| fertilise | 479 | 207 | 556 |
| scout | 758 | 41 | 244 |
| spray | 14 | 112 | 627 |
| amend | 4 | 136 | 496 |

### Whole seasons through HTTP

`python tests/e2e/mock_seasons.py --seasons 25`: 100 seasons where every decision came over a real socket from the stand-in API (which requires the key: every request carried a test key), plus the built-in manager on the same cases for reference. The faulty stand-in answered 4 % of requests with 429 (Retry-After), 3 % with 500, timed out on 1 %, sent malformed JSON 4 % of the time and slipped an unsafe decision into 8 % of its replies. The cautious farmer turned down preventive sprays, immature harvests and every other scouting request, with a reason each time. 12 minutes.

| | Seasons | Finished | Mean yield t/ha | Calls | Retried and then fine | Repaired | Failed → fallback | Unsafe suggestions → rejected | Rejected by the farmer → reason seen in the next call | Same decisions as the built-in manager | Key found in a record |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Built-in manager (reference, no HTTP) | 25 | 25 | 4.26 | 7,318 | 0 | 0 | 0 → 0 | 0 → 0 | 0 → 0 | – | 0 |
| API through the stand-in, auto | 25 | 25 | 4.26 | 7,318 | 0 | 0 | 0 → 0 | 0 → 0 | 0 → 0 | 25 of 25 | 0 |
| API through the stand-in, approve mode, every decision approved | 25 | 25 | 4.26 | 7,318 | 0 | 0 | 0 → 0 | 0 → 0 | 0 → 0 | 25 of 25 | 0 |
| API through a faulty stand-in, auto | 25 | 25 | 4.26 | 7,318 | 843 | 290 | 0 → 0 | 584 → 584 | 0 → 0 | 0 of 25 | 0 |
| API through a faulty stand-in, approve mode, a cautious farmer | 25 | 25 | 4.26 | 7,340 | 845 | 293 | 0 → 0 | 574 → 574 | 1,760 → 1,760 | 0 of 25 | 0 |

All checks passed. Without faults the API through the stand-in made exactly the built-in decisions, in auto and in approve mode. With faults the decision lists differ (each rejected unsafe suggestion is a decision record too, and the cautious farmer turned decisions down), yet every season finished, every unsafe suggestion was stopped, and the farmer's reasons reached the decision maker.

### Determinism and exact replay

`python tests/e2e/determinism.py`:

| Case | Decision maker | Run twice in separate processes | Decision fingerprint | Farm fingerprint |
|---|---|---|---|---|
| kharif 20261024, weather year 0 | builtin | identical | `12a884b29cf1039f` | `48c45fe19c3537aa` |
| kharif 20261024, weather year 0 | conventional | identical | `62041ae98cbc742b` | `df49d2d86d5e1714` |
| kharif 20261024, weather year 0 | api | identical | `12a884b29cf1039f` | `48c45fe19c3537aa` |
| rabi 7400011, weather year 6001 | builtin | identical | `ebef58c09debeea6` | `e11f7f0e81f70da5` |
| rabi 7400011, weather year 6001 | conventional | identical | `c94777321e12534e` | `ef99f4700186984f` |
| rabi 7400011, weather year 6001 | api | identical | `ebef58c09debeea6` | `e11f7f0e81f70da5` |
| kharif 7400012, weather year 6002 | builtin | identical | `b5059024b6a60c7c` | `74a27bca27079bf2` |
| kharif 7400012, weather year 6002 | conventional | identical | `2fa1aa6aec0d1b0e` | `66475a3ace27016a` |
| kharif 7400012, weather year 6002 | api | identical | `b5059024b6a60c7c` | `74a27bca27079bf2` |

The API through the stand-in made exactly the built-in manager's decisions, and the farm followed exactly the same course, in 3 of 3 cases (the fingerprints above match row for row).

| Recorded run | Calls | Retried or repaired | Failed | Replayed from the records |
|---|---:|---:|---:|---|
| kharif 20261024 | 302 | 0 | 0 | identical decisions and farm |
| rabi 7400011, stand-in with faults | 285 | 43 | 0 | identical decisions and farm |

### The strategy comparison

`python -m paddy compare --managers api,api-no-advice,builtin,conventional --seasons 10 --mock` (the same 10 cases for each, kharif and rabi alternating). It prints the cost estimate first; with the stand-in nothing is spent.

| Mean per season (± 95 % interval) | API (stand-in) | API without advice (stand-in) | Built-in | Conventional |
|---|---:|---:|---:|---:|
| Yield, t/ha | 4.23 ± 0.31 | 4.22 ± 0.30 | 4.23 ± 0.31 | 3.95 ± 0.26 |
| Irrigation, mm | 956 ± 151 | 952 ± 154 | 956 ± 151 | 1,336 ± 186 |
| Nitrogen, kg N/ha | 109 ± 7 | 109 ± 7 | 109 ± 7 | 110 ± 6 |
| Insecticide, g a.i./ha | 9.5 ± 2.4 | 8.2 ± 3.1 | 9.5 ± 2.4 | 360.2 ± 0.4 |
| Bt, kg/ha | 0.24 ± 0.11 | 0.21 ± 0.10 | 0.24 ± 0.11 | 0.00 ± 0.00 |
| Area sprayed, m² | 20 ± 7 | 19 ± 8 | 20 ± 7 | 292 ± 7 |
| Methane, kg CH₄/ha | 65 ± 4 | 62 ± 1 | 65 ± 4 | 113 ± 2 |
| Decision calls | 289 ± 3 | 289 ± 4 | 289 ± 3 | 289 ± 4 |
| Input tokens (stand-in count) | 1,353,036 ± 29,226 | 1,137,552 ± 17,095 | – | – |
| Output tokens (stand-in count) | 14,440 ± 538 | 13,129 ± 932 | – | – |

Stand-in check: the API through the stand-in made exactly the built-in decisions in 10 of 10 seasons.

Without the forests' advice the stand-in falls back on the built-in manager's plain rules: almost the same yield, a little less spraying, and a little less methane because it applies no compost (only the soil advice suggests it), and its calls carry 16 % fewer input tokens. With a real model, these two columns are the test of whether the advice helps it.

- API (stand-in): 1,353,036 input and 14,440 output tokens per season by the stand-in's count ≈ US$4.28 per season at US$3 / US$15 per million.
- API without advice (stand-in): 1,137,552 input and 13,129 output tokens per season by the stand-in's count ≈ US$3.61 per season at US$3 / US$15 per million.

### The website in a browser

`node tests/e2e/ui.js` (Playwright, Chromium) against `serve --manager api --mode approve --mock`: 41 checks passed, 0 failed (in 76 s).

From the page itself, on desktop:

- 4 decisions wait for approval
- stand-in decisions are labelled "stand-in mock API"
- approving from the page carries the decision out
- rejecting from the page records the reason
- the person's reason reaches the decision maker in the next call
- "New season" from the page resets to day 0 with the API in auto mode
- Run and Pause from the page (status paused on day 52, 116 calls so far)
- adding a storm from the page is confirmed ("Event added.")
- the storm is in the weather log: "Heavy storm: about 95 mm tomorrow from 14:00"
- the storm triggers a decision call (2026-07-23T18:00, triggers scheduled+event)
- about 95 mm of storm rain reaches the fields (95 mm on A in a day)

On every viewport (desktop 1440 px and phone 400 px, light and dark):

| Check | desktop light | desktop dark | phone light | phone dark |
|---|---|---|---|---|
| fields view loads | yes | yes | yes | yes |
| arrow keys move the selection | yes | yes | yes | yes |
| a decision's detail shows the state that was sent | yes | yes | yes | yes |
| views switch from the keyboard | yes | yes | yes | yes |
| no sideways scrolling | yes | yes | yes | yes |
| no console errors | yes | yes | yes | yes |

- whole season: a kharif season runs to the end from the page (13 s, 283 calls to the stand-in)
- whole season: both fields harvested (A 4.83 t/ha, B 4.44 t/ha)
- whole season: every decision came from the stand-in ({"mock":214})
- whole season: the conventional run on the same weather finished alongside (4.631 t/ha against 4.63)
- whole season: no console errors
- backend down: the page says so ("The farm server is not answering. Start it from the backend folder with python -m paddy ser…")

The published replay page, tested without any server (`node tests/e2e/replay_ui.js`): 22 checks passed, 0 failed.

| Check | desktop, light | phone, dark |
|---|---|---|
| labelled as a stand-in replay | yes | yes |
| opens on a growing crop | yes | yes |
| steps to the next decision point | yes | yes |
| a decision's call opens with the state that was sent | yes | yes |
| the charts carry the conventional run on the same weather | yes | yes |
| the comparison tables are on the page | yes | yes |
| "Back to day 0" starts over | yes | yes |
| Run plays the season | yes | yes |
| no sideways scrolling | yes | yes |
| no console errors | yes | yes |
| no network requests | yes | yes |

