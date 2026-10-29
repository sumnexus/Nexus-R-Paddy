# Addendum for v2 — written 28 October 2026, before any v2 result

`DEFINITIONS.md` (the playground's definitions, round 4 included) and `DEFINITIONS-paddy.md` (how they map onto the two
paddy fields) stay exactly as they are. This addendum adds what v2 needs. It is not changed after any v2 result has
been seen; its SHA-256 is recorded in `tools/checksums/definitions-v2.sha256` on the day it was written.

## 1. The arms

v2 carries out every decision with the four arms (`backend/paddy/robots/`). Where a definition refers to the moment
something is done, that moment is now the arm's:

- **Spray efficacy** — "from what the farm knows when the arm sprays": each square at the minute the pest arm sprays it
  (its close-up look identifies the pest), with the weather station's readings at that minute and the hourly rain
  forecast issued then for the six hours after it. Truth: that square's kill with the rain that actually fell. (v1 used
  the top of the hour for every square of a decision.)
- **Planting day** at the transplanting itself: the check is made when the transplant job is handed to the planting
  arm (the top of the hour). Every square of a job is planted the same day, so the three-day weather window of the
  prediction and of the truth is the one the seedlings actually meet.
- **Soil suitability** is scored on the probe reading, as written. v1 scored the first field probed on each morning on
  the daily scan instead, because the probe did not refresh the farm's cached soil advice (finding F1 in the README).

## 2. Time to AWD: the time-to-stress forest's paddy job

- **Who is scored:** at every 06:00 and 18:00 decision point, each field that is transplanted, not harvested or
  abandoned, before the drain-down (field progress < 0.9), with no irrigation running (inlet not open), whose water
  level is above the AWD threshold in force at that moment (−150 mm; 0 mm from progress 0.5 to 0.78).
- **Prediction:** hours until the field's water level (what the water tube measures, without its ±3 mm reading noise)
  falls to that threshold if nothing is irrigated, from what the farm knows: the tube reading, the forecasts as issued,
  and what it knows of its soil and crop.
- **Truth:** the twin's own water balance run forward hour by hour from the field's true level — evapotranspiration of
  the crop as it grows, percolation, bund seepage, rain — with no irrigation and no drainage, and the weather that
  actually came. A check is scored once its 72 hours have passed (or at the end of the season with the weather then
  known); the truth is capped at 73 h.
- **Pass:** for checks whose truth is under 72 h, within ±6 h or ±20 % of the truth, whichever is larger, ≥ 95 %.
- **Use:** in the built-in manager's AWD timing and as advice to the API (each change that alters decisions is A/B
  tested over at least 200 paired seasons).

## 3. Spray efficacy, planned: now a target

- **Prediction:** when a spray is decided, for each square on it, the kill the farm expects at the hour the spray is to
  run, from the hourly forecast issued at the decision for that hour and the six hours after it, the product, the sun
  at that hour, and the pest the cameras see on the square (or the decision's target pest) with its tracked age
  carried forward to that hour.
- **Truth:** that square's actual kill (as for spray efficacy).
- **Pass:** within ±5 points ≥ 95 %; where the measured ceiling is below 96 %, within 1 point of the ceiling.
- **Ceiling, measured by re-simulating the same cases:** each scored square is re-drawn 200 times, varying only what
  the farm could not know when it decided, and each draw is scored with the farm's own spray rule
  (`agro.spray_efficacy`):
  - the weather at the minute the arm sprayed, given the forecast the farm had: temperature, humidity and wind from
    the hourly forecaster's own error model (temperature ± (0.3 + 0.04 × lead in hours) °C, humidity ± 4 points,
    wind × 0.8–1.2, uniform as the forecaster makes them), inverted with a locally flat prior (uniform for temperature
    and humidity, log-uniform for wind); rain in each of the six hours after it: an hour forecast with rain at a rate
    other than the forecaster's false-alarm value of 1.5 mm/h rains at that rate; an hour forecast dry, or at the
    false-alarm value, rains with the probability that such a forecast hides rain (the forecaster's miss and
    false-alarm rates combined with that month's climatological chance of rain in that hour), at a rate drawn from
    that month's wet hours; the climatology comes from 2,000 weather years of the generator (weather years
    90,001–92,000, used for nothing else);
  - the pest's true infestation age given its tracked age, drawn from the training seasons' (true − tracked) ages
    for that pest category;
  - the pest itself where the cameras may have misidentified it, drawn from the training seasons' share of true
    species for each (seen species, identification votes).

  The best prediction for a square is the value within ±5 points of the most draws; the square's ceiling is that
  share of draws; a set's ceiling is the mean over its squares (and likewise per decision maker and season). Chosen on
  its own draws it flatters itself a little, so the 200 draws are also split in halves (choose on one, score on the
  other) and both numbers are reported. Each draw counts as one re-draw towards the ≥ 100,000.
- **Other ceilings if needed** (a model below 95 % on a check with ≥ 20 cases): pest risk — each case's 7-day
  projection re-run 200 times with the weather beyond the 3-day forecast drawn from the same calendar days of other
  weather years and the true severity behind the camera reading drawn from the camera's error model; time to AWD —
  each check's projection re-run 200 times with the 72 h weather drawn from the daily forecast's error model; soil — the
  playground's 200 probe re-draws. Each is reported as measured, with how it was drawn.

## 4. The new sets

Seeds and weather years are disjoint from each other and from every v1 run (v1: seeds 7,100,000–7,100,199 with weather
years 3,001–3,200; 7,300,000–7,300,024 with 5,001–5,025; 5,100–5,109 with 101–110; 7,400,011–7,400,012 with
6,001–6,002; the default seed 20,261,024 with year 0; the unit tests' small seeds). Case *i* of each set:

| Set | Purpose | Seed | Weather year | Size |
|---|---|---|---|---|
| G | training | 8,100,000 + i | 10,001 + i | as many as the models need (i < 10,000) |
| T | test: compares candidate models only | 8,200,000 + i | 20,001 + i | 200 cases × 4 decision makers |
| L | live: run once, after every model is frozen | 8,300,000 + i | 30,001 + i | 500 cases × 4 decision makers |
| H | A/B checks of decision changes | 8,400,000 + i | 40,001 + i | ≥ 200 paired cases per check |
| W | HTTP seasons through the stand-in (final checks) | 8,500,000 + i | 45,001 + i | ≥ 100 |
| K | climatology for the planned-spray ceiling (weather only) | — | 90,001 + i | 2,000 years |

- **Season:** kharif for even *i*, rabi for odd *i*.
- **Events:** each case draws events with probability ½ (from a hash of its seed): one to three of pest outbreak,
  heavy storm, dry spell and overcast week, each on a day drawn from days 10–120 (pest outbreaks from days 30–100, so
  that there is a crop to infest) at 08:00. A pest outbreak strikes field A or B (drawn) with the pest drawn by the twin.
- **Decision makers:** built-in (AWD; also what the API stand-in answers); conventional; the API without advice,
  through the stand-in (the HTTP client and its prompt, adapter, parsing and validation, with the transport handing
  each request to the stand-in in the same process); and the continuous-flooding ablation (built-in with continuous
  flooding). In T and L every case runs under all four, so comparisons are paired. In G each case runs under one of
  them, in turn by *i*.
- The 200 cases of v1's campaign (seeds 7,100,000–7,100,199, weather years 3,001–3,200, no events) are run again only
  to compare the v1 and v2 farms season by season — never to train, select or score models.
- **Check:** before any set is run, every pair of sets (and every v1 run) is checked for a shared seed or weather year;
  the report states the count, which must be zero.

## 5. Targets

- Every model at least 95 % on T and on L (pest risk: accuracy and balanced accuracy), pooled and separately for each
  decision maker and each season (kharif, rabi), wherever a check has at least 20 cases there; where luck makes 95 %
  impossible, within 1 point of the measured ceiling, with the proof.
- Also reported (not pass marks): the share of seasons at ≥ 95 % (among seasons with ≥ 20 cases of that check) and the
  lower end of each model's 95 % interval from 10,000 bootstrap draws of whole seasons.
- Every rate is reported with its case count and a Wilson interval, and with the season-bootstrap interval.
- The models frozen before L are recorded (file hashes) in this folder before L is run.
