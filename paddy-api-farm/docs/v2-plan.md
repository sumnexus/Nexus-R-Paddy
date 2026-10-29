# Paddy API Farm v2 — plan and compute budget

Written 28 October 2026, before any v2 code or result.

## Measured starting point (v1 code, this machine, one core)

| | built-in season | conventional season |
|---|---:|---:|
| first season in a process (forests load) | 17.5 s | 7.9 s |
| later seasons | 11.5 s | ≈ 6 s |

The machine has 2 cores and 7 GB of memory. Profiling a built-in season: about half the time is building the state
(the spray-timing and pest-risk forests), a quarter the hourly physics, the rest the audit and forest walks.

## Stages

0. **Setup (done).** SHA-256 of every file in `agri-sim` (236, without `test/tmp/`) and `agri-sim-py` (61) →
   `tools/checksums/before-v2.json` (identical to the v1 start snapshot); SHA-256 of every v1 file (173) →
   `tools/checksums/v1.json`; `release/paddy-api-farm-v1.zip` kept; v1 results copied to `tests/results/v1/`.
1. **Arms in the backend.** `paddy/robots/`: copies of the port's `planner.py`, `arms.py` and `energy.py`, adapted to
   the two 6 × 6 fields; the frame, docks, stations, inlets and outlets; one job queue per arm; decisions become arm
   jobs; the arm system advances in lock-step with the hourly twin and each square's effect is applied at its actual
   time; energy (solar, battery, biogas, grid) for the pump, the arms and the frame; the state says which arms are
   busy; plans in the decision records and frames. Tests: reach and joint limits for all 72 squares, zero collisions,
   determinism and replay.
2. **Definitions addendum** (before any v2 result): time to AWD, the planned-spray target and its ceiling, the new
   sets with seeds and weather years, the targets.
3. **3D farm** (the playground's WebGL2 renderer, geometry and scene, wrapped as a React component): runs while the
   model data is generated in the background.
4. **Model round.** Feature rows from the audit; training set; v1 forests on the new test set (the "before");
   paddy-specific forests compared on the test set only; A/B of every change that alters decisions; ceilings by
   re-simulation; freeze; the live set once.
5. **Final checks** on the final build: unit tests, ≥ 10,000 fuzzed replies, 200 seasons per decision maker, ≥ 100
   HTTP seasons (auto and approve, with faults), determinism and replay, Playwright desktop/phone light/dark, the new
   3D and arm tests.
6. **Delivery:** README, zip, screenshots, the replay page republished at the same address, checksum proof.

## Compute budget (2 cores; about 9 s per season on average with the arms, measured again after stage 1)

| Purpose | Seasons | Wall time |
|---|---:|---:|
| Training (every decision maker, both seasons, half with events) | ≈ 5,000 | ≈ 6–7 h |
| Test set: 200 paired cases × 4 decision makers, run for the "before" and for each candidate that changes decisions | 800 per run | ≈ 1 h per run |
| A/B checks of decision changes (≥ 200 paired cases each) | ≈ 1,200 | ≈ 1.5 h |
| Live set: 500 paired cases × 4 decision makers, once after the freeze | 2,000 | ≈ 2.5 h |
| Final campaign (200 × 4) and ≥ 100 HTTP seasons | ≈ 900 | ≈ 1.5 h |
| Ceilings: ≥ 100,000 re-draws (spray outcomes, pest projections, probe re-draws, …) | — | ≈ 1–2 h |
| **Total** | **≥ 10,000** | **≈ 15–18 h** |

Every long job runs in the background in chunks of at most 100 minutes, writes one result file per season as it
goes, skips finished seasons when restarted, and is checked to be alive before anything relies on it.
Speed-ups that leave every number unchanged (forest walks, caching inside one hour) are allowed; each is checked by
comparing season digests before and after.
