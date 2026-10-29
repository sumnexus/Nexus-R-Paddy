# Paddy API Farm

Two small paddy (rice) fields near Bengaluru, simulated hour by hour, where **every farm decision comes from an
external AI model through an API**. Field A and field B are each a 6 × 6 grid of 1 m² squares. Twice a day, and
whenever something happens, the farm sends the model a compact picture of both fields, with advice from trained
random forests. The model replies in JSON: what to do, where, when and why. Software checks every decision against
safety rules before a Python digital twin carries it out. A React website shows the fields, every decision with the
model's own reason, and lets you run, pause, step, add weather and pest events, and approve or reject decisions.

**Version 2** puts the two fields on the playground's own 3D model, with four robot arms that carry out every
decision square by square, and brings the forecasts the farm checks to 95 % or more on new test and live sets — every
check in every group, except v1's yield forecast for conventionally managed fields on the live set (94.5 %) — see
[What's new in v2](#whats-new-in-v2).

You need your own decision API (any service that speaks the common chat-completions format) and its key. Without a
key everything still runs: a **stand-in mock API** answers with the built-in manager's choices, and the website labels
those decisions "stand-in" everywhere.

![The two paddy fields in 3D under the sensor frame, on a kharif morning of the recorded stand-in season: the four arms, water at its true depth, the rice by stage, and the solar array, battery and biogas unit that power them](docs/screens/farm3d-overview.png)

- [What's new in v2](#whats-new-in-v2)
- [Run it](#run-it)
- [Architecture](#architecture)
- [The decision loop](#the-decision-loop)
- [The state sent to the API](#the-state-sent-to-the-api)
- [The action schema](#the-action-schema)
- [Guardrails](#guardrails)
- [The decision API client](#the-decision-api-client)
- [REST API reference](#rest-api-reference)
- [The digital twin](#the-digital-twin)
- [The 3D farm](#the-3d-farm)
- [The arms](#the-arms)
- [The model round](#the-model-round)
- [Decision makers compared](#decision-makers-compared)
- [Test and simulation results](#test-and-simulation-results)
- [v2 change log](#v2-change-log)
- [Checksums](#checksums)
- [Limits](#limits)
- [Why it is built this way](#why-it-is-built-this-way)
- [Numbers and their sources](#numbers-and-their-sources)
- [Files](#files)

---

## What's new in v2

- **A 3D farm.** The Fields view opens on a 3D model of the two paddies, drawn with the playground's own WebGL2
  renderer: water at its true depth, drying and cracking soil, rice by stage, pests, spray, rain, day and night, and the
  overlays of the plan view — all a pure function of what the twin recorded. [The 3D farm](#the-3d-farm)
- **Four arms carry out every decision.** A planting, a watering, a pest and a harvest arm ride a cube frame over both
  fields and do each accepted decision square by square, on routes planned by the playground's planner, with collision
  avoidance, refills and energy; the 3D view animates exactly the recorded plans, and approve mode shows a pending
  decision's path before you approve it. [The arms](#the-arms)
- **Every model at 95 %.** A new test set (200 seasons per decision maker) and a live set (500 per decision maker, run
  once after the freeze): every check at ≥ 95 % pooled, per decision maker and per season on the test set, and on the
  live set all but one group — including the two new checks: the time to AWD (41.1 % → 99.2 % on the test set,
  99.2 % live) and the planned spray (66.7 % → 99.4 %, 99.2 % live). The one short of 95 % is v1's yield forecast for
  conventionally managed fields on the live set, 94.5 % (it over-forecasts; a calibration would fix it — next round).
  The farm now draws the coming weather from the exact posterior of the weather generator given its forecasts, fits
  its water level to two days of readings, and asks its own arm planner when each square will be sprayed.
  [The model round](#the-model-round)
- **The built-in manager stops re-flooding early**: in the margin above the AWD line where v1 re-flooded at once, it
  now waits when the time-to-AWD forest expects the line only after the next decision point (−0.6 % irrigation and
  pump energy over 650 paired seasons, yield unchanged). State `paddy-state/1.3`, prompt `paddy-prompt/1.6`.
- **What changed and why**, with numbers, tests and A/B results: [v2 change log](#v2-change-log). The earlier projects
  are unchanged byte for byte: [Checksums](#checksums).

---

## Run it

You need Python 3.10 or newer (built and tested on 3.13) with NumPy. Node 18 or newer is only needed to rebuild the
website; a built copy is in `frontend/dist/`.

```bash
cd paddy-api-farm/backend
pip install -r requirements.txt            # NumPy; nothing else
```

### Without a key (stand-in mock)

```bash
python -m paddy serve --manager api --mock      # open http://127.0.0.1:8000
```

The mock is a real HTTP server on your machine speaking the chat-completions format. It reads the state out of each
request and answers with the built-in manager's choices. Every decision it makes is marked "stand-in". Add
`--faults p429=0.05,p500=0.03,pmalformed=0.05,punsafe=0.1` to watch the retries, repair requests and guardrails work.

### With your key

1. Copy `.env.example` to `.env` next to this README and fill in `API_BASE_URL`, `API_MODEL` and `API_KEY`. `.env` is
   git-ignored and left out of the zip; the key is never logged, stored, exported or shown.
2. Check the connection with one call:

   ```bash
   python -m paddy check-api            # prints the settings (key only as "present"), sends one state, validates the reply
   python -m paddy check-api --dry-run  # settings only, no call
   ```
3. Run the farm on it:

   ```bash
   python -m paddy serve --manager api                 # decisions carried out automatically
   python -m paddy serve --manager api --mode approve  # each decision waits for you in the website
   ```

A season takes about 290 calls (06:00 and 18:00 for about 140 days, plus triggers). Commands that would call the real
API many times (`run`, `compare`, the website's comparison) show the cost estimate first and wait for an explicit yes
(`--yes`, or the confirmation box in the website).

### Without any API

`--manager builtin` (the farm's own rules with the forests' advice) or `--manager conventional` (common practice:
calendar inputs, continuous flooding).

### Headless

```bash
python -m paddy run --manager api --mock --out runs/demo      # one season; every call and decision recorded
python -m paddy replay runs/demo                              # re-run it from the recorded replies: identical
python -m paddy compare --managers api,builtin,conventional --seasons 10 --mock   # paired, with a cost estimate
python -m paddy campaign --managers builtin,conventional --seasons 200            # validation campaign
python -m paddy fuzz --n 10000                                # guardrail fuzzing
python -m paddy mock --port 8765                              # the stand-in API on its own
python -m paddy sets                                          # v2: the data sets' seeds and weather years, overlap check
python -m paddy batch --set T --start 0 --stop 200 --models paddy --tag after   # v2: a data set, resumable chunks
```

The model round's every step, in order and resumable, is in [`tools/models/RUNBOOK.md`](tools/models/RUNBOOK.md).

### Rebuild the website

With network access: `cd frontend && npm install && npm run dev` (Vite dev server; `/api` is proxied to the backend on
port 8000) or `npm run build`. This copy was built offline, because the npm registry wasn't reachable from the build
machine: `node build.mjs` bundles with esbuild, taking React and d3 from `node_modules/` or from the folder in
`NODE_TOOLS`. The self-contained replay page comes from a recorded run:

```bash
cd backend
python -m paddy run --manager api --mock --seed 20261024 --season kharif --out ../runs/demo-kharif
python -m paddy export-replay ../runs/demo-kharif --baseline \
    --campaign ../tests/results/campaign200.json="Validation campaign"
cd ../frontend && node build.mjs --replay ../runs/demo-kharif/replay.json    # → dist-replay/paddy-replay.html
```

The replay page carries the 3D farm too and replays the recorded arm plans; it is one self-contained file
(5.9 MB: every call of the recorded season with the state sent and the reply, 2.8 MB; the 06:00/18:00 frames, 1.2 MB; the
arm plans and keyframes, 0.7 MB; decisions, hourly weather, water and power, the conventional run on the same weather
and the campaign summary, 0.5 MB; the page's code and fonts, 0.7 MB).

### Tests

```bash
cd paddy-api-farm
python -m unittest discover -s tests             # unit, client, engine, twin, manager, arm, 3D-state, model-round and
                                                 # REST tests (109; about 5 minutes on two cores)
python tests/e2e/mock_seasons.py --set W --start 0 --seasons 10 --out tests/results/e2e_mock_part0.json
                                                 # seasons of set W through the stand-in over HTTP, auto and approve, with
                                                 # faults (in parts; --merge joins them)
python tests/e2e/determinism.py                  # same case twice → same decisions and farm; recorded runs replay exactly
python tests/e2e/fuzz_chunks.py --n 12000 --chunks 4 --chunk 0   # guardrail fuzzing in parts (… --chunk 1, 2, 3; then --merge)
node tests/e2e/ui.js http://127.0.0.1:8812/      # browser test against `serve --port 8812 --manager api --mode approve --mock`
node tests/e2e/replay_ui.js frontend/dist-replay/paddy-replay.html   # browser test of the static replay page
node tests/e2e/farm3d_ui.js frontend/dist-replay/paddy-replay.html tests/results/screens3d http://127.0.0.1:8813/
                                                 # v2: the 3D farm — every camera preset on desktop and phone, light and
                                                 # dark; inspect, highlight, approve-mode path, no-WebGL2 fallback
node tests/e2e/farm3d_controls.js frontend/dist-replay/paddy-replay.html   # v2: orbit, pan, zoom, tilt by mouse, keys, touch
node tests/e2e/farm3d_fps.js frontend/dist-replay/paddy-replay.html   # v2: frame rates by render quality and window size
python tests/e2e/campaign_resumable.py --parts tests/results/campaign200.parts.jsonl   # v2: the campaign, resumable
python tests/e2e/campaign_v1_v2.py tests/results/v1/campaign200.json tests/results/campaign200.json   # v1 → v2 farm
python tests/e2e/analyse_campaign.py tests/results/campaign200.json   # campaign tables
python tests/e2e/results_md.py                   # the other result tables
python tools/checksums/verify.py --v2            # the earlier projects are unchanged (v2 run)
```

---

## Architecture

```
 React website (frontend/)                  Python backend (backend/paddy/)
 ─────────────────────────                  ──────────────────────────────────────────────────────────────
 Fields (3D / plan) · Decisions ── REST ──▶ server.py      stdlib HTTP server, SSE stream, OpenAPI, serves the site
 Charts · Compare · Models      ◀── SSE ─── engine.py      the clock: decision points, triggers, budgets, approvals,
 three/  the playground's WebGL2              │            the arms in lock-step with the hourly twin
         renderer, scene, geometry            ├─ decisions/state.py       what the decision maker is shown
                                              ├─ decisions/api_client.py  `api = DecisionAPI.from_env()`, adapters,
                                              │                           retries, repair, fallback   ──HTTPS──▶ your API
                                              ├─ decisions/mock_api.py    stand-in API (same wire format)
                                              ├─ decisions/managers.py    built-in and conventional managers
                                              ├─ decisions/schema.py      the reply's JSON Schema + validator
                                              ├─ decisions/guardrails.py  accept / warn / reject, twice
                                              ├─ decisions/actuators.py   SimActuators (twin) · HttpActuators (stub)
                                              ├─ decisions/records.py     audit trail, redaction, CSV
                                              ├─ robots/                  v2: the four arms — planner, arms, traffic,
                                              │                           energy (copied from the playground's port),
                                              │                           layout for two 6 × 6 basins, the arm system
                                              ├─ sim/twin.py              the two fields, hour by hour
                                              ├─ sim/advisor.py           the forests' advice (v2: the farm's level
                                              │                           fit, posterior weather draws, arm minutes)
                                              ├─ sim/wxpost.py            v2: the posterior of the coming weather given
                                              │                           the forecasts
                                              ├─ sim/audit.py             checks of that advice (fixed definitions)
                                              ├─ modelround.py, datasets.py  v2: the sets, rows, scoring, the paddy pack
                                              └─ core/                    copied from the Agri Robotics Playground's
                                                                          Python port: weather, agronomy, library,
                                                                          random forests and the trained forests;
                                                                          v2: paddy_features.py and models/paddy_forests.*
```

One farm runs at a time on the server. Everything except the website is Python with NumPy, using only the standard
library for HTTP (server and client). The website talks to the backend only through the REST API and the event stream.

---

## The decision loop

1. **Decision points.** At 06:00 and 18:00 every simulated day, and right away after a trigger:
   - a new pest sighting by the cameras;
   - a field's water tube reaching its AWD threshold (15 cm below the surface; the surface itself from about a week
     before flowering to a week after it);
   - a forecast storm (a day with 64.5 mm or more forecast at ≥ 50 % chance, IMD's "heavy rain" class);
   - the end of a wait the decision maker asked for (`wait`);
   - an event you add (outbreak, storm, dry spell, overcast week).

   One call covers both fields. Triggers are at least one simulated hour apart. Budgets: 6 calls per simulated day,
   600 per season, 240 per real hour (environment variables override them). A call over budget is skipped and logged.
2. **The state.** A compact JSON of what the farm knows (next section), plus the forests' advice, clearly labelled.
   Nothing the farm couldn't know goes in: no true soil, no true pest severity, no "this seedling will die".
3. **The reply** is JSON validated against a JSON Schema (`decisions/schema.py`); invalid decisions are dropped with a
   reason, valid ones kept.
4. **Guardrails** check each decision when it arrives and again just before it runs (a spray scheduled for 17:00 may
   meet rain): accepted, accepted with a warning, or rejected. Rejections and warnings go back in the next state's
   `recent_decisions`.
5. **Execution** by the arms (v2): each accepted decision that needs a machine becomes a job for its arm, and each
   square changes when the arm finishes it ([The arms](#the-arms)); the actuator interface stays — `SimActuators`
   applies the arms' work to the twin, `HttpActuators` is a documented stub that would POST the same commands to a real
   controller and is off unless explicitly enabled.
6. **Modes.** `auto` carries accepted decisions out at once (or at their `when`). `approve` holds each one until you
   approve or reject it in the website; the clock waits meanwhile. A rejection, with your reason, goes back to the
   decision maker.
7. **Audit.** Each call keeps the exact state sent, the raw reply (never the key), validation errors, latency, tokens,
   retries, repair and fallback. Each decision keeps its guardrail verdict and messages, approval, execution and
   outcome (for example "34 of 36 transplanted squares established after 6 days" or "mean kill 71 %, forest predicted
   69 %"). The forests' advice is checked with the original project's fixed definitions (`docs/DEFINITIONS.md`,
   copied unchanged; `docs/DEFINITIONS-paddy.md` says how they apply here; `docs/DEFINITIONS-paddy-v2.md`, the v2
   addendum written before any v2 result, adds the time to AWD, the planned spray as a target and what the arms
   change).
8. **Failure.** If a call still fails after retries and one repair request, the configured fallback runs: `hold`
   (nothing happens until the next decision point) or `builtin` (the built-in manager decides this one time). Either way
   it is logged and shown as a fallback.

---

## The state sent to the API

About 8,600 characters of JSON on average (≈ 2,150 tokens; v1: 7,800); the fixed instructions (`decisions/prompt.py`,
versioned `paddy-prompt/1.6`) add about 2,700 tokens. A real state from the recorded demo season (the 06:00 call on
day 115, with the pest arm finishing a spray and both fields' time-to-AWD advice), abridged where it says "…":

```json
{
  "v": "paddy-state/1.3",
  "farm": {"fields": ["A", "B"], "grid": "6x6 squares of 1 m2 per field"},
  "now": {"time": "2026-09-24T06:00", "day_of_season": 115, "season": "kharif", "trigger": ["scheduled"], "month": 9},
  "weather": {
    "station": {"temp_c": 20.1, "rh": 95, "wind_ms": 1.8},
    "rain_last_24h_mm": 58.3,
    "next_12h": {"rain_mm": 0.0, "max_wind_ms": 5.0},
    "forecast": [{"date": "2026-09-24", "tmax": 25.5, "tmin": 20.5, "rain_mm": 1.6, "rain_prob": 0.73, "rh": 86, "wind_ms": 3.3}, "…"]
  },
  "pump": {"m3_per_h": 3.0, "shared_by": []},
  "fields": {
    "A": {
      "phase": "growing",
      "puddled": true,
      "transplanted": "2026-06-03",
      "days_after_transplanting": 112.5,
      "stage": "Grain filling",
      "progress": 0.872,
      "maturity_estimate": "2026-10-10",
      "days_to_maturity": 16.0,
      "phi_no_harvest_before": "2026-10-04T19:01",
      "plants": {"planted": 36, "alive": 36, "dead": 0, "health": 0.99},
      "water": {"tube_mm": 53, "square_depth_range_mm": [34, 67], "target_mm": null, "pumping": false, "draining": false, "awd_threshold_mm": -150},
      "soil": {"codes": ["nnnnnn", "nnnann", "nnnann", "nnnnnn", "nnnnnn", "nnnnnn"], "avg": {"ph": 5.7, "ec": 0.35, "n": 258, "p": 27, "k": 288, "oc": 0.58, "sand": 27, "clay": 44}, "probed_squares": 16, "probe_age_days": 115.0},
      "inputs": {"n_kg_ha": 99.7, "p2o5_kg_ha": 50.1, "k2o_kg_ha": 50.4, "splits": [["basal", 50.0, "2026-06-02"], "…"], "irrigation_m3": 27.1, "irrigation_mm": 753, "rain_mm": 512, "pump_kwh": 11.38, "sprays": 7, "spray_l": 0.66},
      "pests": {"sightings": [{"sq": "A3,1", "pest": "leaf_folder", "sev": 0.045, "days": 80.6}, "…"], "n_sightings": 4, "protected_squares": {"pymetrozine": 2}, "recent_sprays": [{"time": "2026-08-05T20:00", "field": "A", "product": "chlorantraniliprole", "pest": "yellow_stem_borer", "squares": 1, "max_sev": 0.049}, "…"]},
      "advice": {
        "soil": {"grid": ["ASSSSS", "SSSASS", "SSSASS", "SSSSSS", "SSSSSS", "SSSSSS"]},
        "yield": {"forecast_at_transplanting": 0.85},
        "pest_risk": {"farm_acts_at": 0.545, "at_risk": [], "checked_plants": 52, "max_risk": 0.0},
        "spray": {"options": {"leaf_folder": [{"product": "chlorantraniliprole", "best_safe_time": "2026-09-24T06:00", "kill_at_best_safe_time": 0.57, "kill_now": 0.57, "candidate_hours": ["06:00 0.57", "07:00 0.57", "08:00 0.55"], "phi_days": 47, "bee_toxicity": 0.2, "irac": "28"}, "…"], "…": "other pests"}},
        "water": {"hours_to_awd_threshold": 67, "threshold_mm": -150, "from": "forest"}
      }
    },
    "B": {"…": "the same for field B"}
  },
  "recent_decisions": [{"time": "2026-09-23T18:00", "field": "B", "action": "scout", "status": "executed", "note": "scouted 1 squares"}, "…"],
  "outcomes": [{"time": "2026-09-23T18:00", "field": "B", "action": "scout", "text": "1 of 1 squares have pests"}, "…"],
  "limits": {"calls_left_today": 5, "calls_left_season": 355, "max_decisions_per_call": 12, "decision_hours": [6, 18]},
  "arms": {"planting": null, "watering": null, "pest": {"jobs": ["spray A"], "busy_until": "2026-09-24T06:00"}, "harvest": null},
  "advice_track_record": {
    "soil": "89% of 72 checks this season",
    "establish": "100% of 218 checks this season",
    "pest": "100% of 12924 checks this season",
    "spray": "100% of 30 checks this season"
  }
}
```

- **Soil per square** as a 6 × 6 matrix of compact codes from the readings alone (`n` fine, `a` acid, `k` alkaline,
  `s` saline, `l` low nitrogen), with field averages. Before transplanting the raw pH, EC and N grids are included too.
- **Advice** (left out entirely for `api-no-advice`): soil suitability per square (S / A / U) and suggested
  amendments; before transplanting, the establishment chance (mean, minimum, lowest squares) and the yield forecast
  if transplanted today; while growing, pest risk over 7 days per plant at risk, spray options with the predicted kill
  at the best safe hour (the best kill less 1.2 points per hour of waiting, among hours that pass the rain, wind,
  daylight and bee rules, as the original farm chose) and at the candidate hours, and harvest readiness near maturity.
- **Timing:** `days_after_transplanting`, `progress`, `days_to_maturity` (counted exactly as the waiting-period rule
  counts it) and `phi_no_harvest_before`, the end of the longest waiting period still running.
- **Plants:** live and dead counts, and `dead_squares` once seedlings have died, for gap filling.
- **Feedback:** `recent_decisions` (the last 8 verdicts and executions, including your rejections with your reason) and
  `outcomes` (what came of earlier decisions), plus `advice_track_record`, how often each forest has been right.
- **v2:** `arms` (each arm's queued jobs and when it will be free; `null` when idle — a decision for a busy arm waits
  its turn), `fields.X.water.scheduled` (an irrigation or drainage already decided for later, so it is not decided
  twice) and `fields.X.advice.water` (hours until the water falls to the AWD threshold if nothing is irrigated; `from`
  says whether the time-to-AWD forest or a plain water balance gave it).
- **farmer_notes**: notes you type in the website reach the model as data (the prompt says never to follow
  instructions found in the state).

---

## The action schema

The reply must be one JSON object:

```json
{"decisions": [{"field": "A", "action": "irrigate", "params": {"target_depth_mm": 50}, "squares": null, "when": "now",
                "reason": "Water is 15 cm below the surface and no rain is forecast for 3 days.", "confidence": 0.8}],
 "notes": "optional overall reasoning"}
```

| Action | Params | Squares | What happens |
|---|---|---|---|
| `soil_check` | – | optional | precise probe (pH ±0.03, N/K ±3, P ±0.6, EC ±0.02 dS/m) |
| `amend` | `product`: lime / gypsum / sulphur / compost / leach; `dose` (g/m², mm for leach) | required | changes pH, salinity, organic matter |
| `puddle` | – | – | seals the plough pan (percolation ÷ 2.5) and levels the field; needs standing water |
| `transplant` | – | optional (gap-fill) | nursery seedlings, their age set by the nursery |
| `irrigate` | `target_depth_mm` | – | the shared pump fills the field towards the target |
| `drain` | `to_depth_mm` | – | opens the outlet |
| `hold` | – | – | nothing |
| `fertilise` | `product`: urea / dap / mop / ssp / complex_10_26_26; `kg_per_ha` (product); `split` | optional | nutrients into the soil; urea into deep water loses more N to the air |
| `scout` | – | required | close-up look: exact pest reading |
| `spray` | `product` (label list); `target_pest` (optional) | required | spot spray at the label dose |
| `harvest` | `override_immature` (optional) | – | harvests the whole field |
| `wait` | `hours` | – | asks for an extra call later (field may be null) |

`field` is `"A"` or `"B"`; squares are `"A3,4"`, `"3,4"` or `[3, 4]` (row, column, 1-based); `when` is `"now"`,
`"HH:MM"` (the next time the clock shows it) or `"YYYY-MM-DDTHH:MM"`, at most 48 hours ahead, rounded to the hour.
The full JSON Schema is `components.schemas.DecisionAPIReply` in `/api/openapi.json`. Non-finite numbers, unknown keys,
empty square lists and duplicated squares are rejected or removed.

---

## Guardrails

Every decision, from the API, the mock, the built-in manager or a fallback, passes the same checks
(`decisions/guardrails.py`, numbers in `decisions/rules.py`):

| Rule | Verdict |
|---|---|
| Sprays only with products on the paddy label list, for the pests on their label; the dose is always the label dose | rejected |
| Pre-harvest interval: the product's rice waiting period must end before the expected harvest; no harvest while any interval runs | rejected |
| No spray with ≥ 2 mm of rain forecast in the 6 h after it, wind above 4 m/s, or outside 05:30–20:30 | rejected |
| Bee-toxic products (imidacloprid) on a flowering crop only at 05:30–07:00 or 17:30–19:30, and only against severity ≥ 0.25 | rejected |
| Same IRAC group on the same square within 14 days | warning |
| More than 4 sprays on a square in a season (warning), more than 8 (rejected) | warning / rejected |
| Fertiliser per application at most urea 130, DAP 250, MOP 150, SSP 400, complex 300 kg/ha | rejected |
| Seasonal nitrogen at most 150 kg N/ha on any square (warning above 120) | rejected / warning |
| Urea or DAP into more than 50 mm of water; nitrogen after flowering | warning |
| Amendments per application at most lime 400, gypsum 500, sulphur 100, compost 3,000 g/m², leach 80 mm; twice that per season per square; leaching may not overflow the bund | rejected |
| Irrigation target between −100 and 100 mm (the bund is 150 mm); no irrigation from 90 % progress (drain-down for harvest) | rejected |
| Puddling needs ≥ 20 mm of standing water and never after transplanting | rejected |
| Transplant only into a puddled field, once, before day 75; squares whose seedlings died may be gap-filled within 15 days | rejected |
| Transplanting into > 50 mm of water, onto a dried-out field, or seedlings older than 30 days | warning |
| Harvest only from 96 % progress, unless `override_immature` (then a warning); wet grain or standing water at harvest | rejected / warning |
| At most 12 decisions per reply; contradicting decisions (irrigate and drain the same field at the same time) or duplicates | rejected |

The label list is **simulated**, modelled on India's registrations for rice; check a real product's current label
before any real use.

| Product | Labelled on rice for | Label dose | Counted per m² sprayed | Waiting period |
|---|---|---|---|---|
| chlorantraniliprole 18.5 % SC | yellow stem borer, leaf folder | 150 ml/ha (30 g a.i./ha) | 3.0 mg a.i. | 47 days |
| pymetrozine 50 % WG | brown planthopper | 300 g/ha (150 g a.i./ha) | 15 mg a.i. | 19 days |
| imidacloprid 17.8 % SL | brown planthopper | 100–125 ml/ha (20–25 g a.i./ha) | 2.5 mg a.i. | 40 days |
| azadirachtin 0.15 % EC (neem) | brown planthopper, leaf folder, stem borer | 1.5–2.5 l/ha (2.25–3.75 g a.i./ha) | 0.375 mg a.i. | 5 days |
| Bacillus thuringiensis var. kurstaki | stem borer, leaf folder | 1.5 kg/ha | 0.15 g of product (a microbial: reported as kg of product, not in the a.i. total) | none stated (0) |

The insecticide totals in the results count the chemical and botanical active ingredients at the label dose (the
upper end of a range); Bt is reported separately as kg of product, because grams of a microbial "active ingredient"
are not comparable with grams of a synthetic one.

---

## The decision API client

`backend/paddy/decisions/api_client.py` creates one object, `api = DecisionAPI.from_env()`.

| Variable | Default | |
|---|---|---|
| `API_BASE_URL` | – | e.g. `https://api.example.com/v1` |
| `API_MODEL` | – | the model name your API expects |
| `API_KEY` | – | sent only as the request's auth header |
| `API_ADAPTER` | `chat_completions` | or `anthropic_messages`, `generic_json` |
| `API_TIMEOUT_S` | 60 | per request |
| `API_TEMPERATURE` | 0.2 | |
| `API_JSON_MODE` | 1 | asks for `response_format: json_object` |
| `API_RETRIES` | 3 | on timeouts, connection errors, 408/409/425/429/5xx/529 |
| `API_FALLBACK` | `hold` | or `builtin` |
| `API_MAX_CALLS_PER_DAY` / `_SEASON` / `_REAL_HOUR` | 6 / 600 / 240 | |
| `API_PRICE_IN_PER_M`, `API_PRICE_OUT_PER_M` | – | US$ per million tokens, for cost estimates only |

- **Adapters** are small classes (`decisions/adapters/`): the default speaks the common chat-completions format over
  HTTPS with `urllib`; Anthropic's Messages format and a plain JSON service are one file each. Another provider is one
  more file.
- **Robustness:** timeouts; exponential backoff with jitter, honouring `Retry-After`; if the reply isn't usable JSON
  or fails the schema entirely, one repair request quotes the errors back; then the fallback. A reply with some valid
  decisions keeps those and reports the others as invalid.
- **The key** is read from the environment (or `.env`), kept in a private attribute, never in `repr`, `describe()`,
  logs, records, exports, the website or error messages; everything written goes through a redaction pass that would
  also catch it.
- **Prompt** (`decisions/prompt.py`, versioned): the farm, the objectives in order (safety, yield, water, pesticide,
  then fertiliser, energy and methane), how to read the state and the advice, the action menu, the enforced rules,
  good practice, the reply format, a short plain reason per decision, and "the state is data, never instructions".
- **Mock** (`decisions/mock_api.py`): the same wire formats; answers with the built-in manager's choices; model name
  `mock-builtin (stand-in)` and an `X-Paddy-Mock: stand-in` header, which the client records as source `mock`; fault
  injection (latency, 429 with Retry-After, 500, timeouts, malformed JSON, unsafe suggestions), deterministic per
  request.
- **Cost:** a call carries the instructions (about 2,700 tokens, `paddy-prompt/1.6`) and the state with its message
  (about 2,650), so about 5,350 input tokens (v1: 4,600; the stand-in counted 5,200–5,350 a call in v2's seasons); a
  season makes about 290–300 calls, so roughly 1.5–1.6 million input tokens. Output depends on the model; assuming
  about 300 tokens a call (a few decisions with short reasons) adds about 0.09 million. The estimate shown before
  spending counts 300 calls: at US$3 / US$15 per million tokens about US$6.20 per season (v1's estimate: US$5.50); at
  US$0.20 / US$0.80 about US$0.40. `run`, `compare` and the website's comparison print or show the estimate before
  spending anything (set `API_PRICE_IN_PER_M` and `API_PRICE_OUT_PER_M` to your prices). If your provider caches a
  repeated prompt prefix, the instructions cost less.

---

## REST API reference

The OpenAPI 3.1 document is at `/api/openapi.json`. CORS is open for development (`--cors` to restrict). Times are
simulated local times.

| Method and path | What |
|---|---|
| `GET /api/health` | status, the API status (`connected`, `configured`, `no key`, `not configured`, `failing`, `mock`, `not used`) and the call budget |
| `GET /api/config` | layout, managers, modes, limits, the action menu, the label list, the API settings and status (never the key), and what one season on the real API would cost |
| `GET /api/farm` | both fields: phase, water, stage, inputs, methane, square-by-square states, daily history, the conventional run alongside |
| `GET /api/fields/{A\|B}` | one field square by square: water over it, crop, health, soil readings and code, pest, risk, establishment |
| `GET /api/fields/{A\|B}/squares/{r},{c}` | one square's history at every 06:00 and 18:00 |
| `GET /api/arms` | v2: the four arms now — their build, the frame layout, where each is, its queued and current jobs, busy until, season totals, and the keyframes of the last hour of arm work |
| `GET /api/weather` | station, 3-day forecast, daily rain, events |
| `GET /api/decisions?field=&status=&action=&since=` | decision records |
| `GET /api/decisions/{id}` | one decision with its call: state sent, raw reply, validation, latency, tokens |
| `POST /api/decisions/{id}/approve` | carry out a decision waiting for approval |
| `POST /api/decisions/{id}/reject` `{reason}` | reject it; the reason goes back to the decision maker |
| `POST /api/sim/start`, `/pause` | run or stop the clock |
| `POST /api/sim/step` `{hours}` | advance now |
| `POST /api/sim/reset` `{seed, weatherYear, season, manager, mode}` | new season |
| `POST /api/sim/speed` `{x, work}` | simulated hours per real second; v2: `work`, simulated seconds per real second while an arm works (1–3,600; default 30) |
| `POST /api/events` `{kind: pest\|dry\|storm\|cloudy, day, field, pest}` | an event now or on a later day |
| `POST /api/observations` | real-farm mode: a pest seen on a square, a water reading (replaces the tube for 6 h), a soil reading (replaces the probe), or a note for the decision maker |
| `POST /api/compare` `{managers, seasons, confirm_real_api}`, `GET /api/compare` | the current case under several decision makers, in the background; paired differences |
| `GET /api/models`, `GET /api/audit` | the forests and the checks of their advice |
| `GET /api/runs`, `GET /api/runs/{id}/export?format=json\|csv` | runs kept in memory; full export or the decision table |
| `GET /api/stream` | server-sent events: `status`, `tick`, `call`, `decision`, `event`, `finished`, `baseline`, `compare`, and (v2) `arms` (each hour's arm keyframes) |

---

## The digital twin

`backend/paddy/sim/twin.py`, started from the Python port's code (`core/` is copied unchanged) and trimmed to paddy
on two fields:

- **Layout.** Field A, a bund, field B; each `ROWS × COLS` 1 m² squares (`config.py`: one value each, so "one 6 × 6 plot
  split in two" is `COLS = 3`). One water level per field (a bunded basin); soil, plants, pests and readings per square;
  each square sits a few mm above or below the field's mean surface, so water depth differs a little across it.
- **Water.** Rain, irrigation from one shared pump, evapotranspiration (FAO-56 crop coefficients from the library),
  percolation through the plough pan that rises with the ponded head, lateral seepage through the bunds, drainage
  through the outlet, overflow over the bund. Below the surface the perched water table falls with the soil's
  specific yield; percolation stops by the time it is 10 cm down. Before puddling, percolation is 2.5 times higher.
- **Crop.** The original thermal-time development (progress 0 at transplanting, 1 at maturity); water stress once the
  water table is more than 15 cm down, or as soon as the field falls dry from a week before flowering to a week after
  it (progress 0.55–0.75), and none once the grain is maturing (from 0.9); nutrient, temperature and heat factors,
  pest damage, shattering when left too long, the original yield formula; establishment with the practice factors
  under [Numbers and their sources](#numbers-and-their-sources).
- **Pests and sprays.** The original pest dynamics and camera detection model for the three paddy pests (yellow stem
  borer, brown planthopper, leaf folder), spray residue, wash-off and drift, the original spray-efficacy formula.
- **Sensors.** Camera passes every two hours by day, a daily soil scan, a probe on request, a water tube per field
  (±3 mm, reads to −300 mm), the weather station and rain gauge, forecasts as issued.
- **Inputs and outputs.** Water (m³, mm, pump energy), N-P-K, insecticide active ingredient (Bt as kg of product)
  and area sprayed, methane by IPCC Tier 1, yield.
- **Arms (v2).** Every decision that needs a machine is done by one of four arms, square by square, and each square
  changes at the minute the arm finishes it ([The arms](#the-arms)).
- **Deterministic:** the same seed, weather year and decisions give the same results; a recorded run replays exactly
  from its recorded replies (with the arms too).

What v1's validation campaign changed in the twin, and why, is listed under
[What v1's validation found](#what-v1s-validation-found-and-what-changed-v1); v2's changes are in the
[v2 change log](#v2-change-log).

---

## The 3D farm

The Fields view opens on a 3D model of the two paddies, drawn with the playground's own WebGL2 renderer
(`agri-sim/src/ui/gl.js`, `geometry.js` and its scene, copied into `frontend/src/three/`; no three.js) inside a React
component (`components/Farm3D.jsx`). Everything it shows is a pure function of what the twin recorded
(`three/view.js`): the 06:00/18:00 frames, the arms' keyframes and each decision's arm plan, and the hourly weather,
true water level and power. The page keeps one clock; the view asks "what was true at this second" and draws it.

**What is on it.** The two basins with their bunds, the shared pump house and supply pipe along the north side, each
field's inlet gate (north) and outlet gate (south, into the drain channel), the cube sensor frame with its rails, the
service bay (seedling magazine, fertiliser store, spray mixing station, harvest chute, four docks), the solar panels,
battery and biogas unit. Per square:

- *water* at the field's true level, drawn at its true height over the surveyed square heights (deeper where a square
  sits low); the field's water tube stands on its board (−300 … +150 mm, true scale) with the surface marked in black
  and the safe-AWD line (−150 mm) in red; its column turns amber at the line that applies — −150 mm, or the surface
  from about a week before flowering to a week after it — and its label says so; before transplanting and from the
  drain-down on no line applies;
- *soil* that darkens when wet and pales and cracks as the water table falls (wet above −20 mm, cracked from
  −150 mm, fully cracked by −300 mm — a display rule chosen to match the AWD line);
- *rice* by stage, 9 hills to a square (4 on phones; a real square holds about 50): seedling tufts at least 20 cm tall
  at transplanting, tillers, panicles from progress 0.48, golden from 0.72 to 1.0, stubble after the harvest arm
  passes; dead squares stay bare until gap-filled;
- pest markers where the cameras have seen a pest, spray mist, rain, day and night (the sun from the farm's latitude);
- the scan bar on the frame at every camera hour (a 120 s sweep) and the soil scan at 06:00.

**Overlays** (the same colours and legends as the plan view, drawn unlit so they match): water depth, crop stage,
soil code, pest risk, establishment chance, plant health; "As it looks" turns them off.

**The four arms** ride the frame and do exactly what each decision's recorded plan says, at the recorded seconds:
the planting arm (transplant, gap-fill, soil probe), the watering arm (inlet and outlet gates, amendments, fertiliser),
the pest arm (scouting, spraying) and the larger harvest arm. The trolley moves along the route's stops, the mast
lowers to the stop's shoulder height, the elbow reaches each square (two-link inverse kinematics within the joint
limits) and the tool works at the task's height. A square changes when the arm finishes it (a transplanted seedling
appears, the harvested crop turns to stubble), a gate when the arm sets it. Between frames the squares are the frame
plus the arm events up to that second. While an arm is working the clock slows to a watchable rate, chosen next to the
layers (10×, 30×, 60×, 240× or no slow-down): on the replay page the page's own clock, 60× by default, skipping quiet
hours to the next arm start; on the live site the server's clock, 30× by default and at most 45 s of real time per
simulated hour (`POST /api/sim/speed {"x": …, "work": …}`). With the slow-down a whole season through the stand-in takes
about 7 minutes on the live site (v1: 13 s); "no slow-down" brings it back to the speed setting alone.

**Inspecting.** Click a square (its readings, crop, water depth, pest, the decisions that touched it) or an arm (its
role, what it is doing, its current job, busy until, queued jobs, season totals). Camera presets: overview, field A,
field B, and follow each arm; orbit (drag), pan (right-drag or Shift-drag), zoom (wheel or pinch), and keyboard
(arrows, WASD, +/−, PageUp/PageDown) when the view has focus. A 2D plan view is one click away (3D / Plan), and the
choice is remembered. Clicking a decision in the feed highlights its arm and its squares; in approve mode a pending
decision shows its planned path (a dashed route and a ghost arm) before you approve it.

**Quality.** Desktop: full quality with shadows and adaptive resolution (frames over 45 ms for 2 s step the resolution
down by 15 %, to half at most; under 22 ms for 5 s step it back up). A coarse pointer, a window under 700 px or a
machine with two cores or fewer gets the light render (4 hills per square, a coarser soil texture, no shadows, at most
1.25 device pixels per CSS pixel, half the particles). `prefers-reduced-motion` stops sway, blinking, bobbing and
camera easing and keeps only the spray mist. Without WebGL2 the page shows the plan view with a note. The frame rate
is shown on the view (and reported below for this machine, which renders WebGL in software).

**Checks.** `tests/test_scene_state.py` + `tests/e2e/scene_state.mjs`: the 3D view's square states (stage, water,
pumping/draining) are hashed at every frame time of a recorded season and compared with the twin's own state — 275
frames, 146 square changes, 39 gate events, 0 mismatches, and nothing changes before the arm gets there.
`tests/e2e/farm3d_ui.js` (Playwright): every preset on desktop and phone, light and dark (screenshots in
`tests/results/screens3d/`, each looked at; a selection in `docs/screens/`); a square and an arm inspected; a feed
entry highlighting its arm; the approve-mode planned path and the arm setting off after approval; the WebGL2-missing
fallback; reduced motion; the live arm slow-down control; no console errors. `tests/e2e/farm3d_controls.js`: orbit,
pan, zoom and tilt with the mouse (drag, right-drag, Shift-drag, wheel), the keyboard (arrows, WASD, Shift+arrows, +/−,
PageUp/PageDown) and touch (one-finger orbit, pinch, two-finger pan, as real multi-touch input on a phone page), each
judged by what it does to the picture, and the page itself never scrolls or zooms.

**Frame rates** (this build machine renders WebGL in software — SwiftShader on two CPU cores — so a real GPU is far
faster): median frame rates over ten one-second readings after a 12 s warm-up (`tests/e2e/farm3d_fps.js`): desktop 1440 × 1000 with the render this machine picks for itself (the light one, two cores) **6 fps**, with the full render forced 2 fps (1280 × 720: 2 fps); a 400 px phone **7 fps**. The scene is 88,000 triangles in 31 draw calls (light) or 153,000 (full, with sun shadows): little for any GPU, so a machine with one should run it far faster, but that was not measured here. The camera eases and the arms move by time, not by frame, so a low frame rate drops frames rather than slowing anything down.

| | |
|---|---|
| ![The overview of the recorded stand-in season on day 60 at 18:00: the frame, both fields at panicle initiation, the four arms parked at the service bay, the solar array, battery and biogas unit](docs/screens/farm3d-overview.png) | ![Field A up close: rice at panicle initiation over 13 mm of water, the outlet gate and the water tube on its board](docs/screens/farm3d-field-a.png) |
| The overview (day 60, 18:00): frame, fields, arms parked at the bay, power plant | Field A: rice at panicle initiation, 13 mm of water, the outlet and the water tube |
| ![Approve mode on the live site: a pending soil check's planned path — the planting arm's ghost and the squares it will probe — drawn before approval](docs/screens/farm3d-approve-preview.png) | ![After approval the planting arm sets off along that path (day 0, 06:00)](docs/screens/farm3d-arm-at-work.png) |
| Approve mode: a pending decision's planned path, before approval | … and the arm carrying it out after approval |
| ![A decision shown on the farm: its arm and squares highlighted, dark theme](docs/screens/farm3d-highlight-dark.png) | ![The 3D farm on a 400 px phone, dark theme, light render](docs/screens/farm3d-phone-dark.png) |
| "Show on the farm": a decision's arm and squares, dark theme | A 400 px phone, dark theme (light render) |

Without WebGL 2 the page shows the plan with a note (`docs/screens/farm3d-no-webgl2.png`). Every preset on desktop and
phone, light and dark, is in `tests/results/screens3d/` (28 preset views, 4 highlights, 2 approve-mode shots and the
fallback), and each was looked at.

---

## The arms

v2 carries out every decision with four arms on a cube sensor frame over both fields (`backend/paddy/robots/`), copied
from the playground's Python port (`planner.py`, `arms.py`, `energy.py`, unchanged algorithms) and laid out for two
6 × 6 basins (`layout.py`).

**Who does what** (the arm is fixed by the action):

| Arm | Does | Speed | Reach | Carries |
|---|---|---:|---:|---|
| 1 Planting | transplant, gap-fill, soil probe, puddling | 1.2 m/s | 0.8 m | 36 seedlings (refill 18 s at the magazine) |
| 2 Watering | inlet and outlet gates, amendments, fertiliser | 1.2 m/s | 0.8 m | 25 kg hopper (refill 18 s at the store) |
| 3 Pest | scouting, spraying | 1.3 m/s | 0.8 m | 5 L tank (refill 20 s at the mixing station) |
| 4 Harvest | harvesting | 0.8 m/s | 1.15 m | 40 kg bin (unload 15 s at the chute) |

The playground's arm definitions (speed, acceleration, keep-out radius, reach, mast and reach times, capacities,
refill times, power by phase) are kept; three phases are paddy-only and their durations and powers are this
project's estimates: turning a gate and setting its float (6 s, 120 W), spreading granules (3 s, 100 W) and puddling
with a rotary tool (8 s, 450 W).

**Plans.** Every accepted decision that needs a machine becomes one job for its arm; jobs for an arm run strictly in
the order they were submitted. When an arm starts a job the planner groups its squares into stops (every square the arm
reaches from one trolley position), orders the stops (nearest neighbour, 2-opt, Or-opt) and places refill or unload
trips (Prins' split). The traffic manager drives the trolleys (goal reservation, prioritised velocity sampling with a
2.5 s look-ahead, make-way, detours) and keeps 0.25 m between the arms' keep-out circles (radius 0.85 m; 1.3 m for
the harvest arm); a safety monitor counts every overlap of two keep-out circles (a collision) and every approach within
half that margin (a near miss), and records the smallest clearance of the season. The
plan (route, stops with shoulder heights, every task with its planned and actual times and tool height) is stored in
the decision record and in the frames; the 3D view animates exactly that.

**Time.** The arm system steps 0.25 s while anything moves and from phase change to phase change (at most 5 s) while
the arms only work in place, as the playground's world loop does. It runs in lock-step with the hourly twin: right
after the decision point at the top of an hour it runs through that hour, and each finished square is applied to the
twin at its own finishing time, before the hour's physics — a 12-square spray is applied square by square, each with
the weather at its minute. A job that waits more than an hour for its arm is checked by the guardrails again before
it starts. The state sent to the API says which arms are busy, until when, and what is queued.

**Energy.** The playground's power plant sized for this frame (`energy.size_for` on the 78 m² frame: 1.5 kWp of
panels, a 6 kWh battery, the biogas generator fed by crop residue, the grid as last resort) supplies the pump, the
arms (moving and by phase), the frame's sensors and scan bar (one third duty at each camera hour), the edge computer
and the night lights. Per season and per arm: busy time, travel, energy, refills, and the closest approach between
arms.

**Checks.** `tests/test_arms.py`: for every one of the 72 squares, every arm that may work it reaches it from a stop
of its own plan within reach and joint limits (elbow 25–172°, mast 0.85 m at its shortest); every plan in a season
collision-free (zero safety-monitor violations); jobs in submission order; determinism (two runs, identical
keyframes). `tests/test_engine_arms.py`: decisions → jobs → twin changes at the arm's times; preview without side
effects; exact replay with the arms.

**Over a season** (means per season on the live set; the closest approach is the smallest clearance between two arms'
keep-out circles over the season):

| Mean per season | built-in (500) | conventional (500) | API, no advice (500) | cont. flooding (500) |
|---|---:|---:|---:|---:|
| Arm jobs | 176 | 133 | 157 | 243 |
| Busy time, h (the four arms added up) | 3.5 | 3.6 | 3.3 | 4.0 |
| … planting arm busy, h | 0.61 | 0.44 | 0.61 | 0.61 |
| … watering arm busy, h | 1.19 | 1.63 | 1.17 | 1.72 |
| … pest arm busy, h | 1.06 | 0.88 | 0.88 | 1.05 |
| … harvest arm busy, h | 0.66 | 0.66 | 0.66 | 0.66 |
| Travel, km (the four arms added up) | 4.03 | 2.66 | 3.63 | 5.19 |
| … planting arm travel, m | 168 | 104 | 168 | 168 |
| … watering arm travel, m | 1,063 | 2,138 | 1,049 | 2,230 |
| … pest arm travel, m | 2,632 | 252 | 2,246 | 2,620 |
| … harvest arm travel, m | 171 | 169 | 172 | 171 |
| Arm energy, kWh (moving, mast, tools and standby; the four arms) | 1.10 | 1.12 | 1.05 | 1.24 |
| … of it during the jobs themselves (each job from its start to its end) | 0.75 | 0.77 | 0.72 | 0.81 |
| … planting arm refills / unloads | 1.9 | 2.0 | 1.9 | 1.9 |
| … watering arm refills / unloads | 0.2 | 0.0 | 0.0 | 0.2 |
| … pest arm refills / unloads | 0.1 | 3.3 | 0.0 | 0.1 |
| … harvest arm refills / unloads | 2.0 | 2.0 | 2.0 | 2.0 |
| Detours / made way | 153 | 32 | 141 | 159 |
| Farm load, kWh (pump, arms, sensors, computer) | 433 | 447 | 433 | 444 |
| … pump | 28.0 | 39.0 | 28.1 | 38.9 |
| … sensors and the frame's scan bar | 177 | 178 | 177 | 177 |
| … edge computer | 227 | 229 | 227 | 227 |
| … arms | 1.10 | 1.12 | 1.05 | 1.24 |
| PV, kWh | 807 | 811 | 805 | 807 |
| … not used (battery full) | 347 | 337 | 347 | 334 |
| Grid, kWh | 0.1 | 0.1 | 0.1 | 0.1 |
| Biogas generator, kWh | 2.9 | 2.1 | 3.3 | 2.1 |
| Self-sufficiency | 100.0 % | 100.0 % | 100.0 % | 100.0 % |
| Closest approach between arms, m (lowest season – median) | 0.25 – 0.36 | 0.25 – 0.47 | 0.25 – 0.38 | 0.25 – 0.29 |
| Collisions / near misses (all seasons) | 0 / 0 | 0 / 0 | 0 / 0 | 0 / 0 |

Zero collisions and zero near misses in 2,000 seasons; the closest approach is the traffic manager's 0.25 m margin
between keep-out circles, reached in some season under every decision maker (`tools/arms_summary.py runs/datasets/live --set L`).
The arms use about 1.1 kWh a season, 0.3 % of the farm's load: the edge computer and the sensors take most of it, and
the pump 28–39 kWh.

---

## The model round

Every check of the forests' advice reaches 95 % on the new test set, pooled, for each decision maker and in each
season, wherever the check has at least 20 cases — including the two checks v2 adds: the time to AWD (the
time-to-stress forest's paddy job) and the planned spray (now a target). On the new live set every check does too,
except one group: v1's yield forecast for the conventional manager's fields, 94.5 % — an over-forecast that a simple
calibration would lift to about 98 %, explained under
[Below target](#below-target-the-yield-forecast-under-conventional-practice-live-set). How each number was produced is
in `tools/models/RUNBOOK.md`; every command there is resumable.

### Definitions

`docs/DEFINITIONS.md` and `docs/DEFINITIONS-paddy.md` are unchanged. `docs/DEFINITIONS-paddy-v2.md` was written on
28 October before any v2 result, its SHA-256 recorded the same morning in `tools/checksums/definitions-v2.sha256`
(`e083695b…`, unchanged at the end). It adds what the arms change (each square sprayed at the minute the arm reaches it;
planting checked when the job is handed to the arm; the soil check on the probe reading itself — v1 scored a cached
daily-scan value, finding F1), the time-to-AWD check (truth under 72 h, within ± 6 h or ± 20 %, whichever is larger,
using the weather that came), the planned spray as a target with how its ceiling is measured, the sets, and the
targets.

#### The sets

| Set | Purpose | Seeds | Weather years | Seasons |
|---|---|---|---|---:|
| G | training (one decision maker per case, in turn) | 8,100,000–8,101,999 | 10,001–12,000 | 2,000 |
| T | test: compares candidates; the before (v1 forests) and after (the pack) tables | 8,200,000–8,200,199 | 20,001–20,200 | 200 × 4, run twice (+ 200 × 4 in the harder forecast mode) |
| L | live: run once, after the freeze | 8,300,000–8,300,499 | 30,001–30,500 | 500 × 4 |
| H | A/B of the decision changes (built-in and continuous flooding; v1 forests vs the pack) | 8,400,000–8,400,649 | 40,001–40,650 | 650 × 2 × 2 |
| W | HTTP seasons through the stand-in (final checks) | 8,500,000–8,500,049 | 45,001–45,050 | 50 × 5 |
| v1 campaign cases | v1 farm vs v2 farm, season by season (never used for models) | 7,100,000–7,100,199 | 3,001–3,200 | 200 × 4 |
| K | climatology for the planned-spray ceiling (weather only) | — | 90,001–92,000 | 2,000 weather years |

Kharif for even *i*, rabi for odd *i*; half the cases draw one to three events (pest outbreak, heavy storm, dry spell,
overcast week) from a hash of their seed. `python -m paddy sets` checks every pair of sets and every v1 run for a
shared seed or weather year: 45 pairs, 0 shared.

The four decision makers are the brief's: the **built-in** manager (which is also what the API does through the
stand-in: it makes exactly the built-in decisions, checked in the tests and the HTTP seasons), **conventional**
practice, the **API without the forests' advice** through the stand-in (`api-no-advice`, "API, no advice" in the
tables), and the **continuous-flooding** ablation (`builtin-cf`).

### What the farm now knows better, and how it found out

**Time to AWD: 41.1 % → 99.2 % (test set).** v1 had no model for it: the farm's water balance on the forecast scored 41.1 % on the
test set (57,908 checks). Re-simulating each check from the field's true state, with the weather drawn from what the
forecasts allowed at that moment, scored about 99 % — so the misses were not luck. Three things were wrong, each found
by recomputing the checks with one part of the truth put back:

1. *The level.* One tube reading carries ± 3 mm of noise (σ). Above the surface the water falls about half a millimetre
   an hour, so 3 mm is about 6 h — the whole tolerance of a short check. The farm now fits its own water balance (its
   gauge, the station's ET₀, the pump's and outlet's metered water, percolation from the soil readings, the crop's
   water use by stage, the surveyed square heights) to the last 48 hourly readings, weighted by e^(−age/24 h), and takes
   the fitted level: error σ 0.3–0.5 mm.
2. *The forecast read at face value.* The hourly forecaster misses a rainy hour with probability 0.08 + 0.01 × lead
   (and then says dry), and calls 15 % of those missed hours 1.5 mm/h; temperatures are off by up to ± (0.3 + 0.04 ×
   lead) °C. Taking the forecast as the weather counts phantom rain and none of the hidden rain, and a shower that
   starts at 14:20 shows up from 15:00. The farm now draws the coming 73 hours from the **exact posterior of the weather
   generator's days given everything it was forecast** (`backend/paddy/sim/wxpost.py`): the generator's wet/dry chain,
   gamma rain amounts, one shower a day with its start and length, temperature noise; the hourly forecaster's misses and
   false alarms; the daily forecaster's chance-of-rain bands; announced events; the rain its own gauge has seen today.
   A rain rate seen in the forecast is matched within ± 2 % and then used exactly. Every true day has positive weight in
   the posterior (1,624 of 1,624 tested) except days inside an event — a dry spell, an overcast week — announced only
   after the forecasts were issued, which the farm cannot know about (0.5 % of the days in the time-to-AWD ceiling run);
   and the farm never sees future weather (a test checks that a night shower at 06:00 reveals neither the day's high
   nor its cloud).
3. *The physics.* The estimate used one crop coefficient for a field; the projection is now the twin's own documented
   water balance (per-square crop coefficient and stress, percolation with head, bund seepage, the crop growing by
   thermal time), run on 256 draws at once (the farm's tabulated version matches `Twin.project_level_many` to 0.0 h on
   every state tested). It is skipped when even a dry run with 25 % more evapotranspiration than forecast cannot reach
   the line within the horizon — then every draw is past 72 h.

The forest is *anchored* on that posterior estimate (the value most of the 256 draws agree on within the check's
tolerance): it predicts the ratio of the truth to it from the draws' spread, the level fit and the v1 inputs, so where
it has nothing to add it returns the posterior estimate. Forests that predicted the hours directly re-learnt the
posterior badly (97.4–98.0 % on the training holdout against 98.9 % for the posterior alone); trained only on the checks
the audit scores (truth under 72 h), the anchored forest adds a little (99.35 % against 99.14 % on the full holdout).

**Planned spray: 66.7 % → 99.4 % (test set).** v1 gave every square of a spray the same number, the spray forest at the planned
hour. Recomputing the test rows with the weather that came at each square's minute and the pest as the farm saw it
scored 99.4 %, so most misses were not luck either. Three causes: the wind of one hourly forecast (the true wind ×
0.8–1.2), the 1.5 mm/h false alarms read as rain, and the top of the hour standing in for the minute the arm reaches each
square. The farm now asks its own arm planner for each square's minute (the dry run approve mode already used), takes
the day's wind from the station (today) or from where all of that day's hourly wind forecasts agree (later days: the
intersection of their ± 20 % ranges, typically ± 0.5 %), fits the days' highs and lows to every hourly temperature
forecast and the station, and draws the rain of the six hours after each minute 128 times from the same posterior; the
kill rule on each draw gives the estimate most draws agree on (± 5 points). A forest anchored on that estimate (a
difference) corrects it from the pest the cameras see, their identification votes and the square's history. The misses
left are mostly sprays where the cameras named the wrong pest, so the product barely worked — luck the farm can't see
when it decides (the arm's close-up look identifies the pest only when it sprays).

**Harvest readiness: 99.4 % → 100.0 %.** The truth is the readiness rule on the crop's progress, the last two days' rain
and the humidity, all of which the farm measures. v1's forest, trained on the playground's crops, approximated the
thresholds and left 6 % of seasons under 95 %. The paddy forest gets the rule's own answer as an input.

**Soil (98.0 %), spray at the arm (99.5 %), planting day (99.5 %), pest risk (99.0 %, balanced 99.1 %), yield
(99.1 %)** were already above 95 % in every group with v1's forests (T, before) and keep them. Two were looked at
further:
- *Soil* is at its ceiling: on the test set the best any prediction from the reading can score is 98.1 % (the rules
  applied to 200 re-draws of the true soil behind each reading; see Ceilings), against v1's forest's 98.0 % — the
  probe's own noise sets the limit. A paddy forest with those re-draws as inputs scored 98.04 % against v1's 97.99 %
  but 0.1 point lower in kharif, so by the rule below v1's forest stays.
- *Spray at the arm*: 502 of the 667 misses on the training set happen with no rain in the forecast at all (showers it
  missed, tracked-age errors); even fixing every miss that had forecast rain would lift the share of seasons at ≥ 95 %
  only from 93.0 % to 95.8 %. The farm's own rule with the false-alarm hours dropped scores the same as v1's forest
  (99.17 % against 99.23 %), so v1's forest stays.

### How the forests were chosen

Two sets do two jobs. **G** (training) is split into whole seasons: the cases with i % 5 = 0 are held out, and the two
new forests were developed on G alone — candidates trained on the other cases and scored on the held-out seasons
exactly as the audit scores them. **T** (test) compares the candidates for the checks v1 already had and gives the
before → after tables. **L** (live) was run once, after the pack was frozen.

The rule for the two new forests was written down at 13:45 on 28 October, before the full training set existed, and
applied mechanically (`tools/models/select_rule.py`): among the posterior estimate alone and the anchored forests (12
trees of depth 10, 20 of depth 14, 30 of depth 16; with and without equal weight per decision maker; for the time to
AWD with and without the never-scored checks), take the highest **minimum** over the four decision makers and two
seasons; within 0.15 points of it, the higher pooled rate; within 0.1 there, the smaller forest. On the full holdout
(1,522 seasons with the v2 inputs; 19,459 scored time-to-AWD checks, 29,979 planned-spray squares):

| Candidate | Time to AWD: pooled | lowest group | nodes | | Planned spray: pooled | lowest group | nodes |
|---|---:|---:|---:|---|---:|---:|---:|
| v1 (water balance / v1 forest) | 42.2 | 39.5 | – | | 75.4 | 48.1 | – |
| posterior estimate alone | 99.14 | 98.31 built-in | 0 | | 99.23 | 95.15 cont. flooding | 0 |
| anchored 12 × d10 (AWD: scored checks only) | **99.35** | **98.67** built-in | 6,884 | | 99.32 | 95.56 cont. flooding | 5,772 |
| … equal weight per decision maker | 99.36 | 98.63 built-in | 7,648 | | 99.23 | 94.67 cont. flooding | 7,348 |
| anchored 20 × d14 (AWD) / 30 × d16 (plan) | 99.25 | 98.52 built-in | 51,156 | | **99.35** | **95.86** cont. flooding | 87,554 |

For soil and harvest a paddy forest replaces v1's only if it is at least as good in every group on T and better pooled:
harvest yes (100.0 % everywhere), soil no (above).

**The pack** (`backend/paddy/core/models/paddy_forests.npz` + `.json`, 0.66 MB, loaded on top of v1's forests, which
still answer every other check): time to AWD (12 trees, 7,386 nodes, 53 inputs), planned spray (30 trees, 101,390
nodes, 37 inputs), harvest readiness (10 trees, 240 nodes) — each trained on every G season with its inputs. SHA-256 in
`docs/models-frozen-v2.txt`, recorded before L was run.

### Results on the test set: before (v1's forests) → after (the pack)

The same 200 cases under each decision maker, run twice: with v1's forests (before) and with the frozen pack (after).
Every check is at or above 95 % in every group; the lowest is the yield forecast under the conventional manager, 96.5 % on 400 cases. The full table with every group's interval and season-bootstrap interval is in `runs/models/T_before_after.md`.

**The three checks the pack changed (test set; before → after, in %)**

| Check | pooled | built-in | conventional | API, no advice | cont. flooding | kharif | rabi |
|---|---:|---:|---:|---:|---:|---:|---:|
| Time to AWD (v2) | 41.1 → **99.2** | 42.0 → **98.9** | 38.3 → **100.0** | 42.1 → **98.9** | 38.4 → **100.0** | 41.1 → **99.0** | 41.0 → **99.3** |
| … after: 95 % interval | 99.1–99.3 | 98.8–99.1 | 100.0–100.0 | 98.7–99.0 | 99.9–100.0 | 98.8–99.1 | 99.2–99.4 |
| … after: cases | 58,436 | 21,809 | 7,684 | 21,286 | 7,657 | 19,108 | 39,328 |
| Spray efficacy, planned (v2 target) | 66.7 → **99.4** | 56.1 → **97.6** | 69.4 → **99.9** | 60.6 → **97.9** | 56.2 → **97.6** | 56.8 → **99.1** | 80.1 → **99.8** |
| … after: 95 % interval | 99.4–99.5 | 97.2–98.0 | 99.9–99.9 | 97.4–98.3 | 97.2–97.9 | 99.0–99.2 | 99.7–99.8 |
| … after: cases | 78,154 | 6,759 | 60,660 | 4,066 | 6,669 | 44,937 | 33,217 |
| Harvest readiness | 99.4 → **100.0** | 99.4 → **100.0** | 99.2 → **100.0** | 99.5 → **100.0** | 99.4 → **100.0** | 99.3 → **100.0** | 99.4 → **100.0** |
| … after: 95 % interval | 100.0–100.0 | 100.0–100.0 | 100.0–100.0 | 100.0–100.0 | 100.0–100.0 | 100.0–100.0 | 100.0–100.0 |
| … after: cases | 621,174 | 158,283 | 151,634 | 152,656 | 158,601 | 350,469 | 270,705 |

**Every other check (v1's forests; the before and after runs give the same rates to 0.1 point)**

| Check | pooled | built-in | conventional | API, no advice | cont. flooding | kharif | rabi | cases (pooled) | 95 % interval (pooled) |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Soil suitability | 98.0 | 98.0 | – | 98.0 | 98.0 | 98.0 | 97.9 | 43,200 | 97.8–98.1 |
| Planting day (transplanted) | 99.5 | 99.7 | 99.3 | 99.5 | 99.7 | 99.3 | 99.6 | 177,340 | 99.4–99.5 |
| Pest risk (7 days) | 99.0 | 99.5 | 98.2 | 98.8 | 99.4 | 98.9 | 99.2 | 693,829 | 99.0–99.1 |
| … balanced accuracy | 99.1 | 99.7 | 98.9 | 98.8 | 99.7 | 99.0 | 99.4 | | |
| Yield forecast | 99.1 | 100.0 | 96.5 | 100.0 | 100.0 | 98.6 | 99.6 | 1,600 | 98.5–99.5 |
| Spray efficacy (at the arm) | 99.5 | 98.5 | 99.8 | 98.2 | 98.5 | 99.4 | 99.7 | 78,154 | 99.5–99.6 |

### Results on the live set (run once, after the freeze)

The 500 cases of set L under each decision maker (2,000 seasons), run once after the pack was frozen (`docs/models-frozen-v2.txt`; `python tools/models/freeze.py --check` confirms the pack and every file that decides its numbers are unchanged since). Full table with every group's season-bootstrap interval: `runs/models/L_live.md`.

**The three checks the pack changed (live set, in %)**

| Check | pooled | built-in | conventional | API, no advice | cont. flooding | kharif | rabi |
|---|---:|---:|---:|---:|---:|---:|---:|
| Time to AWD (v2) | **99.2** | **99.0** | **99.9** | **99.0** | **99.9** | **99.0** | **99.3** |
| … 95 % interval | 99.2–99.3 | 98.9–99.0 | 99.9–100.0 | 98.9–99.1 | 99.9–100.0 | 98.9–99.1 | 99.3–99.4 |
| … cases | 147,072 | 54,905 | 19,455 | 53,405 | 19,307 | 46,129 | 100,943 |
| Spray efficacy, planned (v2 target) | **99.2** | **97.2** | **99.8** | **96.9** | **97.2** | **99.0** | **99.4** |
| … 95 % interval | 99.1–99.2 | 97.0–97.5 | 99.7–99.8 | 96.6–97.3 | 96.9–97.4 | 98.9–99.0 | 99.4–99.5 |
| … cases | 194,923 | 16,555 | 151,596 | 10,277 | 16,495 | 110,068 | 84,855 |
| Harvest readiness | **100.0** | **100.0** | **100.0** | **100.0** | **100.0** | **100.0** | **100.0** |
| … 95 % interval | 100.0–100.0 | 100.0–100.0 | 100.0–100.0 | 100.0–100.0 | 100.0–100.0 | 100.0–100.0 | 100.0–100.0 |
| … cases | 1,550,628 | 397,637 | 373,992 | 381,364 | 397,635 | 869,736 | 680,892 |

**Every other check (v1's forests; live set, in %)**

| Check | pooled | built-in | conventional | API, no advice | cont. flooding | kharif | rabi | cases (pooled) | 95 % interval (pooled) |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Soil suitability | 98.1 | 98.1 | – | 98.1 | 98.1 | 98.3 | 97.9 | 108,216 | 98.0–98.2 |
| Planting day (transplanted) | 99.2 | 99.2 | 99.3 | 99.2 | 99.2 | 99.0 | 99.5 | 448,036 | 99.2–99.3 |
| Pest risk (7 days) | 99.0 | 99.4 | 98.2 | 98.7 | 99.4 | 99.0 | 99.0 | 1,739,046 | 99.0–99.0 |
| … balanced accuracy | 99.1 | 99.6 | 98.9 | 98.7 | 99.6 | 99.0 | 99.4 | | |
| Yield forecast | 98.5 | 99.8 | 94.5 | 99.8 | 99.8 | 97.7 | 99.2 | 4,000 | 98.0–98.8 |
| Spray efficacy (at the arm) | 99.4 | 98.2 | 99.8 | 98.1 | 98.1 | 99.3 | 99.6 | 194,923 | 99.4–99.4 |

### Below target: the yield forecast under conventional practice (live set)

On the live set the yield forecast scores **94.5 %** for the conventional manager's fields (1,000 field-seasons; Wilson
92.9–95.8 %): the one group of any check below 95 % on either set (on the test set it was 96.5 %, 400 field-seasons,
94.2–97.9 %). Pooled (98.5 %), in kharif (97.7 %), in rabi (99.2 %) and for the other three decision makers (99.8 %)
the check passes. It is not luck:

- **The misses are one-sided.** All 55 are over-forecasts: for conventional practice the forecast at transplanting is
  2.8 points too high on average (built-in −0.4, API without advice +0.6, continuous flooding −0.4). v1's yield forest,
  trained on the playground's farms, does not see how much a field loses to what conventional practice skips — no soil
  test and no amendment of acid, alkaline or saline squares, urea into standing water, calendar sprays. Kharif suffers
  more (92.0 %) than rabi (97.0 %).
- **The ceiling is at least 98 %.** The bias was already there before the freeze: +2.7 points on the training set's
  1,000 conventional field-seasons, +1.9 on the test set's 400. One correction learnt on the training set alone
  (2.7 points off the forecast for a conventionally managed field — the farm knows how it is managed) scores
  **98.1 %** (97.1–98.8 %) on the live set; learnt on the test set, 97.6 %. A forecast that the farm could make scores
  that much, so the best possible is at least that, and the forest is more than the 1 point the brief allows for luck
  below it.
- **Why it slipped through.** The rule for the checks v1 already had kept v1's forest wherever it reached 95 % in every
  group on the test set, and the conventional group scored 96.5 % there — but its interval reached down to 94.2 %. A
  rule on the lower end of each group's interval, or one that looked at the training set's bias, would have caught it.
- **What would raise it.** A paddy yield forest (or a calibration of v1's) trained on set G with the farm's management
  as inputs — soil tested and amended or not, how the fertiliser went in, the spray regime — chosen on T and checked on
  a fresh live set. That is left for the next round: the live set is run once and the definitions stay as written, so
  the pack was not changed after this result, and the corrections above are a diagnosis, not a result. No decision
  maker here acts on the yield forecast (it is advice shown to the API), so nothing the farm did depended on it.

### The share of seasons at 95 % or more

**The share of seasons at 95 % or more** (seasons with at least 20 cases of the check; in brackets the share's own season-bootstrap 95 % interval)

| Check | v1 campaign | T before | T after | L (live) | lower end of the pooled rate's season-bootstrap 95 % interval (L (live)) |
|---|---:|---:|---:|---:|---:|
| Soil suitability | 76 % | 90 % (87–92) | 90 % (87–92) | 92 % (91–94) | 98.0 % |
| Planting day (transplanted) | 98–100 % | 99 % (98–100) | 99 % (98–100) | 98 % (97–99) | 99.0 % |
| Pest risk (7 days) | 100 % | 98 % (97–99) | 98 % (97–99) | 97 % (96–98) | 98.9 % |
| Spray efficacy (at the arm) | 92 % | 94 % (92–96) | 94 % (92–96) | 93 % (92–95) | 99.2 % |
| Spray efficacy, planned (v2 target) | 1–10 % | 5 % (3–6) | 91 % (88–93) | 90 % (89–92) | 99.0 % |
| Harvest readiness | 92–94 % | 94 % (92–96) | 100 % (100–100) | 100 % (100–100) | 100.0 % |
| Time to AWD (v2) | – (no model) | 0 % (0–0) | 98 % (97–99) | 98 % (97–99) | 99.2 % |

Soil's share rose from v1's 76 % to 90–92 % because the check is now made on the probe reading itself (finding F1), not because of a new forest: it is at its ceiling (98.1 %), and with about 72 squares a season, three or four borderline readings put a season under 95 %. At the arm, one spray decision covers many squares in the same hour, so a single shower the forecast missed can sink a season (502 of the 667 misses on the training set had no rain in the forecast at all). Pest risk and the planting day reach 95 % in 97–99 % of the new sets' seasons where v1's campaign had 98–100 %: half of the new sets' seasons have events (outbreaks, storms, dry spells, overcast weeks), which v1's campaign cases had none of; on those same cases v2 reaches 95 % in 100 % of seasons for pest risk and 98 % for the planting day (Validation campaign).

### The harder forecast mode (off by default; reported with and without)

`--forecast realistic` swaps the simulated forecaster for the harder one in `core/climate.py` (`FCR`, from the
playground's port; in v1 it was in the code but not exposed): daily rain amounts with long-tailed errors (Student-t,
3 degrees of freedom, on the log scale, growing with the lead time), misses and false alarms; temperatures with
Student-t errors; the hourly forecast is the daily one spread over the climatological timing, so it says nothing about
the hour the rain comes; and the wind comes from the month's climate, not from the weather. The weather itself, the
cases and the pack are unchanged, but the farm's weather posterior still describes the default forecaster, so in this
mode it is mis-specified (see Limits). The same 200 test cases per decision maker (`--tag after-realistic`; default →
**harder mode**, in %):

| Check | pooled | built-in | conventional | API, no advice | cont. flooding | kharif | rabi | realistic: cases, 95 % interval (pooled) | realistic: seasons at ≥ 95 % |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Time to AWD (v2) | 99.2 → **94.1** | 98.9 → **92.9** | 100.0 → **97.6** | 98.9 → **92.8** | 100.0 → **97.6** | 99.0 → **94.6** | 99.3 → **93.9** | 58,400, 93.9–94.3 | 55 % |
| Spray efficacy, planned (v2 target) | 99.4 → **96.7** | 97.6 → **90.9** | 99.9 → **98.1** | 97.9 → **96.0** | 97.6 → **91.0** | 99.1 → **97.3** | 99.8 → **96.0** | 79,169, 96.6–96.9 | 76 % |
| Harvest readiness | 100.0 → **100.0** | 100.0 → **100.0** | 100.0 → **100.0** | 100.0 → **100.0** | 100.0 → **100.0** | 100.0 → **100.0** | 100.0 → **100.0** | 619,518, 100.0–100.0 | 100 % |
| Soil suitability | 98.0 → **98.0** | 98.0 → **98.0** | – | 98.0 → **98.0** | 98.0 → **98.0** | 98.0 → **98.0** | 97.9 → **97.9** | 43,200, 97.8–98.1 | 90 % |
| Planting day (transplanted) | 99.5 → **97.1** | 99.7 → **98.5** | 99.3 → **95.7** | 99.5 → **98.2** | 99.7 → **98.5** | 99.3 → **96.7** | 99.6 → **97.5** | 177,516, 97.0–97.2 | 95 % |
| Pest risk (7 days) | 99.0 → **98.3** | 99.5 → **99.0** | 98.2 → **96.6** | 98.8 → **98.2** | 99.4 → **99.0** | 98.9 → **98.4** | 99.2 → **98.2** | 723,772, 98.3–98.4 | 94 % |
| … balanced accuracy | 99.1 → **98.7** | 99.7 → **99.5** | 98.9 → **98.0** | 98.8 → **98.5** | 99.7 → **99.5** | 99.0 → **98.7** | 99.4 → **99.0** | | |
| Yield forecast | 99.1 → **99.1** | 100.0 → **100.0** | 96.5 → **96.5** | 100.0 → **100.0** | 100.0 → **100.0** | 98.6 → **98.6** | 99.6 → **99.6** | 1,600, 98.5–99.5 | – |
| Spray efficacy (at the arm) | 99.5 → **97.8** | 98.5 → **93.0** | 99.8 → **99.1** | 98.2 → **95.5** | 98.5 → **92.9** | 99.4 → **97.7** | 99.7 → **98.0** | 79,169, 97.7–97.9 | 82 % |

What falls is what reads the forecast: the time to AWD (94.1 % pooled, built-in 92.9 %) and the planned spray (96.7 %,
built-in 90.9 %), and less so v1's spray forest at the arm (97.8 %), the planting day (97.1 %) and pest risk (98.3 %).
Several groups are below 95 % here. The targets are fixed on the sets as the addendum defines them, with the default
forecaster; this mode is an extra the brief allows, reported rather than targeted. What would raise it: a posterior
built on this forecaster's own error model (its t-distributed errors, the hourly spreading, the climatological wind) and
forests trained on seasons in this mode — the same method with a different forecaster's verification statistics.

### Ceilings

A ceiling is the best any prediction could score from what the farm knew, measured by re-simulating the same cases
under many draws of what it could not know. The targets are met everywhere, so no check relies on a ceiling to pass;
they are measured where the addendum asks (the planned spray) and where they show a check is at its limit.

**Planned spray** (definitions addendum §3; `tools/models/ceiling_plan.py` on the test set's after rows; 78,154
squares × 200 draws = 15.6 million re-draws per version). Each square's kill is re-drawn varying what the farm could not
know when it decided — the weather at the arm's minute, the rain of the six hours after it, the pest's true age, and
the pest itself where the cameras may have misidentified it — and scored with the farm's own kill rule; the best
prediction is the value within ± 5 points of the most draws. Two versions:

- *As written in the addendum*: wind × 0.8–1.2 and temperature ± (0.3 + 0.04 × lead) °C around one hourly forecast
  (inverted with a flat prior), rain from the false-alarm and miss rates with a 2,000-year climatology (weather years
  90,001–92,000).
- *With what the v2 farm knows*: the farm's own posterior rain draws, its wind from the intersection of all the hourly
  wind forecasts for the day, its fitted temperatures.

| | pooled | built-in | conventional | API, no advice | cont. flooding | kharif | rabi |
|---|---:|---:|---:|---:|---:|---:|---:|
| The pack (T, after) | **99.4** | **97.6** | **99.9** | **97.9** | **97.6** | **99.1** | **99.8** |
| Ceiling as written (split halves) | 97.0 (96.8) | 93.9 (93.3) | 97.9 (97.8) | 94.1 (93.5) | 93.9 (93.3) | 96.9 (96.6) | 97.3 (97.2) |
| Ceiling with the farm's knowledge (split halves) | 99.1 (99.1) | 98.2 (98.1) | 99.4 (99.4) | 97.9 (97.9) | 98.2 (98.1) | 99.3 (99.3) | 99.0 (98.9) |

The pack beats the ceiling as written in every group: the addendum's draws treat the wind as uncertain by ± 20 %,
but the farm narrows it to about ± 0.5 % by intersecting every hourly forecast for the day — so that ceiling
understates what can be known (its own best guesses hit the truth only 92.6 % of the time for the built-in manager).
Against the ceiling with the farm's knowledge, the pack is within 0.6 points where it is below it (built-in,
continuous flooding), level for the API without advice, and above it elsewhere (the draws of the pest's age and identity, taken from
the training seasons' spread, are a little wider than the real uncertainty). What is left is luck: sprays where the
cameras named the wrong pest.

**Soil** (`tools/models/ceiling_soil.py` on T; 43,200 readings × 200 re-draws of the true soil behind each reading,
the probe's own noise, a flat prior): **98.1 %** against v1's forest's 98.0 % — at the ceiling.

**Harvest readiness**: 100 % by construction (the truth is a rule on what the farm measures), and the pack scores it.

**Time to AWD** (`tools/models/ceiling_awd.py`; a fifth of the test cases under all four decision makers — 160 seasons
re-run exactly, every second scored check: 6,125 checks × 200 = 1.225 million re-draws; 8 of them had no draw the audit
would score, so the ceiling is the mean over 6,117). Each check is re-projected from
the field's **true** state with the twin's own water balance, under 200 draws of the next 73 hours from the posterior
of the weather given everything forecast at the check (with the weather that came it reproduces the audit's truth:
largest difference 0.0 h). The ceiling is more generous than the farm: it knows the true level and today's weather so
far.

| | pooled | built-in | conventional | API, no advice | cont. flooding | kharif | rabi |
|---|---:|---:|---:|---:|---:|---:|---:|
| The pack, on these checks | 99.25 | 98.98 | 100.0 | 98.95 | 100.0 | 98.98 | 99.37 |
| Ceiling (split halves) | 99.91 (99.90) | 99.85 (99.83) | 100.0 (99.99) | 99.91 (99.89) | 100.0 (99.99) | 99.81 (99.77) | 99.96 (99.95) |

Within 0.9 points of the ceiling everywhere. Of the 24,417 true days checked, 131 (0.5 %) had zero weight in the
posterior. On seven of the cases, checked day by day, every such day (267 of 267) lay inside an event — a dry spell or
an overcast week — announced only after the check, which changed the weather after the forecasts were issued, and none
lay elsewhere: the farm cannot know an event before it is announced, and the posterior has no events it was not told
of.

### A/B of the decision changes

The pack changes what the built-in manager decides (the time to AWD in its re-flooding, finding F5; the harvest readiness in its harvest timing, F4) and therefore its continuous-flooding ablation too. Set H runs 650 paired cases for each, once with v1's forests and once with the pack, the same v2 rules in both (`tools/models/ab.py`; means per season, 95 % intervals from the paired t-distribution):

| Mean per season | built-in: v1 forests → pack (n = 650) | pack − v1 (95 % interval) | cont. flooding: v1 forests → pack (n = 650) | pack − v1 (95 % interval) |
|---|---:|---:|---:|---:|
| Yield, t/ha | 4.249 → 4.249 | -0.000 (-0.003 … +0.002) | 4.244 → 4.244 | -0.000 (-0.003 … +0.002) |
| Irrigation, mm | 934.9 → 929.4 | -5.4 (-6.5 … -4.4) | 1,288.7 → 1,288.7 | identical |
| Nitrogen, kg N/ha | 110.14 → 110.14 | identical | 110.22 → 110.22 | identical |
| Insecticide, g a.i./ha | 15.65 → 15.64 | -0.01 (-0.09 … +0.08) | 15.62 → 15.62 | identical |
| Biologicals (Bt), kg/ha | 0.324 → 0.324 | -0.000 (-0.002 … +0.001) | 0.323 → 0.323 | identical |
| Area sprayed, m² | 31.55 → 31.55 | -0.01 (-0.09 … +0.08) | 31.46 → 31.46 | identical |
| Methane, kg CH₄/ha | 64.808 → 64.767 | -0.040 (-0.059 … -0.022) | 117.719 → 117.644 | -0.075 (-0.108 … -0.042) |
| Pump energy, kWh | 28.270 → 28.106 | -0.164 (-0.196 … -0.133) | 38.971 → 38.971 | identical |
| Guardrail rejections | 0.00 → 0.00 | identical | 0.00 → 0.00 | identical |
| Guardrail warnings | 3.614 → 3.634 | +0.020 (-0.002 … +0.042) | 3.925 → 3.918 | -0.006 (-0.015 … +0.002) |

- built-in: significantly worse with the pack: nothing
- cont. flooding: significantly worse with the pack: nothing

Kharif:

| Mean per season | built-in: pack − v1 (95 % interval) | cont. flooding: pack − v1 (95 % interval) |
|---|---:|---:|
| Yield, t/ha | -0.001 (-0.005 … +0.004) | -0.001 (-0.005 … +0.004) |
| Irrigation, mm | -4.6 (-6.3 … -2.9) | identical |
| Nitrogen, kg N/ha | identical | identical |
| Insecticide, g a.i./ha | -0.04 (-0.16 … +0.08) | identical |
| Biologicals (Bt), kg/ha | -0.001 (-0.004 … +0.002) | identical |
| Area sprayed, m² | -0.06 (-0.21 … +0.10) | identical |
| Methane, kg CH₄/ha | -0.044 (-0.080 … -0.008) | -0.086 (-0.149 … -0.022) |
| Pump energy, kWh | -0.138 (-0.190 … -0.087) | identical |
| Guardrail rejections | identical | identical |
| Guardrail warnings | +0.031 (-0.011 … +0.072) | -0.012 (-0.029 … +0.005) |

Rabi:

| Mean per season | built-in: pack − v1 (95 % interval) | cont. flooding: pack − v1 (95 % interval) |
|---|---:|---:|
| Yield, t/ha | -0.000 (-0.001 … +0.001) | -0.000 (-0.001 … +0.001) |
| Irrigation, mm | -6.3 (-7.5 … -5.1) | identical |
| Nitrogen, kg N/ha | identical | identical |
| Insecticide, g a.i./ha | +0.03 (-0.09 … +0.14) | identical |
| Biologicals (Bt), kg/ha | +0.000 (-0.001 … +0.002) | identical |
| Area sprayed, m² | +0.05 (-0.03 … +0.13) | identical |
| Methane, kg CH₄/ha | -0.036 (-0.046 … -0.027) | -0.064 (-0.081 … -0.047) |
| Pump energy, kWh | -0.191 (-0.227 … -0.154) | identical |
| Guardrail rejections | identical | identical |
| Guardrail warnings | +0.009 (-0.007 … +0.025) | identical |

The guardrail warnings were looked at although they are not on the brief's list: over the 25 seasons whose warning counts differed, the pack's extra warnings were mostly "about 30 mm or more of rain is expected today" on a re-flood (the field now waits longer before re-flooding, so a re-flood falls more often on a day with a storm in the forecast; below the surface such a storm raises the water table only about 100 mm, so the re-flood is still needed) and rotate-the-mode-of-action notes on sprays whose timing shifted. None was a rejection.

The conventional manager and the API without advice never read the forests, and with the pack they decide exactly as before — checked on every paired case of the test set (decision and farm fingerprints):

| Decision maker | Paired cases | Same decisions | Same farm | Reads the forests' advice |
|---|---:|---:|---:|---|
| conventional | 200 | 200 | 200 | no |
| api-no-advice | 200 | 200 | 200 | no |
| builtin | 200 | 5 | 5 | yes (see the A/B) |
| builtin-cf | 200 | 167 | 167 | yes (see the A/B) |

### v1's campaign cases, v1 farm → v2 farm

v1's validation-campaign cases (seeds 7,100,000–7,100,199, weather years 3,001–3,200, kharif and rabi alternating, no
events; never used for any model) were run again on the final v2 build under v1's three decision makers and the API
without advice (`tests/e2e/campaign_resumable.py`), and `tests/e2e/campaign_v1_v2.py` sets each season against v1's own
result for the same case (`tests/results/v1/campaign200.json`). Not one of the 600 seasons repeats v1's decision list —
with the arms, seasons part ways from the first morning (F6) — so this is season against season on the same field and
weather. Means per season, paired differences with 95 % intervals (t):

**builtin** (200 seasons; same decisions as v1 in 0)

| Mean per season | v1 | v2 | v2 − v1 (95 % interval) | kharif v2 − v1 | rabi v2 − v1 |
|---|---:|---:|---:|---:|---:|
| Yield, t/ha | 4.239 | 4.241 | +0.003 (-0.005 … +0.011) | +0.000 (-0.014 … +0.014) | +0.005 (-0.004 … +0.013) |
| Irrigation, mm | 947 | 942 | -5 (-7 … -3) | -4 (-8 … -1) | -6 (-8 … -3) |
| Pump energy, kWh | 28.64 | 28.49 | -0.15 (-0.21 … -0.09) | -0.14 (-0.23 … -0.04) | -0.17 (-0.23 … -0.10) |
| Nitrogen, kg N/ha | 110.0 | 110.1 | +0.0 (-0.0 … +0.0) | +0.0 (-0.0 … +0.1) | +0.0 (+0.0 … +0.0) |
| Insecticide, g a.i./ha | 8.1 | 9.0 | +0.9 (+0.2 … +1.7) | +1.5 (+0.1 … +2.9) | +0.4 (-0.3 … +1.1) |
| Bt, kg/ha | 0.24 | 0.26 | +0.02 (-0.00 … +0.03) | +0.02 (-0.02 … +0.05) | +0.01 (-0.00 … +0.03) |
| Area sprayed, m² | 20.4 | 21.7 | +1.3 (+0.3 … +2.2) | +1.7 (-0.1 … +3.5) | +0.9 (+0.2 … +1.6) |
| Methane, kg CH₄/ha | 64.4 | 64.3 | -0.0 (-0.1 … -0.0) | -0.1 (-0.1 … -0.0) | -0.0 (-0.1 … -0.0) |
| Guardrail rejections | 0.00 | 0.00 | +0.00 (+0.00 … +0.00) | +0.00 (+0.00 … +0.00) | +0.00 (+0.00 … +0.00) |

**conventional** (200 seasons; same decisions as v1 in 0)

| Mean per season | v1 | v2 | v2 − v1 (95 % interval) | kharif v2 − v1 | rabi v2 − v1 |
|---|---:|---:|---:|---:|---:|
| Yield, t/ha | 3.959 | 3.954 | -0.005 (-0.013 … +0.002) | -0.005 (-0.018 … +0.009) | -0.006 (-0.014 … +0.002) |
| Irrigation, mm | 1,313 | 1,313 | -0 (-1 … +0) | -1 (-2 … +1) | +0 (-0 … +0) |
| Pump energy, kWh | 39.71 | 39.70 | -0.01 (-0.03 … +0.01) | -0.02 (-0.06 … +0.02) | +0.01 (-0.00 … +0.01) |
| Nitrogen, kg N/ha | 109.6 | 109.6 | +0.0 (+0.0 … +0.0) | +0.0 (+0.0 … +0.0) | +0.0 (+0.0 … +0.0) |
| Insecticide, g a.i./ha | 360.5 | 360.5 | -0.0 (-0.2 … +0.1) | -0.1 (-0.3 … +0.2) | +0.0 (+0.0 … +0.0) |
| Bt, kg/ha | 0.00 | 0.00 | +0.00 (+0.00 … +0.00) | +0.00 (+0.00 … +0.00) | +0.00 (+0.00 … +0.00) |
| Area sprayed, m² | 297.9 | 297.0 | -0.9 (-3.3 … +1.5) | -1.8 (-6.6 … +3.0) | +0.0 (+0.0 … +0.0) |
| Methane, kg CH₄/ha | 113.3 | 113.3 | -0.0 (-0.1 … +0.0) | -0.0 (-0.1 … +0.0) | +0.0 (+0.0 … +0.0) |
| Guardrail rejections | 0.07 | 0.07 | -0.01 (-0.01 … +0.00) | -0.01 (-0.03 … +0.01) | +0.00 (+0.00 … +0.00) |

**builtin-cf** (200 seasons; same decisions as v1 in 0)

| Mean per season | v1 | v2 | v2 − v1 (95 % interval) | kharif v2 − v1 | rabi v2 − v1 |
|---|---:|---:|---:|---:|---:|
| Yield, t/ha | 4.235 | 4.239 | +0.004 (-0.004 … +0.012) | +0.001 (-0.013 … +0.015) | +0.007 (-0.002 … +0.016) |
| Irrigation, mm | 1,307 | 1,307 | -0 (-1 … +0) | -0 (-1 … +1) | -0 (-1 … +0) |
| Pump energy, kWh | 39.52 | 39.51 | -0.00 (-0.02 … +0.01) | -0.00 (-0.04 … +0.03) | -0.00 (-0.02 … +0.01) |
| Nitrogen, kg N/ha | 110.1 | 110.1 | +0.0 (-0.0 … +0.1) | -0.0 (-0.1 … +0.1) | +0.0 (-0.0 … +0.1) |
| Insecticide, g a.i./ha | 8.2 | 8.9 | +0.7 (-0.1 … +1.5) | +1.1 (-0.4 … +2.6) | +0.3 (-0.4 … +1.0) |
| Bt, kg/ha | 0.24 | 0.25 | +0.01 (-0.00 … +0.03) | +0.01 (-0.02 … +0.05) | +0.01 (-0.00 … +0.03) |
| Area sprayed, m² | 20.4 | 21.5 | +1.1 (+0.2 … +2.1) | +1.4 (-0.4 … +3.1) | +0.9 (+0.1 … +1.6) |
| Methane, kg CH₄/ha | 117.0 | 117.0 | -0.1 (-0.1 … -0.0) | -0.1 (-0.2 … -0.0) | -0.1 (-0.1 … -0.0) |
| Guardrail rejections | 0.00 | 0.00 | +0.00 (+0.00 … +0.00) | +0.00 (+0.00 … +0.00) | +0.00 (+0.00 … +0.00) |

- **Water.** The built-in manager pumps 5 mm (−7 … −3) and 0.15 kWh less a season: F5's re-flooding rule (on set H's
  650 paired cases: −5.4 mm). Methane falls by a few hundredths of a kg/ha for both built-in variants: harvests a few hours earlier (F4).
  Yield is unchanged everywhere.
- **Spraying.** On these cases the built-in manager sprayed 1.3 m² more a season (+0.3 … +2.2; 20.4 → 21.7 m²) with
  0.9 g more insecticide (+0.2 … +1.7; 8.1 → 9.0 g a.i./ha), and its continuous-flooding ablation 1.1 m² more
  (+0.2 … +2.1). Neither part of v2 does this on other cases: the pack, A/B on set H's 650 paired cases: area −0.01 m²
  (−0.09 … +0.08), insecticide −0.01 g; the arms alone, on 300 of set H's cases: area −1.1 m² (−3.1 … +1.0),
  insecticide −1.1 g (−2.5 … +0.3) — and on the 155 of them without events, like these cases: +0.5 m² (−0.7 … +1.7),
  −0.9 g (−1.9 … +0.0). The kill per square is unchanged (F6). Seasons that take another course carry the
  season-to-season spread of pest outbreaks into every difference, the two built-in variants share most of their pest
  history (so their differences are not independent evidence), and with 27 comparisons in these tables one or two
  crossing the 95 % line by chance are expected. We found nothing in v2 that makes the farm spray more and report the
  differences as measured.
- **Conventional practice** is unchanged within noise (it reads neither the forecast nor the forests).

### Simulations and compute

Every season run in this round, by purpose (`tools/models/compute_table.py`). Core-hours are the seasons times the CPU
time one season of that kind takes, measured on this machine with `time.process_time()` (`tools/models/cpu_per_season.py`);
the batches' elapsed times are longer because jobs shared the two cores. The 360 seasons set aside were run and then not
used, each for the reason given; they are counted here and nowhere else.

| Set | Purpose | Seasons | Core-hours (seasons × measured CPU per season) | Elapsed, h (batches; jobs shared two cores) |
|---|---|---:|---:|---:|
| G | training (one decision maker per case, in turn) | 2,000 | 8.2 | 9.5 |
| T | test, before (v1 forests) | 800 | 3.3 | 4.0 |
| T | test, after (the pack) | 800 | 3.4 | 4.6 |
| T | test, harder forecast mode (the pack) | 800 | 3.4 | 7.7 |
| L | live, once after the freeze (the pack) | 2,000 | 8.5 | 11.1 |
| H | A/B arm A (v1 forests; built-in and cont. flooding) | 1,300 | 5.0 | 4.3 |
| H | A/B arm B (the pack; built-in and cont. flooding) | 1,300 | 6.4 | 6.8 |
| G | smoke test of the batch runner | 4 | 0.0 | 0.0 |
| v1 campaign cases | final check: v1 farm → v2 farm, four decision makers | 800 | 3.4 | 7.8 |
| W | final check: seasons through the stand-in over HTTP (200) and the built-in reference (50) | 250 | 1.4 | 4.4 |
| H | diagnostic: the arms off (against A/B arm A) | 300 | 0.9 | 2.4 |
| T (a fifth) | time-to-AWD ceiling: seasons re-run exactly to capture the checks | 160 | 0.7 | 1.2 |
| T, cases 0–34 (before) | set aside: run without rows, which the tables need; run again with rows | 35 | 0.1 | – |
| H, first F5 rule | set aside: A/B seasons of the first re-flood rule, worse; rule fixed, A/B run again | 285 | 1.2 | – |
| G, development | set aside: feature-cost trials while the time-to-AWD inputs were developed | 16 | 0.1 | – |
| T, development | set aside: partial test run with an earlier 44-input feature set | 24 | 0.1 | – |
| **all** | | **10,874** | **45.9** | |

| Ceiling | Re-draws | CPU, min |
|---|---:|---:|
| Planned spray, as the addendum writes it | 15,630,800 | 9 |
| Planned spray, with the farm's knowledge | 15,630,800 | 4 |
| Time to AWD | 1,225,000 | 72 |
| Soil | 8,640,000 | – |
| **all** | **41,126,600** | |

CPU per season, measured (s; mean of a kharif and a rabi case of set H, `tools/models/cpu_per_season.py`): v1-rows: builtin 19, conventional 11, api-no-advice 14, builtin-cf 15; pack-rows: builtin 20, conventional 10, api-no-advice 15, builtin-cf 16; v1-norows: builtin 14, conventional 9, api-no-advice 10, builtin-cf 14; pack-norows: builtin 20, conventional 11, api-no-advice 14, builtin-cf 15; v1-norows-armsoff: builtin 10, conventional 6, api-no-advice 8, builtin-cf 10.

Not in the table: the guardrail fuzzing (12,000 replies in four chunks, 3.3 core-hours), the forests' development and
training (each final forest trains in under a minute and a half; the candidates were trained the same way), the unit
tests (5 minutes), the determinism and replay test (7 minutes) and the browser tests. In all: **10,514 seasons used and
10,874 run** (≥ 10,000 asked) and **41.1 million re-draws** for the ceilings (≥ 100,000 asked), on two cores.

---

## Decision makers compared

| | |
|---|---|
| `api` | the external model, with the forests' advice |
| `api-no-advice` | the external model without any forest output (an ablation) |
| `builtin` | the farm's own rules: soil check and amendments from the soil advice; soak, puddle with the basal dose, transplant when the establishment forest gives ≥ 72 % (or once seedlings are 26 days old); gap-fill dead squares 7–10 days later; safe AWD (ponded 10 days after transplanting, re-flood to 50 mm at −15 cm — at a decision point from −14 cm, unless the time-to-AWD forest expects −15 cm only after the next decision point (v2) — ponded from about a week before flowering to a week after it, only re-wetting to the surface in the last days before the drain-down, drained at 90 % progress); the recommended dose in three nitrogen splits adjusted to the soil test; the original pest rules and product choice with the spray forest's best safe hour, keeping two days in hand on waiting periods; harvest when the readiness forest calls ≥ 60 % of plants ready and the grain is dry |
| `conventional` | no soil test; transplant 21-day seedlings on the planned day and gap-fill a week later; continuous flooding (top up to 50 mm below 30 mm); blanket fertiliser on the calendar into standing water; four calendar sprays over the whole field (chlorantraniliprole and pymetrozine in turn, 20, 35, 50 and 65 days after transplanting) plus a whole-field spray when damage is obvious (≥ 15 % severity; products chosen so their waiting periods fit); it doesn't check the forecast, so the rain rule sometimes postpones a calendar spray to the next morning; harvest at maturity |
| `builtin-cf` | (campaign ablation) the built-in manager with continuous flooding: isolates AWD's effect |

The mock answers with `builtin`'s choices, so `api` against the mock reproduces `builtin` decision for decision
(checked in the tests); `api-no-advice` against the mock uses the built-in manager's plain-rule variant, which works
from the readings only.

---

## Test and simulation results

<!--RESULTS-->
Everything here was produced on the final v2 build by the commands shown; the raw outputs are in `tests/results/`
(v1's are kept in `tests/results/v1/`). **Every "API" number comes from the stand-in**, which answers with the built-in
manager's choices (or, without advice, its plain rules): these results test the plumbing, the guardrails and the farm,
not a real model's judgement.

### Unit and integration tests

`python -m unittest discover -s tests`: 109 tests in 14 files, all pass, with the frozen paddy pack installed (275 s on two cores; v1: 68 tests in 8 files). New in v2: test_arms (7), test_engine_arms (11), test_awd_post (7), test_paddy_features (5), test_modelround (3), test_scene_state (1); test_managers gained the AWD-timing tests.

Per file: api_client 11 · arms 7 · awd_post 7 · config 2 · engine 8 · engine_arms 11 · guardrails 10 · managers 14 · modelround 3 · paddy_features 5 · scene_state 1 · schema 12 · server 11 · twin 7

They cover the twin's water balance, AWD and methane; growth, establishment and the practice factors; pests, sprays and the label accounting; every guardrail; the reply schema and validator; the API client against the stand-in with rate limits, server errors, timeouts, malformed and unsafe replies; the engine's decision loop, triggers, budgets, approvals and fallbacks; the managers' rules; every REST endpoint against the OpenAPI document, the event stream and kept-alive connections; the `.env` reader; and, new in v2, the arms (all 72 squares within reach and joint limits, no collisions, submission order, determinism, preview = execution), the engine with the arms, the 3D view against the twin's state, the weather posterior, the paddy features, the model round's tools and the AWD timing.

### Validation campaign: 200 seasons per decision maker

`python tests/e2e/campaign_resumable.py --managers builtin,conventional,builtin-cf,api-no-advice --seasons 200 --base-seed 7100000 --first-year 3001 …` (v1's campaign cases; resumable, one season per line, then `--finish`), tables by `tests/e2e/analyse_campaign.py`. The built-in manager, conventional practice, the built-in manager with continuous flooding (isolating AWD) and the API without advice through the stand-in.


Every manager ran the same cases (new field seed and weather year each, kharif and rabi alternating). Means per season with 95% confidence intervals.

| | Built-in (AWD) | Conventional | Built-in, continuous flooding | API, no advice |
|---|---:|---:|---:|---:|
| Yield, t/ha (paddy) | 4.24 ± 0.06 | 3.95 ± 0.06 | 4.24 ± 0.06 | 4.22 ± 0.06 |
| Irrigation, mm | 942 ± 29 | 1,313 ± 36 | 1,307 ± 35 | 948 ± 29 |
| Pumping energy, kWh (72 m²) | 28.5 ± 0.9 | 39.7 ± 1.1 | 39.5 ± 1.1 | 28.7 ± 0.9 |
| Nitrogen, kg N/ha | 110 ± 2 | 110 ± 1 | 110 ± 2 | 110 ± 2 |
| Insecticide, g a.i./ha (chemical and botanical) | 9.0 ± 0.9 | 360.5 ± 0.1 | 8.9 ± 0.9 | 8.5 ± 0.8 |
| Bt (microbial), kg of product/ha | 0.26 ± 0.03 | 0.00 ± 0.00 | 0.25 ± 0.02 | 0.21 ± 0.02 |
| Area sprayed, m² of 72 (all sprays added up) | 22 ± 2 | 297 ± 2 | 22 ± 2 | 18 ± 1 |
| Methane, kg CH₄/ha | 64 ± 1 | 113 ± 1 | 117 ± 1 | 62 ± 0 |
| Decision calls | 293 ± 1 | 288 ± 1 | 288 ± 1 | 290 ± 1 |
| Yield, kharif | 4.69 ± 0.02 | 4.31 ± 0.06 | 4.68 ± 0.02 | 4.64 ± 0.03 |
| Irrigation, kharif, mm | 757 ± 25 | 1,086 ± 29 | 1,083 ± 29 | 762 ± 25 |
| Yield, rabi | 3.79 ± 0.01 | 3.60 ± 0.04 | 3.79 ± 0.01 | 3.79 ± 0.01 |
| Irrigation, rabi, mm | 1,128 ± 13 | 1,539 ± 18 | 1,531 ± 18 | 1,134 ± 12 |
| Water productivity, kg grain per m³ pumped | 0.49 ± 0.02 | 0.32 ± 0.01 | 0.34 ± 0.01 | 0.48 ± 0.02 |

**Paired differences** (same cases; mean difference, 95% interval, relative to the second manager):

| | Yield | Irrigation | Nitrogen | Insecticide a.i. | Methane |
|---|---:|---:|---:|---:|---:|
| Built-in (AWD) − Conventional | +0.29 [+0.25, +0.32] (+7.3%) | -371 [-379, -362] (-28.2%) | +0.5 [-0.1, +1.0] (+0.4%) | -351.5 [-352.4, -350.6] (-97.5%) | -49.0 [-49.6, -48.3] (-43.2%) |
| Built-in (AWD) − Built-in, continuous flooding | +0.00 [+0.00, +0.00] (+0.1%) | -365 [-373, -357] (-27.9%) | -0.1 [-0.1, +0.0] (-0.1%) | +0.1 [-0.1, +0.3] (+1.3%) | -52.6 [-53.2, -52.1] (-45.0%) |
| Built-in, continuous flooding − Conventional | +0.28 [+0.25, +0.32] (+7.2%) | -6 [-8, -5] (-0.5%) | +0.5 [-0.1, +1.1] (+0.5%) | -351.6 [-352.5, -350.7] (-97.5%) | +3.7 [+2.6, +4.8] (+3.2%) |

**Against published figures**

- Karnataka rice yield 2018-19 to 2022-23: 3,012–3,089 kg of rice per ha (RBI). At a 67% paddy-to-rice out-turn that is about 4.5–4.6 t/ha of paddy. The twin gives 3.95 ± 0.06 t/ha (conventional) and 4.24 ± 0.06 t/ha (built-in); kharif 4.69 ± 0.02, rabi 3.79 ± 0.01 (built-in).
- AWD against continuous flooding, all else equal: water -27.9% [-28.5, -27.3], yield +0.1% [+0.0, +0.1], methane -45.0%. Published: mild AWD cuts water use by 23.4% without a significant yield loss (Carrijo et al. 2017); IRRI and TNAU cite up to 25–30% less water; IPCC scales methane by 0.55 for multiple drainage (−45%); Balaine et al. (2019) measured −41% for safe AWD.

**The forests' advice, checked as the definitions say** (docs/DEFINITIONS*.md, with the v2 addendum's time to AWD, planned spray and spray at the arm's minute; all checks pooled over the campaign; Wilson 95% intervals):

| Check | Built-in (AWD) | Conventional | Built-in, continuous flooding | API, no advice |
|---|---:|---:|---:|---:|
| Soil suitability | 98.3% [98.0–98.5], n=14,436 | – | 98.3% [98.0–98.5], n=14,436 | 98.3% [98.0–98.5], n=14,436 |
| Transplanting day, chance within ±5 points | 99.5% [99.5–99.6], n=30,130 | 99.4% [99.3–99.4], n=87,108 | 99.5% [99.4–99.6], n=30,130 | 99.5% [99.5–99.6], n=31,347 |
| Transplant / wait call | 100.0% [99.7–100.0], n=1,100 | 100.0% [99.8–100.0], n=2,706 | 100.0% [99.7–100.0], n=1,100 | 100.0% [99.7–100.0], n=1,136 |
| Pest risk, 7 days | 99.9% [99.9–99.9], n=2,603,038 | 99.2% [99.2–99.3], n=1,725,522 (balanced 99.4%) | 99.9% [99.9–99.9], n=2,604,362 | 99.5% [99.5–99.5], n=2,707,919 (balanced 99.0%) |
|   recall, plants that did pass 8% | – | 99.5% [99.1–99.7], n=2,442 | – | 98.6% [98.0–98.9], n=2,766 |
|   recall, plants that did not | 99.9% [99.9–99.9], n=2,603,038 | 99.2% [99.2–99.3], n=1,723,080 | 99.9% [99.9–99.9], n=2,604,362 | 99.5% [99.5–99.5], n=2,705,153 |
| Yield forecast within ±10 points | 100.0% [99.0–100.0], n=400 | 95.0% [92.4–96.7], n=400 | 100.0% [99.0–100.0], n=400 | 99.8% [98.6–100.0], n=400 |
| Spray efficacy within ±5 points | 99.3% [99.0–99.5], n=4,332 | 99.8% [99.8–99.9], n=59,400 | 99.3% [99.1–99.5], n=4,306 | 99.3% [99.0–99.5], n=3,569 |
| Spray efficacy, planned (v2 target) | 98.5% [98.1–98.8], n=4,332 | 99.9% [99.9–100.0], n=59,400 | 98.4% [98.0–98.8], n=4,306 | 98.7% [98.2–99.0], n=3,569 |
| Harvest readiness | 100.0% [100.0–100.0], n=155,457 | 100.0% [100.0–100.0], n=149,088 | 100.0% [100.0–100.0], n=155,527 | 100.0% [100.0–100.0], n=152,466 |
| Time to AWD (v2), within ±6 h or ±20 % | 99.0% [98.9–99.2], n=22,529 | 100.0% [99.9–100.0], n=7,954 | 100.0% [99.9–100.0], n=7,893 | 99.0% [98.9–99.1], n=22,057 |

Share of seasons in which each check reached 95% (checks with at least 20 cases in the season):

| Check | Built-in (AWD) | Conventional | Built-in, continuous flooding | API, no advice |
|---|---:|---:|---:|---:|
| Soil suitability | 94% of 200 | – | 94% of 200 | 94% of 200 |
| Transplanting day, chance within ±5 points | 98% of 200 | 98% of 200 | 98% of 200 | 98% of 200 |
| Transplant / wait call | – | – | – | – |
| Pest risk, 7 days | 100% of 200 | 100% of 200 | 100% of 200 | 100% of 200 |
|   recall, plants that did pass 8% | – | 100% of 57 | – | 91% of 68 |
|   recall, plants that did not | 100% of 200 | 100% of 200 | 100% of 200 | 100% of 200 |
| Yield forecast within ±10 points | – | – | – | – |
| Spray efficacy within ±5 points | 92% of 99 | 99% of 200 | 93% of 99 | 95% of 92 |
| Spray efficacy, planned (v2 target) | 89% of 99 | 100% of 200 | 89% of 99 | 96% of 92 |
| Harvest readiness | 100% of 200 | 100% of 200 | 100% of 200 | 100% of 200 |
| Time to AWD (v2), within ±6 h or ±20 % | 97% of 199 | 100% of 154 | 100% of 155 | 97% of 199 |

Guardrail verdicts over the campaign: Built-in (AWD) 34,478 accepted, 640 with a warning, 0 rejected; Conventional 25,149 accepted, 1,742 with a warning, 14 rejected; Built-in, continuous flooding 48,188 accepted, 684 with a warning, 0 rejected; API, no advice 30,524 accepted, 727 with a warning, 484 rejected.
Forced harvests: 0; abandoned fields: 0.
Simulation time: Built-in (AWD) 43.9 s per season, Conventional 25.8 s per season, Built-in, continuous flooding 37.5 s per season, API, no advice 33.9 s per season (wall time per season in each worker process; the machine had two cores, shared with other test runs).

**Reading the campaign** (v2; v1's figures in brackets).

- **Yield.** The built-in manager beats conventional practice by 7.3 % (v1: 7.1 %): 9 % in kharif, 5 % in rabi. The
  continuous-flooding ablation shows the gain is not from the water: with the same flooding it is still 7.2 % ahead. It
  comes from the soil test and amendments, top-dressing into shallow water and pest control that acts on first
  sightings. Rabi yields are lower mostly because of the original crop library's sowing-month suitability (see Limits).
- **Water and methane.** AWD cuts pumping by 27.9 % against continuous flooding (27.5 %) with no yield change, inside
  the published 23–30 %, and methane by 45 %, IPCC's factor for multiple drainage. The extra 0.4 point is F5's
  re-flooding rule.
- **Insecticide.** Conventional practice sprays the whole field four times on the calendar (360 g of active ingredient
  per ha); the built-in manager spot-sprays a few squares, mostly with Bt or neem at low pressure: 9.0 g/ha (8.1) plus a
  quarter of a kilogram of Bt. The difference from v1 is discussed under "v1's campaign cases, v1 farm → v2 farm".
- **The API without advice** (new in this campaign) decides from the readings alone with the built-in manager's plain
  rules: almost the same yield (4.22 t/ha) and water, less spraying (18 m², no soil advice so no compost: methane
  62 kg/ha). Its 484 rejected decisions (2.4 a season) are sprays proposed for a windy hour or with rain coming — in
  the season with the most (13), every one was a spray at wind above 4 m/s or with rain forecast in the six hours after:
  without the spray forest's best safe hour it sprays at the hour of the call, and the guardrails stop it. That is what
  the advice is for.
- **Guardrails.** The built-in manager had no decision rejected in 400 seasons; conventional practice had 14 (15), all
  calendar sprays the rain rule pushed to a later morning (it doesn't read the forecast).
- **The forests.** Every check is at or above 95 % pooled under every decision maker on these cases too, including the
  two v2 checks: the time to AWD (99.0–100.0 %) and the planned spray (98.4–99.9 %; v1 66–70 %). Soil is at 98.3 %
  (96.7 %; F1) and reaches 95 % in 94 % of seasons (76 %); harvest readiness is 100 % in every season (92–94 %). The
  lowest is the yield forecast under conventional practice, 95.0 % on 400 cases (96.2 %; 92.4–96.7), the decision maker
  furthest from the playground farms the yield forest was trained on; on the new test set it is 96.5 %, and on the live
  set 94.5 %, the one group below 95 % anywhere (see
  [Below target](#below-target-the-yield-forecast-under-conventional-practice-live-set)).

### What v1's validation found, and what changed (v1)

Kept as v1 reported it; v2's findings are in the [v2 change log](#v2-change-log).

Kept as v1 reported it; v2's findings are in the [v2 change log](#v2-change-log).

Kept as v1 reported it; v2's findings are in the [v2 change log](#v2-change-log).

1. **AWD saved almost no water.** Percolation through the plough pan hardly depended on the water depth. It now
   rises with the ponded head (× (1 + depth/150 mm)) and the bunds seep 4 % of the ponded depth a day: 5–9 mm/day of
   seepage and percolation while ponded, inside FAO's 4–8 mm/day from heavy clay to sandy soils. AWD's saving then
   landed in the published range.
2. **Continuous flooding was scored as "single drainage"** for methane, because the pre-harvest drain-down counted
   as a drainage period. IPCC counts only drainage during the season; now it doesn't count.
3. **Nitrogen leaching made AWD look 3 % better in yield.** Leaching took 40 % of the available N per season under
   flooding. Flooded soil holds N mostly as ammonium; leaching is now about a tenth of that, and AWD and continuous
   flooding give the same yield, as meta-analyses find for safe AWD.
4. **The establishment advice was blind to water depth and seedling age**, so it was wrong when seedlings went into
   deep water. The twin and the advice now share explicit practice factors, applied to the true depth in the twin and
   to the observed depth in the advice.
5. **The spray check used the field's oldest sighting** as the pest's age; the original definition uses each plant's
   own tracked age after the close-up look. Fixed to match.
6. **Generic waiting periods** (chlorantraniliprole 7 days) were replaced by the rice labels' (47, 19, 40, 5, 0 days).
7. **Rabi got the kharif fertiliser dose**; it now gets the recommended 120:60:60.
8. **One built-in spray in 200 seasons was rejected** by the waiting-period rule: the state rounds progress, the rule
   uses the exact median. The managers now keep two days in hand, and the state carries `days_to_maturity` counted
   exactly as the rule counts it.
9. **Chlorantraniliprole was counted at 18 g a.i./ha** (the library's generic figure) instead of the label's 30.
   Active ingredient now comes from the label list, and Bt is reported as kg of product.
10. **A field was re-flooded to 50 mm the day before the drain-down** (seen in the water chart): the water was pumped
    in and then drained away. Near the drain-down the built-in manager now re-wets only up to the surface.
11. **Lost seedlings were never replaced**: in one season field B lost 5 of its 36 squares and gave 4.10 t/ha
    against 4.82 on field A. Both managers now gap-fill 7–10 days after transplanting, as TNAU advises; that season
    field B now gives 4.44 t/ha.
12. **A server bug, found by the browser test:** a POST whose route ignored its body left the bytes on the
    connection, and the next request on that kept-alive connection was garbled (Node and browsers reuse connections).
    The server now always reads the body; a regression test covers it.
13. **The website's automatic theme overrode a host page's choice** (it removed `data-theme`); it now keeps it.

Items 8–11 changed the built-in and conventional managers, so every campaign, comparison and end-to-end run above and
below was repeated after them.

### Guardrail fuzzing

`python tests/e2e/fuzz_chunks.py --n 12000 --chunks 4 --chunk i` for i = 0 … 3, then `--merge` (the split `python -m paddy fuzz --workers 4` makes: 4 shares of 3,000 replies with seeds 1, 1001, 2001, 3001, each run as its own job under the two-hour limit; 3.3 hours in all) pushed 12,000 random replies through validation, the guardrails and execution, each on a copy of a real farm state (372 moments of four built-in seasons, kharif and rabi, with pest outbreaks added so sprays have targets). The replies mix garbage (not JSON, wrong types, NaN, 5,000-character strings, "ignore previous instructions"), near-misses (squares off the grid, unknown products and pests, out-of-range times) and plausible but unsafe decisions (doses over the caps, unlabelled products, sprays in rain, wind, darkness or on flowering rice with bee-toxic products, harvests inside a waiting period, irrigation over the bund or during the drain-down). Decisions scheduled for later were run to their time and checked again. After every execution the farm was checked against the rules.

| | |
|---|---:|
| Replies | 12,000 |
| Decisions that passed the schema | 17,434 |
| Schema problems reported (decisions dropped or fields removed) | 54,712 |
| Accepted / with a warning / rejected by the guardrails | 5,257 / 2,577 / 9,600 |
| Carried out | 6,269 |
| Stopped by the second check at execution time | 36 |
| **Crashes** | **0** |
| **Rule violations found in the farm afterwards** | **0** |

| Action | accepted | warning | rejected |
|---|---:|---:|---:|
| wait | 907 | 622 | 448 |
| puddle | 43 | 0 | 1,764 |
| irrigate | 346 | 197 | 1,238 |
| soil_check | 1,321 | 0 | 424 |
| transplant | 0 | 13 | 1,691 |
| drain | 44 | 1,192 | 451 |
| hold | 1,286 | 0 | 375 |
| harvest | 34 | 63 | 1,374 |
| fertilise | 493 | 186 | 534 |
| scout | 757 | 38 | 221 |
| spray | 19 | 130 | 580 |
| amend | 7 | 136 | 500 |

### Whole seasons through HTTP

`python tests/e2e/mock_seasons.py --set W --start k --seasons 10 --workers 1` for k = 0, 10, …, 40, then `--merge`: the 50 cases of set W (seeds and weather years of their own, 25 kharif and 25 rabi, 28 with events — outbreaks, storms, dry spells, overcast weeks), each under every variant below: 200 seasons where every decision came over a real socket from the stand-in API (which requires the key: every request carried a test key), plus the built-in manager on the same cases for reference. The faulty stand-in answered 4 % of requests with 429 (Retry-After), 3 % with 500, timed out on 1 %, sent malformed JSON 4 % of the time and slipped an unsafe decision into 8 % of its replies. The cautious farmer turned down preventive sprays, immature harvests and every other scouting request, with a reason each time. 4.4 hours of wall time (one worker per part, on two cores shared with other runs).

| | Seasons | Finished | Mean yield t/ha | Calls | Retried and then fine | Repaired | Failed → fallback | Unsafe suggestions → rejected | Rejected by the farmer → reason seen in the next call | Same decisions as the built-in manager | Key found in a record |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Built-in manager (reference, no HTTP) | 50 | 50 | 4.25 | 14,756 | 0 | 0 | 0 → 0 | 0 → 0 | 0 → 0 | – | 0 |
| API through the stand-in, auto | 50 | 50 | 4.25 | 14,756 | 0 | 0 | 0 → 0 | 0 → 0 | 0 → 0 | 50 of 50 | 0 |
| API through the stand-in, approve mode, every decision approved | 50 | 50 | 4.25 | 14,756 | 0 | 0 | 0 → 0 | 0 → 0 | 0 → 0 | 50 of 50 | 0 |
| API through a faulty stand-in, auto | 50 | 50 | 4.25 | 14,756 | 1,777 | 581 | 0 → 0 | 1,202 → 1,202 | 0 → 0 | 0 of 50 | 0 |
| API through a faulty stand-in, approve mode, a cautious farmer | 50 | 50 | 4.25 | 14,783 | 1,741 | 610 | 3 → 3 | 1,102 → 1,102 | 3,465 → 3,465 | 0 of 50 | 0 |

All checks passed. Without faults the API through the stand-in made exactly the built-in decisions, in auto and in approve mode. With faults the decision lists differ (each rejected unsafe suggestion is a decision record too, and the cautious farmer turned decisions down), yet every season finished, every unsafe suggestion was stopped, and the farmer's reasons reached the decision maker.

### Determinism and exact replay

`python tests/e2e/determinism.py`:

| Case | Decision maker | Run twice in separate processes | Decision fingerprint | Farm fingerprint |
|---|---|---|---|---|
| kharif 20261024, weather year 0 | builtin | identical | `2cec7e530f634928` | `49484ffa573741ed` |
| kharif 20261024, weather year 0 | conventional | identical | `66266df1c4e8bb61` | `cd4f56f6a92c598e` |
| kharif 20261024, weather year 0 | api | identical | `2cec7e530f634928` | `49484ffa573741ed` |
| rabi 7400011, weather year 6001 | builtin | identical | `58cd254bd0ab8769` | `7d14beb5102054f1` |
| rabi 7400011, weather year 6001 | conventional | identical | `1627605136a1b6d9` | `b355ed24b01b50d1` |
| rabi 7400011, weather year 6001 | api | identical | `58cd254bd0ab8769` | `7d14beb5102054f1` |
| kharif 7400012, weather year 6002 | builtin | identical | `2a82a52d855e4c97` | `24c22fc216956a17` |
| kharif 7400012, weather year 6002 | conventional | identical | `12bae8a644b83291` | `e9e42e7e495811b9` |
| kharif 7400012, weather year 6002 | api | identical | `2a82a52d855e4c97` | `24c22fc216956a17` |

The API through the stand-in made exactly the built-in manager's decisions, and the farm followed exactly the same course, in 3 of 3 cases (the fingerprints above match row for row).

| Recorded run | Calls | Retried or repaired | Failed | Replayed from the records |
|---|---:|---:|---:|---|
| kharif 20261024 | 289 | 0 | 0 | identical decisions and farm |
| rabi 7400011, stand-in with faults | 295 | 57 | 0 | identical decisions and farm |

### The website in a browser

`node tests/e2e/ui.js` (Playwright, Chromium) against `serve --manager api --mode approve --mock`: 42 checks passed, 0 failed (in 493 s).

From the page itself, on desktop:

- 4 decisions wait for approval
- stand-in decisions are labelled "stand-in mock API"
- approving from the page hands the decision to its arm (queued)
- rejecting from the page records the reason
- the person's reason reaches the decision maker in the next call
- the approved decision is carried out by its arm once the clock moves (executed)
- "New season" from the page resets to day 0 with the API in auto mode
- Run and Pause from the page (status paused on day 52, 112 calls so far)
- adding a storm from the page is confirmed ("Event added.")
- the storm is in the weather log: "Heavy storm: about 95 mm tomorrow from 14:00"
- the storm triggers a decision call (2026-07-23T23:00, triggers event)
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

- whole season: a kharif season runs to the end from the page (411 s, 288 calls to the stand-in)
- whole season: both fields harvested (A 4.73 t/ha, B 4.71 t/ha)
- whole season: every decision came from the stand-in ({"mock":227})
- whole season: the conventional run on the same weather finished alongside (4.67 t/ha against 4.72)
- whole season: no console errors
- backend down: the page says so ("The farm server is not answering. Start it from the backend folder with python -m paddy ser…")

### The 3D farm in a browser

`node tests/e2e/farm3d_ui.js` (Playwright, Chromium with software WebGL 2): 42 checks passed, 0 failed.

On every viewport (desktop 1440 px and phone 400 px, light and dark):

| Check | desktop light | desktop dark | phone light | phone dark |
|---|---|---|---|---|
| the 3D farm renders | yes | yes | yes | yes |
| all 7 camera presets screenshotted | yes | yes | yes | yes |
| clicking square A3,3 in 3D opens its panel | yes | yes | yes | yes |
| clicking the harvest arm opens the arm panel | yes | yes | yes | yes |
| "Show on the farm" highlights decision d00085 | yes | yes | yes | yes |
| the plan view shows the 72 squares | yes | yes | yes | yes |
| no sideways scrolling | yes | yes | yes | yes |
| no console errors | yes | yes | yes | yes |

- no WebGL 2: the plan is shown with a note ("The 3D farm needs WebGL 2, which this browser doesn't provide, so the …")
- no WebGL 2: no 3D canvas
- no WebGL 2: no console errors
- prefers-reduced-motion: the 3D view turns off sway, blinking, bobbing and camera easing
- approve mode: decision d00001's planned path is drawn before approval (3D shows d00001)
- approve mode: after approval the arm sets off
- live: the arm slow-down control reaches the server ("don't slow down" → work rate 3600)
- live: no console errors

Camera controls (`node tests/e2e/farm3d_controls.js`, on the replay page): 22 checks passed, 0 failed. Each gesture is judged by what it does to the picture (where the view's test hook projects squares A1,1, A1,6, A6,1, A6,6 and B6,6 on the screen): the corner-to-corner span (zoom), its bearing (orbit), the shift of its middle (pan) and field A's height over width (tilt). On the desktop each gesture starts from the overview preset; the touch gestures follow one another on a 400 px phone page, sent as real multi-touch input through the DevTools protocol.

| Check | Result | What the picture did |
|---|---|---|
| desktop: the view is still when nobody touches it | pass | span 364 → 363 px (×1.00), bearing -0.8° → -0.8°, middle moved 0 px |
| mouse: drag orbits the view | pass | span 364 → 210 px (×0.58), bearing -0.8° → -63.1°, middle moved 113 px |
| mouse: vertical drag tilts the view | pass | span 364 → 361 px (×0.99), bearing -0.8° → -0.5°, middle moved 9 px; field A's height over width on the screen 0.582 → 0.408 |
| mouse: right-drag pans the view | pass | span 364 → 386 px (×1.06), bearing -0.8° → -0.8°, middle moved 120 px |
| mouse: Shift-drag pans the view | pass | span 364 → 344 px (×0.95), bearing -0.8° → -0.7°, middle moved 106 px |
| mouse: the wheel zooms in | pass | span 364 → 566 px (×1.56), bearing -0.8° → -0.8°, middle moved 43 px |
| mouse: the wheel zooms out | pass | span 566 → 364 px (×0.64), bearing -0.8° → -0.8°, middle moved 43 px |
| keyboard: + zooms in | pass | + + +: span 364 → 519 px (×1.43), bearing -0.8° → -0.8°, middle moved 33 px; field A's height over width 0.582 → 0.586 |
| keyboard: − zooms out | pass | - - -: span 364 → 253 px (×0.70), bearing -0.8° → -0.8°, middle moved 24 px; field A's height over width 0.582 → 0.580 |
| keyboard: arrows orbit | pass | ArrowLeft ArrowLeft ArrowLeft: span 364 → 378 px (×1.04), bearing -0.8° → 14.8°, middle moved 48 px; field A's height over width 0.582 → 0.555 |
| keyboard: WASD pans | pass | d d d: span 364 → 362 px (×1.00), bearing -0.8° → -0.8°, middle moved 126 px; field A's height over width 0.582 → 0.549 |
| keyboard: Shift+arrows pan | pass | Shift+ArrowRight Shift+ArrowRight Shift+ArrowRight: span 364 → 362 px (×1.00), bearing -0.8° → -0.8°, middle moved 126 px; field A's height over width 0.582 → 0.549 |
| keyboard: PageUp tilts | pass | PageUp PageUp PageUp: span 364 → 369 px (×1.01), bearing -0.8° → -1.0°, middle moved 10 px; field A's height over width 0.582 → 0.758 |
| keyboard: PageDown tilts back | pass | PageDown PageDown PageDown: span 364 → 360 px (×0.99), bearing -0.8° → -0.5°, middle moved 11 px; field A's height over width 0.582 → 0.371 |
| desktop: the page did not scroll | pass | scroll 0, 0 |
| desktop: no console errors | pass | none |
| touch: one-finger drag orbits the view | pass | span 157 → 201 px (×1.28), bearing 93.8° → 29.4°, middle moved 41 px |
| touch: pinching out zooms in | pass | span 201 → 869 px (×4.32), bearing 29.4° → 30.7°, middle moved 215 px |
| touch: pinching in zooms out | pass | span 869 → 187 px (×0.22), bearing 30.7° → 29.3°, middle moved 216 px |
| touch: a two-finger drag pans the view | pass | span 187 → 203 px (×1.08), bearing 29.3° → 28.1°, middle moved 109 px |
| touch: the page itself did not scroll or zoom | pass | scroll 0, 0 |
| phone: no console errors | pass | none |

Frame rates (`node tests/e2e/farm3d_fps.js`; two CPU cores, no GPU; Chromium with SwiftShader (software WebGL 2)): each setting warmed up for 12 s, then the view's own counter read ten times, once a second.

| Setting | Median fps | Readings | Render | Triangles, draw calls |
|---|---:|---|---|---:|
| desktop 1440×1000, auto | 6 | 4, 4, 7, 4, 7, 4, 5, 6, 6, 7 | light | 87,810, 31 |
| desktop 1440×1000, high | 2 | 1, 1, 1, 2, 2, 2, 2, 2, 2, 3 | full | 153,246, 31 |
| laptop 1280×720, high | 2 | 1, 3, 2, 1, 3, 3, 3, 2, 2, 2 | full | 153,246, 31 |
| phone 400×860, auto | 7 | 6, 7, 8, 7, 5, 8, 9, 6, 7, 7 | light | 87,810, 31 |

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

The screenshots are in `tests/results/screens/` (the website: every view on desktop, 1440 px, and phone, 400 px, light
and dark, a decision's detail with the state, the raw reply and the arm plan, the soil layer, the end of a whole season
against the stand-in, and the page shown when the backend is down), `tests/results/screens3d/` (the 3D farm) and
`tests/results/replay-screens/` (the replay page). Three of them:

| | | |
|---|---|---|
| ![A decision, the call behind it and its arm plan](docs/screens/decision-detail.png) | ![The charts at the end of a stand-in season, against conventional practice on the same weather](docs/screens/charts-season-end.png) | ![The fields on a phone, dark theme, plan view](docs/screens/phone-dark.png) |
| A decision, the call behind it and its arm plan | A whole season against conventional practice | Phone, dark, plan view |
<!--/RESULTS-->

## v2 change log

Each entry: what was wrong and how it was found, the root cause, the change, the tests, before → after (per decision
maker and season, with intervals, in the tables above), side effects on the farm, and why the world is no easier.

### F1. The soil check scored half the squares on stale advice (a measurement bug)
- **Found** while caching the twin's field progress for speed and checking which actions change `Twin.version` (the
  advisor caches its advice by version and hour): `act_soil_check` did not.
- **Effect in v1.** The 06:00 call on day 0 builds the state (soil advice from the daily scan, cached), then carries out
  `soil_check A`, `irrigate A`, `soil_check B`, … Field A's probes were scored against the cached scan-based classes;
  `irrigate A` changed the version, so field B was scored on the probe-based classes, as the definition says. On 20
  kharif seasons (seeds 40–59, weather years 540–559; not a model set): scan-based 95.4 %, probe-based 98.1 % (1,440
  squares each); v1's audit reported 96.9 %, the average. v1's campaign figure, 96.7 % (76 % of seasons at ≥ 95 %), was
  measured the same way.
- **Fix.** `act_soil_check` and the planting arm's probe change the version, so the audit always scores the probe
  reading. Decisions and the farm's course are unchanged (the built-in manager never reads soil advice in the hour of a
  probe): the v1 demo season's decision and history fingerprints are identical before and after.
- **Result.** Soil on T: 98.0 % (97.8–98.1), 90 % of seasons at ≥ 95 % (v1's report: 96.7 %, 76 %) — a measurement
  correction, not a better model; the ceiling is 98.1 %.
- **Why the world is no easier.** The probe, its noise and the truth are unchanged; the check now measures what the
  definition always said.

### F2. Time to AWD: from a water balance on the forecast to the posterior of the weather
- **Found.** v1 had no model for it; the farm's own water balance on the forecast scored 41.1 % on the test set (57,908
  checks). Re-simulating checks from the true state with the weather the forecasts allow gives a ceiling of about 99.9 %:
  the misses were not luck.
- **Root causes**, each measured by recomputing the checks with one part of the truth put back: the level (one reading,
  σ 3 mm ≈ 6 h above the surface), the forecast at face value (misses, 1.5 mm/h false alarms, showers starting
  inside an hour), and a one-coefficient water balance.
- **Change.** (1) the level fitted to 48 h of readings with the farm's own balance; (2) 256 draws from the posterior of
  the generator's days given the forecasts; (3) the twin's documented balance on each draw; (4) a forest of 12 trees
  anchored on the posterior estimate (it predicts the ratio truth ÷ estimate), trained on the checks the audit scores.
  `sim/wxpost.py`, `Advisor.level_estimate / weather_draws / project_fast`, the pack's `awd` forest.
- **Before → after on T** (after-rate Wilson 95 % intervals): pooled 41.1 → 99.2 (99.1–99.3); built-in 42.0 → 98.9
  (98.8–99.1); conventional 38.3 → 100.0 (100.0–100.0); API without advice 42.1 → 98.9 (98.7–99.0); continuous flooding 38.4 → 100.0
  (99.9–100.0); kharif 41.1 → 99.0 (98.8–99.1); rabi 41.0 → 99.3 (99.2–99.4). Seasons at ≥ 95 %: 0 → 98 % (97–99).
- **Side effects.** Used by the built-in manager's AWD timing (F5) and shown to the API as advice.
- **Tests.** `tests/test_awd_post.py`: the vectorised generator rebuilds every true day exactly (with and without
  events); every true day has positive likelihood given what was forecast (30 checks × 4 days); the farm sees no future
  weather (a night shower at 06:00 reveals neither the high nor the cloud); draws respect the forecast's rates and
  temperature errors; the many-weather projection equals `project_level` with the weather that came; the fitted level
  is closer than one reading (σ ≤ half); the planned-spray wind and temperature are within 12 % and 1 °C.
- **Why the world is no easier.** The weather, its forecasts and the tube are unchanged. The farm describes them the
  way a forecast post-processing system does — a stochastic weather generator calibrated to the local climate and the
  forecasters' verification statistics (miss and false-alarm rates, temperature error ranges) — and estimates its
  level from its own instruments. It does not see the generator's random numbers, future events, or the true state.
  One quirk of the simulated daily forecaster is deliberately not used: for a dry day its rain amount is an exact
  function of its chance of rain.

### F3. Planned spray: one number per spray → each square at the arm's minute
- **Found.** v1 gave every square of a spray the same number (the spray forest at the planned hour): 66.7 % on the test
  set (78,131 squares).
  Recomputing the test rows with the weather that came at each square's minute and the pest as the farm saw it gave
  99.4 %, so most misses were not luck.
- **Root causes**, each measured by putting one part of the truth back: the wind of a single hourly forecast (the true
  wind × 0.8–1.2), the 1.5 mm/h false alarms read as rain, and the top of the hour standing in for the minute the arm
  reaches each square (the sun, and in the morning the wind, change within the hour).
- **Change.** At the decision the farm asks its own arm planner for each square's minute (the dry run the approve mode
  already used); the day's wind comes from the station (today) or from where all of that day's hourly wind forecasts
  agree (later days: the intersection of their ± 20 % ranges); the days' highs and lows are fitted to every hourly
  temperature forecast and the station; the rain of the six hours after each minute is drawn 128 times from the same
  posterior as the time to AWD; the kill rule is applied to each draw and the value most draws agree on (± 5 points) is
  the estimate. A small forest corrects that estimate (a difference) from the pest the cameras see, its votes and its
  history. `Advisor.spray_weather / planned_spray`, `Engine._spray_times`.
- **Tests.** `tests/test_awd_post.py::test_spray_weather_knows_the_wind` (wind and temperature at the minute within 12 % and
  1 °C on real decisions); `tests/test_engine_arms.py::test_spray_squares_land_at_their_own_minute`.
- **Before → after on T:** pooled 66.7 → 99.4 (99.4–99.5); built-in 56.1 → 97.6 (97.2–98.0); conventional 69.4 → 99.9
  (99.9–99.9); API without advice 60.6 → 97.9 (97.4–98.3); continuous flooding 56.2 → 97.6 (97.2–97.9); kharif 56.8 → 99.1
  (99.0–99.2); rabi 80.1 → 99.8 (99.7–99.8). Seasons at ≥ 95 %: 5 % → 91 % (88–93). Ceilings: see the model round.
- **Side effects.** None on decisions: the planned kill is recorded with the decision (and shown to the API), and no
  decision maker reads it. The conventional and API-without-advice decisions are identical before and after (decision
  fingerprints on the test set).
- **Why the world is no easier.** The weather, its forecasts, the cameras and the pests are unchanged; the farm uses
  its own arm planner, its station and the forecasts it was sent.

### F4. Harvest readiness: the readiness rule as an input
- **Found.** v1's harvest forest (trained on the playground's crops) scored 99.4 % on the test set but left 6 % of seasons
  under 95 %: around the rule's thresholds it misjudges whole fields at once.
- **Root cause.** The truth is the readiness rule on the crop's progress, the last two days' rain and the humidity —
  all measured by the farm exactly. The forest only approximated the thresholds.
- **Change.** A paddy forest (`harvestP`, 10 trees, 240 nodes) gets the rule's own answer as one more input
  (`core/paddy_features.harvest_rule`).
- **Tests.** `tests/test_paddy_features.py::test_harvest_rule_is_the_audits_truth` (the rule input equals the readiness
  truth the audit scored, on every plant assessed in a season).
- **Before → after on T:** pooled 99.4 → 100.0 (100.0–100.0), and 100.0 for every decision maker and both seasons
  (before: built-in 99.4, conventional 99.2, API without advice 99.5, continuous flooding 99.4, kharif 99.3, rabi 99.4). Seasons at
  ≥ 95 %: 94 % → 100 %.
- **Side effects.** The built-in manager harvests when the forest calls ≥ 60 % of plants ready, so harvests move by
  hours: methane −0.04 kg/ha a season for the built-in manager and −0.08 for continuous flooding, yield unchanged (A/B).
- **Why the world is no easier.** The crop and the weather are unchanged; the inputs are the farm's own readings.

### F5. The built-in manager uses the time to AWD to stop re-flooding early
- **Found.** v1 re-floods at a decision point as soon as the tube reads −140 mm or less, 10 mm short of the −150 mm
  line, because decision points are 12 h apart (the tube's own trigger at the line covers the time between them). With
  the field falling about 1 mm an hour, that margin often costs half a day of drying. The brief asks for the
  time-to-AWD forecast to be used in this timing.
- **First version, and what the A/B found.** The first v2 rule scheduled the re-flood for the hour the forest expected
  the line ("when" = that hour). Over the first 127 paired seasons of set H it was worse for the built-in manager:
  irrigation +1.0 mm a season (95 % interval +0.2 … +1.9), pump energy +0.03 kWh (+0.01 … +0.06), guardrail warnings
  +0.25 a season (+0.17 … +0.34, +7 %); yield, nitrogen, insecticide and area sprayed unchanged, methane −0.02 kg/ha.
  Diffing paired seasons showed why: a re-flood decided at 06:00 for 14:00 kept the morning's 50 mm target although by
  14:00 the crop was inside the last re-wetting window, where only the soil up to the surface is wetted (one season:
  +26 mm); rain between the decision and the scheduled hour filled the field, and the irrigation then found nothing to
  pump (a warning); and the scheduled re-floods mostly did what the tube's trigger at the line already does.
- **Change (the rule fixed, before any result on T or L).** No scheduled re-floods. In v1's margin (−150 < tube ≤
  −140 mm) the forest decides: re-flood at once if it expects the line before the next decision point (≤ 12 h),
  otherwise let the field keep drying — the trigger at the line, or the next decision point, re-floods it in time. At or
  past the line, without the forest's answer, and in the ponded window around flowering, v1's rule. The state still
  shows an irrigation already decided for later (`fields.X.water.scheduled`, for decisions the API schedules), and the
  advice text says that a call comes at the line, so there is no need to re-flood early (`paddy-prompt/1.6`).
- **Tests.** `tests/test_managers.py::AwdTiming` (keeps drying when the line comes after the next decision point;
  re-floods at once when it comes first, at or past the line, and without the forest; nothing above the margin or when
  already scheduled; near the drain-down only up to the surface; the state field). With v1's forests every decision is
  unchanged (season fingerprints identical).
- **A/B (650 paired seasons per decision maker, H; v1's forests against the pack, both with this rule):** built-in —
  irrigation −5.4 mm a season (−6.5 … −4.4), pump energy −0.16 kWh (−0.20 … −0.13), methane −0.04 kg/ha (−0.06 …
  −0.02), yield, nitrogen, insecticide, biologicals and area sprayed unchanged, rejections 0 → 0, warnings +0.02
  (−0.002 … +0.04, not significant); continuous flooding — methane −0.08 kg/ha, everything else identical (it never
  reaches this rule; the harvest timing of F4 is the only change). Nothing is worse. Full table under "A/B of the
  decision changes".
- **Why the world is no easier.** Only the farm's own rule changed.

### F6. Every decision carried out by the arms (definitions addendum §1)
- **What changed.** v1 applied a decision to all its squares at the top of the hour. v2 hands it to an arm, and each
  square changes when the arm finishes it: a 12-square spray lands square by square, each with the weather at its
  minute; an irrigation starts when the watering arm has opened the inlet; a transplanting job is planted the same day
  but over minutes. The arm plan is stored in the decision record and in the frames, the state says which arms are busy,
  and a job that waits more than an hour for its arm is checked by the guardrails again before it starts.
- **Definitions.** Written into the addendum before any v2 result: spray efficacy at the arm's minute (and with the
  close-up look's identification of the pest), planting checked when the job is handed to the arm, the soil check on
  the probe reading.
- **Tests.** `tests/test_engine_arms.py` (every executed decision has an arm plan; queued → executing → executed; spray
  squares at their own minute; irrigation from the gate; busy arms in the state; season totals; same seed → same season;
  arms off = v1); `tests/test_arms.py` (72 squares within reach and joint limits, four arms at once never collide,
  submission order, determinism, preview = execution); `tests/test_scene_state.py` (the 3D view matches the twin).
- **Side effects.** With the arms off (`arms=False`), every decision and farm fingerprint is v1's
  (`test_arms_off_is_v1`). With them on:
  - *The kill is unchanged.* The pest arm reaches each square of a spray 0.65 minutes after the job is handed to it on
    average (at most 2.9; 247 sprayed squares in 12 seasons), and the kill at that minute differs from the kill at the
    hand-over by +0.0001 on average (−0.001 … +0.002).
  - *Seasons part ways from the first morning.* The planting arm probes the squares in the order of its route, so each
    square gets a different draw of the probe's noise than in v1 (the twin's random stream is shared), the amendments
    come out a little different, and from then on the cameras' and the pests' random draws follow another sequence.
    v1 against v2 is therefore season against season on the same field and weather, not decision against decision
    (0 of the 600 campaign seasons that have a v1 counterpart repeat v1's decision list; see the campaign comparison).
  - *A close look now counts 12 hours longer.* The arm's close look lands a minute or two after the 06:00 or 18:00
    decision point, so three days later, at the same decision point, it is 71.97 hours old and still inside the state's
    3-day "close look" window (`close_look_h_ago` 72), where v1's look, made on the hour, had just dropped out. A
    lingering low-level sighting is therefore looked at again 12 hours later than in v1 (in one season, 149 sightings
    showed a 72-hour-old look; v1 never shows one). Arms on against arms off, everything else equal (`tools/arms_on_off.py`:
    set H's first 300 cases, the built-in manager with v1's forests, the arms off, against the A/B run's arm A; table in
    `runs/models/arms_on_off_H.md`): scouting decisions 110.3 → 101.3 a season (−9.0, 95 % interval −11.7 … −6.4;
    kharif −4.1, rabi −14.0), spray decisions +0.06 (−0.28 … +0.40), area sprayed −1.1 m² (−3.1 … +1.0), insecticide
    −1.1 g a.i./ha (−2.5 … +0.3), Bt −0.03 kg/ha (−0.07 … +0.01), yield −0.002 t/ha (−0.008 … +0.004), irrigation
    +0.1 mm (−1.0 … +1.2), nitrogen +0.01 kg/ha, methane −0.002 kg/ha, guardrail rejections 0.03 → 0 a season.
  - Nothing on the brief's list (yield, water, nitrogen, insecticide, area sprayed, methane, guardrail rejections)
    changes significantly with the arms; the scouting cadence was left as it is, since changing it would alter the
    farm's decisions after every final result was produced (next round).
- **Why the world is no easier.** The arms add delay and order; the twin, weather and sensors are unchanged.

### F7. Speed-ups that change no number
- The twin's field progress cached per version; `Twin._cannot_reach` (a rigorous floor on the level skips the truth's
  73-hour loop when it can only return 73: 40 % of checks for the built-in manager, 70 % for conventional and the
  ablation; zero violations); the farm's level fit (scalar Gauss–Newton, 19 → 1.3 ms a call) and its tabulated
  projection (equal to `Twin.project_level_many` to 0.0 h); weather draws shared by both fields in the hour; and,
  without a time-to-AWD forest and without rows to keep, only the farm's estimate is computed (the posterior is needed
  by nothing then). Season fingerprints identical before and after each (`tests/test_paddy_features.py::LightPath`).

---

## Checksums

The two earlier projects were only read, never edited, rebuilt or re-run. Before v2 started, `tools/checksums/` recorded
a SHA-256 of every file in `/home/claude/agri-sim` (236 files, leaving out `test/tmp/`) and `/home/claude/agri-sim-py`
(61 files); `python tools/checksums/verify.py --v2` hashes them again and compares:

```
before: 2026-10-28T06:55:58Z   after: 2026-10-29T17:41:49Z   excluded: agri-sim/test/tmp/
agri-sim: 236 files before, 236 after; added 0, removed 0, changed 0; tree digest fb9bc47ca154ae63 → fb9bc47ca154ae63  UNCHANGED
agri-sim-py: 61 files before, 61 after; added 0, removed 0, changed 0; tree digest 6341559092f5514d → 6341559092f5514d  UNCHANGED
BOTH TREES BYTE-FOR-BYTE UNCHANGED
```

v1 of this project is kept: `release/paddy-api-farm-v1.zip` is a byte-for-byte copy of v1's zip (SHA-256
`62bd0e24d92ae165…`, the same as the original), and the SHA-256 of every v1 file was recorded before anything changed
(`tools/checksums/v1.json`); v1's results are kept in `tests/results/v1/`.

---

## Limits

- **Stand-ins only.** Every API result here comes from the mock, which repeats the built-in manager (terse reasons,
  a lot of scouting). How a real model decides, and what it costs, is only known once you run `check-api` and
  `compare` with your key. Token counts here are the stand-in's (characters ÷ 4); your provider counts its own way.
- **A model, not a farm.** Two 36 m² fields, hourly steps, one water level per field, idealised sensors. The
  agronomy is the original project's simplified crop model, with paddy parameters from its library; numbers are
  plausible against published ranges, not calibrated to a real farm.
- **Yields** are a little below Karnataka's state average in paddy terms (kharif at about the average) and close to
  the Bengaluru region's. The rabi penalty comes from the original crop library's sowing-month suitability and has
  not been checked against Karnataka's rabi and summer rice yields.
- **Methane** is IPCC Tier 1 (season-level factors), not a process model; nitrous oxide is not modelled (AWD can
  raise it).
- **Labels and doses** are a simulated list; the twin always applies the label dose.
- **The forests.** Five of v1's forests come unchanged from the Agri Robotics Playground, trained on its farms; the
  time-to-AWD, planned-spray and harvest-readiness forests are paddy forests trained on this farm's simulated seasons
  (v2). All are checked live; the soil forest falls below 95 % on some individual fields with many borderline soils,
  at the reading's own noise limit, and v1's yield forest over-forecasts conventionally managed fields by about 3
  points (94.5 % on the live set; see Below target in the model round).
- **`HttpActuators`** is a stub: nothing here drives real hardware.
- **Time** runs in whole hours: a spray asked for 17:30 runs at 18:00 (the spray advice only offers whole hours).
- **A spray decided for later can be decided again.** The state lists an irrigation or drainage scheduled for a later
  hour (v2, `fields.X.water.scheduled`) but not a scheduled spray, and the built-in manager's pest rules look only at
  sprays already carried out. So when it schedules a spray for the spray forest's best hour and a call comes before
  that hour, it decides the same spray again and both are carried out: 3 of 55 sprays in four recorded seasons (v1's
  and v2's demo seasons and one v1-campaign case with the arms off and on; v1 and v2 alike). The guardrails only catch
  duplicates within one reply. Listing scheduled sprays in the state and skipping a pest with one pending would change
  decisions, so it is left for the next round with its own A/B.
- **One farm per server**; runs are kept in memory (12) and in `runs/` as JSON lines.
- **The posterior assumes the forecaster it was built for.** The farm's weather posterior encodes the simulated
  forecaster's error model (its miss and false-alarm rates, its temperature error range, its chance-of-rain bands).
  That is the same information a real forecast post-processing system calibrates from verification statistics, but here
  it is exact. In the harder forecast mode (`--forecast realistic`: the hourly forecast is derived from the daily one)
  the model no longer matches; the results with and without it are in the model round.
- **A season costs about twice v1's compute.** Measured on this machine (`tools/models/cpu_per_season.py`, CPU time): a
  built-in season takes about 20 s of one core with the pack, 14 s with v1's forests, and 10.5 s with the arms off as
  well (v1's behaviour); conventional practice 10–11 s (6 s with the arms off). The arms are simulated at 0.25 s steps
  while they move, and the time to AWD runs 256 weather draws at each check.
- **The planned spray depends on the cameras' identification.** When they name the wrong pest the product may hardly
  work; the farm cannot know that when it decides (the arm's close-up look identifies the pest only when it sprays).
  This is the luck the planned-spray ceiling measures.
- **The arms' paddy-only phases** (turning a gate, spreading granules, puddling) have durations and powers that are
  this project's estimates, not measurements.
- **The 3D view draws 9 hills per square** (4 on phones) where a real square holds about 50; each hill is scaled to
  stand for its share.

---

## Why it is built this way

- **Forests advise, the model decides.** The brief. The advice is labelled, with each forest's live track record, so a
  model can weigh it; the `api-no-advice` ablation measures what it adds.
- **The same checks for every decision maker.** The built-in, conventional and mock managers answer in the API's own
  JSON and go through the same validation and guardrails, so comparisons are fair and the guardrails are exercised by
  over a hundred thousand ordinary decisions in the campaign, not only by tests.
- **Guardrails twice.** A decision scheduled for later is checked again when it runs, because the weather it was
  judged on can change.
- **The built-in manager is a pure function of the state.** It can run inside the mock server from the state alone,
  which proves the state carries everything needed to decide, and makes "mock = built-in" a hard test of the whole
  HTTP path.
- **Compact state.** About 2,150 tokens (v1: 2,000): short keys, grids as strings, only what matters at the season's stage
  (soil grids before transplanting, harvest advice near maturity). The explanations live once in the prompt.
- **Stdlib HTTP, no frameworks.** Nothing to install beyond NumPy, so it runs anywhere Python does; the server is
  small enough to read.
- **One clock, one lock.** Every request sees one consistent moment; the event stream keeps the website live without
  polling (polling is the fallback).
- **Determinism.** Seeds for everything random in the twin; replies recorded verbatim; replay is exact. Real API
  answers can't be reproduced, but recorded runs can.
- **Paired comparisons.** Every comparison runs the same fields and weather under each decision maker; differences
  come with confidence intervals.
- **Safe defaults.** The fallback is `hold`; real API calls in bulk need an explicit yes; the HTTP actuator is off.
- **Design.** The website opens on the two fields, the thing a farmer looks at first — in v2 on the 3D farm, with the
  plan one click away (3D / Plan, remembered): squares coloured by the chosen layer, rice tufts that grow with the
  crop, pest dots, and each field's water tube drawn as an instrument with the AWD line. The decision maker's own words are set in a serif face, machine readings in a sans with tabular
  figures, so "who said this" is visible at a glance. Numbers use Indian digit grouping (1,20,167), as a Bengaluru
  farmer would write them; the two fields' colours were checked for colour-blind readers in both themes.
- **v2: the 3D view plays the record.** Everything it draws is a function of what the twin recorded (frames, the arms'
  keyframes and plans, hourly weather, water and power), so the replay page shows exactly what happened and a test can
  compare the view's state with the twin's at every frame. The arms are never simulated a second time for the picture.
- **v2: the arms run in lock-step with the hourly twin.** The arm system steps through each hour right after the
  decision point, and every finished square is applied at its own minute before the hour's physics, so a long job
  is spread over the minutes it really takes and the season stays deterministic and replayable.
- **v2: the forecast's errors are modelled, not learnt.** The simulated forecaster's error structure (misses, false
  alarms, error ranges) is known; a forest can only approximate it from rows, while the posterior of the weather
  generator given the forecasts handles it exactly. The forests then correct what is left, anchored on that estimate,
  so where they have nothing to add they return it.
- **v2: the selection rule came first.** The rule for choosing the new forests was written down before the full
  training set existed and applied by a script, so the choice was not tuned on the results it is judged by; the test
  set compares candidates only, and the live set was run once, after the freeze.
- **v2: speed-ups that change no number.** Every speed-up was checked against season fingerprints recorded before it.

---

## Numbers and their sources

| Number | Value | Source or reason |
|---|---|---|
| Fields | 2 × 6 × 6 squares of 1 m² | the brief |
| Location, weather | Bengaluru, 12.97° N; the original weather generator | the brief |
| Seasons | kharif from 1 June, rabi from 1 November | the brief |
| Attainable yield | 5.5 t/ha | the original library; UAS Bengaluru reports 5.5 t/ha grain at the recommended dose ([UAS Bengaluru technologies 2025](https://www.uasbangalore.edu.in/wp-content/uploads/2025/06/technologies-E-2025.pdf)) |
| Transplanting-month suitability | yield ceiling × (0.7 + 0.3 × fit); fit 0.85 for June, 0.4 for November | the original crop library's monthly fit for paddy (not checked against Karnataka's rabi yields) |
| Recommended dose | 100:50:50 kg N:P₂O₅:K₂O/ha kharif, 120:60:60 summer | [UAS Bengaluru technologies 2025](https://www.uasbangalore.edu.in/wp-content/uploads/2025/06/technologies-E-2025.pdf) |
| Nitrogen splits | 50 % basal, 25 % tillering, 25 % panicle initiation | common package-of-practice split; the original farm manager's top-dressing pattern |
| Seasonal N cap / warning | 150 / 120 kg N/ha | the revalidated 125 % dose tops out at 150 kg N/ha in summer (UAS Bengaluru, above) |
| Urea N lost to the air | 8 % on a drained field rising to 30 % into ≥ 50 mm of water | 17–30 % from surface urea before flooding ([LSU AgCenter](https://www.lsuagcenter.com/portals/communications/publications/agmag/archive/2013/spring/reducingnitrogenvolatilizationlossesinriceproduction)) |
| Safe AWD threshold | re-flood at 15 cm below the surface | [CTCN/IRRI](https://www.ctc-n.org/technologies/alternate-wetting-and-drying-rice), [TNAU](https://agritech.tnau.ac.in/agriculture/pdf/csa_pdf/Smart_water_technique_for_rice.pdf) |
| Re-flood depth | 50 mm | TNAU: 3–5 cm (above) |
| Ponded around flowering | from about a week before flowering to a week after it (progress 0.50–0.78; the crop model's sensitive window is 0.55–0.75) | CTCN/IRRI: "from 1 week before to 1 week after the maximum flowering", about 5 cm (above) |
| Ponded after transplanting | 10 days, 20–50 mm | TNAU: 2 cm at transplanting and for 7 days (above) |
| Drain-down | at 90 % progress (about 12 days before maturity) | TNAU: last irrigation 15 days before harvest (above) |
| Last re-wetting before the drain-down | from 86 % progress (about 5 days before it) only up to the surface | assumption: standing water put on then would be drained off days later |
| Gap filling | 7–10 days after transplanting, from the same nursery | TNAU: "Fill the gaps if any within 7 - 10 days after planting" ([TNAU](https://agritech.tnau.ac.in/agriculture/agri_cropproduction_cereals_rice_tranpudlow_mainfield_stand_establish.html)); the guardrail allows it up to 15 days |
| Waiting-period margin | the managers keep 2 days in hand | assumption: the state rounds progress, the guardrail does not |
| Days from the surface to −15 cm | about 5–9 days (specific yield 0.30) | chosen to fall inside "1 to more than 10 days" (CTCN/IRRI, above) |
| Seepage and percolation | 5–9 mm/day ponded (1.5 + 0.08 × sand % mm/day through the pan, × (1 + depth/150 mm), plus 4 %/day of the depth through the bunds); × 2.5 before puddling | FAO: 4 (heavy clay) to 8 (sandy) mm/day ([FAO Irrigation Water Management, ch. 3](https://www.fao.org/3/S2022E/s2022e08.htm)) |
| Land soaking | about 125 mm to flood from −250 mm | FAO uses 200 mm to saturate plus 100 mm standing (above); the twin starts kharif from a moist profile |
| Maximum target / bund | 100 mm / 150 mm | FAO's standing layer of 100 mm (above) as the deepest allowed; a bund above it |
| Pump | 3 m³/h shared, 0.42 kWh/m³ | a small farm pump; energy per m³ from the original project |
| Methane | 0.85 kg CH₄/ha/day × 1 / 0.71 / 0.55 (continuous / single / multiple drainage) × compost factor × days | IPCC 2019 Refinement Tier 1 baseline for South Asia and water-regime factors ([table of IPCC factors](https://greencalculus.com/data/afolu-rice-cultivation-factors/)); compost conversion factor 0.17, exponent 0.59 (same) |
| Compost dry matter | 60 % | assumption, for the IPCC organic-amendment factor |
| CH₄ global warming potential | 27 | IPCC AR6, non-fossil methane, 100 years |
| Label doses and waiting periods | see the table under Guardrails; active ingredient counted at the upper end of a label range | CIB&RC major uses as compiled by Junagadh Agricultural University: [insecticides](https://new.jau.in/storage/document/dynamic_document//residue_insecticide_30092025_20251226_783.pdf), [bio-insecticides](https://new.jau.in/storage/document/dynamic_document//residue_bio_insecticide_03102025_20251226_779.pdf); [Gharda chlorantraniliprole label](https://www.gharda.com/?p=488) |
| Rain before spraying | none ≥ 2 mm in 6 h | the twin's spray model: 2 mm in 6 h already cuts the kill by about a quarter |
| Wind | ≤ 4 m/s | the twin's spray model loses 12 % of the kill per m/s above 2.5 m/s; labels advise against spraying in adverse weather (Gharda, above) |
| Spraying hours | 05:30–20:30 | the original farm's working window |
| Bee-toxic products | flowering crop: dawn or dusk only, severity ≥ 0.25 | the original farm manager's pollinator rule; imidacloprid's bee toxicity 0.95 in the library |
| Action threshold | 8 % severity | the original library's economic threshold |
| Pest risk "act" level | calibrated 0.545 (raw forest score 0.1) | the original farm manager |
| Transplant rule | field mean establishment ≥ 72 % | the original farm manager and definitions |
| Seedlings | 18 days old on day 0; penalty above 30 days | transplanting on day 2–3 uses 20–21-day seedlings; TNAU: 18–22 days for short-duration and 25–30 for medium-duration varieties ([TNAU](https://agritech.tnau.ac.in/agriculture/agri_cropproduction_cereals_rice_tranpudlow_mainfield_stand_establish.html)) |
| Practice factors on establishment | water > 50 mm: −0.5 %/mm (floor 30 %); < −30 mm: −0.5 %/mm (floor 60 %); seedlings > 30 days: −1 %/day (floor 80 %) | assumptions; TNAU: deeper water than 2 cm at transplanting means fewer tillers (above) |
| Heavy-rain trigger | ≥ 64.5 mm/day forecast | IMD's "heavy rain" class |
| Decision hours, budgets | 06:00 and 18:00; 6/day, 600/season, 240/real hour | the brief; 2 scheduled + 4 trigger calls a day; about twice a season's scheduled calls; a cap on spending if something loops |
| Karnataka yields | 3,012–3,089 kg of rice per ha, 2018-19 to 2022-23 | [RBI Handbook of Statistics on Indian States, Table 85](https://www.rbi.org.in/Scripts/PublicationsView.aspx?id=22146) |
| Paddy-to-rice out-turn | 67 % | the out-turn ratio used for raw rice in government milling ([CAG report 31 of 2015](https://cag.gov.in/uploads/download_audit_report/2015/Union_Performance_Procurement_Milling_of_Paddy_Central_Pool_31_2015.pdf)) |
| Bengaluru region | Bangalore Rural 2,749 kg of rice per ha, state 2,521 (triennium to 2000-01) | [DRD Patna rice productivity analysis, Karnataka](https://www.drdpat.bih.nic.in/PA-Table-10-Karnataka.htm) |
| AWD water and yield | mild AWD: −23.4 % water, no significant yield loss | [Carrijo, Lundy & Linquist 2017, Field Crops Research 203:173–180](https://pure.psu.edu/en/publications/rice-yields-and-water-use-under-alternate-wetting-and-drying-irri/) |
| AWD methane | −41 % for safe AWD | [Balaine et al. 2019, SSSAJ 83:1533–1541](https://pure.psu.edu/en/publications/greenhouse-gases-from-irrigated-rice-systems-under-varying-severi/) |


The remaining settings are engineering choices rather than agronomy; each with its reason:

| Setting | Value | Reason |
|---|---|---|
| Specific yield of the puddled topsoil | 0.30 | sets how fast the water table falls once the standing water is gone: surface to −15 cm in about 5–9 days, inside IRRI's "1 to more than 10 days" |
| Percolation vs head; where it stops | × (1 + depth/150 mm) while ponded; stops when the water table is 100 mm down | flow through the plough pan grows with the ponded head; a deep perched water table loses its connection (finding 1 under the results) |
| Nitrogen leaching | 0.015 % of available N per mm percolated × (0.4 + sand share) | about a tenth of the first version's, so flooded and AWD fields keep the same yield (finding 3) |
| Water tube | ±3 mm, reads to −300 mm | precise enough for the ±10 mm decisions it drives; deep enough to show how far below the AWD line a field has dried |
| Pump and outlet | 3 m³/h shared; outlet 40 mm/h | a small farm pump fills a 36 m² field from −150 to +50 mm (about 3.4 m³ with the specific yield) in a little over an hour, about one simulation step; the outlet empties 50 mm of standing water in a little over an hour |
| Camera passes; soil scan | every 2 h from 06:00 to 18:00; daily at 06:00 | the original farm's schedule |
| Gap between trigger calls | at least 1 simulated hour | one call per step at most, so a burst of events can't run up the bill |
| Decisions per reply | at most 12 | a busy morning on both fields needs about 8–10 (the built-in manager's busiest calls); more is a runaway reply |
| "when" / "wait" | at most 48 h / 72 h ahead | the forecast reaches 3 days; a decision shouldn't outlive the forecast it was made on |
| Transplanting deadline | day 75 of the season | later, a 125-day crop would ripen far into the next season's weather; the field is abandoned instead |
| Forced harvest | progress ≥ 1.35, day 229, or no live plant for 20 days | ends a season whose decision maker never harvests (never needed in the campaigns) |
| Nursery | sown 18 days before the season opens | seedlings are 20–21 days old on a day-2–3 transplanting, inside TNAU's 18–22 days |
| Built-in manager: re-flood at | −140 mm | decision points are 12 h apart, so it acts a little before the −150 mm line |
| Built-in manager: transplant anyway | when seedlings reach 26 days | before they pass the 30-day warning |
| Built-in manager: top-dressings | tillering from 18 days after transplanting, panicle initiation from 45 % progress, into ≤ 50 mm of water | package-of-practice timing; shallow water loses less urea to the air |
| Built-in manager: harvest | readiness forest ≥ 60 % of plants and dry grain, or progress ≥ 1.12 | the original farm manager's rule |
| Conventional calendar | sprays 20, 35, 50 and 65 days after transplanting; urea at 21 and 45 days; reactive spray at ≥ 15 % severity | a common fixed calendar, with damage obvious to the eye |
| Spray counts per square | warning above 4, rejected above 8 in a season | far above what the built-in manager needs (at most 2–3); stops a runaway sprayer |
| Same IRAC group on a square | warning within 14 days | rotate modes of action between pest generations (resistance management) |
| Fertiliser caps per application | urea 130, DAP 250, MOP 150, SSP 400, complex 300 kg/ha | about twice the largest single application in the recommended doses |
| Amendment caps per application | lime 400, gypsum 500, sulphur 100, compost 3,000 g/m² (4, 5, 1 and 30 t/ha), leaching 80 mm; twice that per season | the upper end of reclamation doses in one go; more is a typo or a runaway |
| Wet grain at harvest | ≥ 6 mm of rain in two days | the original harvest-readiness rule |
| Immature harvest | median progress < 0.96 | the original definitions' readiness assessment starts at 0.96 |

**Numbers added in v2**

| Number | Value | Source or reason |
|---|---|---|
| Seedlings drawn at transplanting | tufts at least 20 cm tall | seedlings are pulled at the 4th-leaf stage ([TNAU](https://agritech.tnau.ac.in/expert_system/paddy/cultivationpractices2.html)); seedlings over 20 cm are topped to 15 cm at transplanting (IRRI's B. Vergara, *A farmer's primer on growing rice*, via [Farm Radio](https://scripts.farmradio.fm/radio-script/choosing-transplanting-rice-seedlings/)) |
| Hills drawn per square | 9 (light render 4) | a real square holds about 50 hills at Karnataka's 20 × 10 cm spacing (TNAU, above); the 3D view draws fewer for the triangle budget and scales each hill |
| Soil colour and cracks | wet above −20 mm; cracks from −150 mm, fully cracked by −300 mm | a display rule tied to the safe-AWD line (15 cm below the surface, CTCN/IRRI and TNAU in the v1 table) |
| Panicles / golden crop | from progress 0.48 / 0.72 → 1.0 | display rules placed on the crop model's stages (panicle initiation, then grain filling and ripening) |
| Paddy-only arm phases | gate 6 s at 120 W; spreading 3 s at 100 W; puddling 8 s at 450 W | this project's estimates for a small geared valve actuator, a spinner spreader and a rotary puddler on the watering and planting arms |
| Arm docks | 3.0–3.1 m apart | the four arms parked closer than the make-way distance forced 1,271 make-way moves in a test season; 3 m brought it to 9 |
| Power plant | 1.5 kWp PV, 6 kWh battery | `energy.size_for` (the playground's own sizing rule) on the 78 m² frame |
| Time-to-AWD draws | 256 per check | 64 draws scored 97.4 % on a built-in rabi season, 256 scored 99.3 % (the posterior's own Monte Carlo error) |
| Weather posterior proposals | 3,000 per day (30,000 where a rain rate was seen); 48 temperature proposals | enough that every true day has positive weight in the likelihood test (1,624 of 1,624 days tested, none inside an unannounced event) at an acceptable cost |
| Seen rain rate matched within | ± 2 % | an hour the forecaster does not miss carries the hour's true rate, so a candidate day's shower must match it; within ± 2 %, then the forecast's rate is used exactly — enough candidates survive while no impossible day does |
| Level fit | the last ≤ 48 hourly readings, weights e^(−age/24 h) | two days keep the fit inside one crop stage; older readings count less because the crop's water use drifts |
| Skip the posterior when | a dry run with 25 % more evapotranspiration than forecast cannot reach the line within 76 h | every draw would then be past 72 h (exact: no draw can fall faster than the driest, thirstiest case) |
| Planned-spray draws | 128 per spray | the rain of six hours is the only drawn part; 128 draws put the cover point's Monte Carlo error under the ±5-point tolerance |
| Built-in AWD lookahead | 12 h | decision points are 12 h apart: in v1's margin (−150 < tube ≤ −140 mm) the field keeps drying when the time-to-AWD forest expects the line only after the next decision point, and is re-flooded at once otherwise |
| Forest sizes | time to AWD 12 trees (7,386 nodes), planned spray 30 trees (101,390 nodes), harvest 10 trees (240 nodes) | chosen on the training holdout by the rule fixed in advance (highest minimum over decision makers and seasons, then pooled, then smaller); the whole pack is 0.66 MB, small enough for the replay page |

---

## Files

```
paddy-api-farm/
  README.md  LICENSE  .env.example  .gitignore
  backend/
    requirements.txt  pyproject.toml
    paddy/
      __main__.py  cli.py  config.py  engine.py  server.py  openapi.py  runs.py  fuzz.py  replay.py
      datasets.py  modelround.py      v2: the data sets (G, T, L, H, W), their rows, the scoring, the paddy pack
      core/        copied unchanged from the Agri Robotics Playground's Python port (+ the trained forests);
                   v2: paddy_features.py, models/paddy_forests.npz + .json (the pack)
      robots/      v2: planner.py  arms.py  energy.py (copied from the port)  layout.py  system.py (the four arms)
      sim/         twin.py  advisor.py  audit.py  wxpost.py (v2: the weather posterior)
      decisions/   schema.py  state.py  prompt.py  rules.py  guardrails.py  managers.py  actuators.py
                   records.py  api_client.py  mock_api.py  adapters/
  frontend/
    package.json  vite.config.js  build.mjs  index.html  src/  public/fonts/  dist/
    src/three/     v2: gl.js  geometry.js  scene.js (the playground's renderer and scene)  view.js (the record → 3D)
    src/components/Farm3D.jsx  ArmPanel.jsx   v2: the 3D farm and the arm panel
    dist-replay/   paddy-replay.html: the recorded demo season, playable without a server (with the 3D farm)
  runs/
    demo-kharif/   the demo season through the stand-in: every call (state sent, raw reply), decision and arm track
    models/        v2: the model round's tables, candidates, final forests, ceilings and A/B results
    datasets/      v2: every season of the model round, one record and its rows per season (515 MB, so not in the
                   zip; `python -m paddy batch …` rebuilds them exactly, in the order of tools/models/RUNBOOK.md)
  tests/
    test_*.py      schema, guardrails, API client, engine, twin, managers, REST server, .env reader;
                   v2: arms, engine with arms, 3D state, weather posterior, paddy features, model round
    e2e/           mock_seasons.py (HTTP seasons)  determinism.py  ui.js  replay_ui.js  farm3d_ui.js  scene_state.mjs
                   farm3d_controls.js (v2: orbit, pan, zoom, tilt)  farm3d_fps.js (v2: frame rates)
                   campaign_resumable.py  campaign_v1_v2.py  fuzz_chunks.py  analyse_campaign.py  results_md.py
    results/       campaign, fuzz, end-to-end, determinism and browser results; screens/  screens3d/
                   replay-screens/; v1/ (v1's)
  docs/            DEFINITIONS.md (copied unchanged)  DEFINITIONS-paddy.md  DEFINITIONS-paddy-v2.md (the v2
                   addendum, written before any v2 result)  models-frozen-v2.txt (the freeze record)  screens/
  tools/           package.py (the zip)  arms_summary.py  arms_on_off.py (v2: arms on against off)  readme_results.py
                   (v2: the README's results block from tests/results/)
                   checksums/ (before.json  after.json  verify.py  report.txt; v2: before-v2.json  after-v2.json
                   report-v2.txt  v1.json  definitions-v2.sha256)
                   models/ (v2: RUNBOOK.md and every script of the model round: dev_awd_plan.py  train.py
                   select_rule.py  assemble.py  freeze.py  report.py  before_after.py  readme_tables.py  ab.py
                   ab_table.py  identity_check.py  ceiling_*.py  realistic_table.py  cpu_per_season.py  compute_table.py)
  release/         paddy-api-farm.zip (v2)  paddy-api-farm-v1.zip (v1, byte for byte)
```
