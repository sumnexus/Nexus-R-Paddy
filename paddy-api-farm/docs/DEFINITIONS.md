# How accuracy is measured (fixed before any tuning)

Written down on 25 October 2026, before any model was retrained, from the definitions in the
task. The live audit (`src/core/audit.js`) and the offline scorer (`test/acc/data.js`, `pass` and
`score`) implement exactly these. They are not changed after seeing results.

## Where the numbers come from

- **Test set:** rows recorded in held-out simulated farm-years (set **T**) — seeds and weather years
  never used for training, every farm size and bed layout, every start month, every strategy that
  uses the models, half the farms with events (pest outbreaks, dry spells, storms, overcast weeks)
  and a fifth with randomly varied imported crops. Nothing is dropped. Each model is scored on what
  the farm actually faced, with the truth worked out by the simulator.
- **Live checks:** the same audit running inside 300+ full farm-years (set **C**, again new seeds and
  weather years) with the final models making the decisions.
- **Training data** comes only from other seeds and weather years (set **A** farm-years and the
  synthetic generators, whose weather series and seeds are disjoint from T and C).
- Every rate gets a 95 % confidence interval (Wilson), and a per-crop breakdown (crops with at least
  20 cases; imported varieties are grouped). "Worst crop" is the lowest per-crop rate.

## The seven models

| Model | Who is scored | Prediction | Truth | Pass |
|---|---|---|---|---|
| Soil suitability | every square the planting arm probes | class with the highest probability, from the probe reading | the agronomic rules applied to the square's **true** soil (no flipped labels) | correct class ≥ 95 % |
| Pest risk | once a day at 08:00: every live plant in a bed where the cameras see a pest, for each pest seen there that can attack the crop, if the plant's own severity of it is seen below 8 % | ≥ 0.5 means "will reach 8 %", from what the farm knows (3-day forecast, camera readings, residue) | whether the plant's severity of that pest reaches 8 % within the next 7 days **if nothing is sprayed** — the farm's own pest dynamics run forward with the actual weather | accuracy ≥ 95 % **and** balanced accuracy (mean of the two recalls) ≥ 95 % |
| Yield forecast | every bed season that finishes | the forecast made when the bed's crop was planned | harvested kg ÷ attainable kg over every square planted in the season (failed and replanted squares included) | within ±10 points for ≥ 95 % of seasons |
| Planting day | at every planting check, every square of every bed due to be planted that day, planted or postponed | the chance the square establishes if planted now | the simulator's true chance for that square: its true soil moisture and the **actual** next three days' weather | within ±5 points ≥ 95 % of squares; **and** per bed and day, "plant" (average chance ≥ 72 %) matches the same rule on the true chances ≥ 95 % |
| Time to water stress | every non-paddy plant the 05:30 and 15:30 irrigation checks assess | hours until stress, from the sensed soil moisture and the forecast | hours until the root zone reaches the crop's stress limit if not irrigated: the true soil moisture run forward with the farm's own water balance and the actual weather | for plants whose truth is under 72 h: within ±6 h or ±20 % (whichever is larger) ≥ 95 % |
| Spray efficacy | every spray the pest arm applies | share of pests killed, from what the farm knows when the arm sprays (measured wind, temperature, humidity and sun; the hourly rain forecast for the next 6 h; product, pest, infestation age) | the share actually killed (with the rain that actually fell) | within ±5 points ≥ 95 % |
| Harvest readiness | every plant a harvest round assesses | ≥ 0.5 means ready | the ready rule when the arm arrives (plants sent), or at the assessment (plants not sent) | correct ≥ 95 % |

Also reported, not part of the pass mark: spray predictions made when a spray is planned (hours
ahead, from the forecast); mean absolute errors; recall of each class; calibration.

## Ceilings

Where an outcome is partly luck, 95 % may be impossible. The ceiling is the score of the best
possible prediction using only what the farm knows at that moment:

- the truth is re-simulated many times for the same case, varying only what the farm cannot know
  (the weather beyond what the forecast says, sensor noise, random draws), and the best prediction
  is the one that scores highest on average over those draws;
- where the unknowns can't be re-drawn case by case, the ceiling is estimated from a much larger
  set of simulated cases (k-nearest-neighbour average of the truth over millions of cases with the
  same knowable inputs) and reported as an estimate.

A model that can't reach 95 % must get within 1 point of its ceiling, with the proof.

## Addendum (25 October 2026, after the first results): how the ceilings are actually computed

The accuracy definitions above are unchanged. Re-drawing the unknowns case by case turned out to be
possible for some models and not for others, so each ceiling below says which kind it is:

- **Soil suitability:** each probed square is re-drawn 200 times with the probe's noise (pH ±0.03,
  N and K ±3, P ±0.6, EC ±0.02; texture and carbon exact) and the rules applied to each draw. This
  assumes nothing about which soils are common, so a model that has learned the farm's soils can
  edge past it; it is reported as an estimate.
- **Yield forecast:** the same farms are re-run with many other weather years. Same seed, same
  field, same first scans and the same day-1 crop plans (outcomes are grouped by bed and the crop
  chosen); the weather, pests, sensor noise after the first scans and failed seedlings are
  re-drawn. For each first-season bed the best possible forecast is the value within ±10 points of
  the most outcomes. Chosen on the same outcomes it flatters itself, so it is also chosen from each
  bed's other weather years (leave one out); the ceiling lies between the two.
- **Time to water stress, planting day:** the forecast in this simulator is generated from the
  actual weather with its own noise, and re-drawing "weather that would have produced this forecast"
  exactly is not feasible. These get an **upper bound** instead: the best possible score for a
  predictor that also knows the actual future weather, re-drawing only what the farm still can't see
  (the true soil water behind its filtered estimate, using the filter's measured error). The true
  ceiling lies between the model's score and that bound.
- **Pest risk:** a plant that passes 8 % with no camera sighting on it or within two squares, and no
  outbreak announced in its bed, can't be flagged by anything the farm knows. The bound assumes
  every other positive is caught and no negative is flagged. Plants with a sighting within two
  squares are recorded in full; the others are counted from the audit's tallies, and since such a
  plant can only be caught through an announced outbreak, every one the model caught was announced.
  (A first attempt re-drew the true severity around the camera reading; it ignored what the bed's
  other plants say and came out no higher than the model, so it is not used.)
- **Spray efficacy, harvest readiness:** the outcome is the farm's own deterministic rule on
  conditions it measures, apart from rain in the next six hours and the true age of the infestation
  (spray), and the time — sometimes hours — until the arm arrives (harvest); the ceiling is close to
  100 % and the models are scored against 95 %.

## Addendum (25 October 2026, after the final runs): reporting only, definitions unchanged

- **Training sets:** besides set A, the final models also learned from set **D** (80 farm-years run
  with interim versions of the new models, so their records match the farm's current sensors) and
  set **E** (90 farm-years with the final spray rule). D's and E's seeds and weather years are
  disjoint from T and C like A's. Pest risk and time to stress learned from D, yield from D and E
  (A was recorded with the old soil-water filter and camera handling).
- **Which set is clean:** T was used to compare candidate models (for example three versions of the
  planting-day model), so C — run once, after every model was frozen — is the untouched confirmation.
- **A second interval:** predictions made inside one farm-year share its weather, soil and crops, so
  they are not independent, and a per-prediction (Wilson) interval is too narrow. Every rate is
  also reported with a 95 % interval from resampling whole farm-years (10,000 bootstrap draws,
  `test/acc/cluster_ci.js`). The pass mark is still the measured rate on T and on C.
- **The mix as drawn:** "half the farms with events and a fifth with randomly varied imported crops"
  is the design (each farm-year draws them with probability ½ and ⅕). T drew 53 of 100 with events
  and 14 with varied crops; C drew 143 and 57 of 300.

## Addendum (25 October 2026, after an independent review of the first final results)

Two changes to how the truth is worked out, both applying the written definitions more faithfully
and both making the scores harder, not easier. The earlier way is still computed alongside and
reported, so the effect can be seen.

- **Time to stress and pest risk use the weather that actually came.** The definitions say "the
  actual weather". The first runs projected each check with the weather as it stood at the check, which
  misses a dry spell, storm or overcast week that starts later inside the window. Each check is now
  kept and scored once its window (72 h, 7 days) has passed, with the weather that really fell. At the
  end of a run, checks whose window hasn't closed are scored with the weather known then.
- **Pest risk, "any cause".** "Reaches 8 % within 7 days" read literally also counts a plant that a
  pest-outbreak event strikes later in the week (18–38 % on up to nine plants at once). The fixed
  definition — the plant's own infestation run forward — leaves those out, and stays the pass mark.
  The literal version is reported next to it, with its ceiling: no prediction from anything the farm
  knows can foresee a scheduled outbreak.
- **Rows:** a stress row's truth is now capped at 73 h (only plants under 72 h are scored); a pest row
  also stores `yAny` and `struck`.

Things the audit or the farm still idealise, kept from the original simulator and stated here:

- A camera sighting is dropped half an hour after the pest is really gone; a real system could only
  drop it after several empty passes. The harvest model reads each plant's fruit load and health
  exactly (the cameras are assumed to count fruit). The record of the last product on a square ends
  when its true residue falls below 2 %, and spray drift is logged only on neighbours with little
  true residue (both are things the farm could work out from its own spray log).
- Spray efficacy is also scored on the clean plants of a whole-bed spray (strategy without spot
  spraying), on the kill the spray would have had. Plants sent to the harvester that it never reaches
  are scored on the assessment.
- The yield ceiling keeps the beds whose first season finished within the 230 simulated days in at
  least 60 % of the weather years.

## Round 4 (26 October 2026, written before any round-4 result): new sets, per-crop targets

Nothing above changes: the seven definitions, both review corrections (stress and pest scored with the weather
that actually came; pest "any cause" reported beside the fixed definition as a reading, not a target) and the
scoring code stay as they are.

- **New sets.** **T2** (test, 200 farm-years, weather years 40000–40199) is used to compare candidate models;
  **C2** (live, 500 farm-years, weather years 41000–41499) is run once through the Simulation Lab's run loop after
  every model is frozen and is the clean confirmation; **G** (training, weather years from 50000) and **H** (A/B
  checks of farm decisions, from 60000) never overlap them. Seeds come from each set's own hash; a check over every
  earlier set (A, B, C, D, E, T, Q, S, P, K, Y and the yield-ceiling re-runs) found no shared seed or weather year.
  T2 and C2 keep the earlier mix (every size and layout, every start month, every model-using strategy, events with
  probability ½, randomly varied crops with probability ⅕); G grows varied crops in 35 % of its farm-years.
  The shipped v3 models are also run on T2, unchanged, to give the "before" on the new test set.
- **Targets.** Every model at least 95 % on T2 and on C2 (pest: accuracy and balanced accuracy). New: every crop at
  least 95 % on both sets too (crops with at least 20 cases; randomly varied imported crops as one group), or within
  1 point of that crop's measured ceiling, with the proof.
- **Also reported, not pass marks:** the share of farm-years at 95 % or more, and the lower end of each model's
  farm-year 95 % interval (10,000 bootstrap draws of whole farm-years).
- **Optional settings** (off by default, reported with and without): realistic forecasts (`forecastMode:
  'realistic'`: Student-t errors on rain amounts and temperatures, misses and false alarms, no hour-level timing),
  the stricter camera (`cameraMisses: 4`), noisy fruit counts (`fruitNoise: 0.15`) and a pheromone-trap early
  warning (`pestTraps`, 12 W). In the realistic-forecast mode the stress and planting-day ceilings are measured by
  re-simulating each check under many weather draws weighted by the forecasts and readings the farm had
  (`test/acc/r4/ceiling_realistic.js`); in the default mode they stay upper bounds, for the reason given above.
- **Simulations** are counted as independent runs of the farm's physics to a scored outcome (a farm-year counts
  once; the predictions inside it are checks); crop seasons inside farm-years are reported as such.

## Round 4 freeze (26 October 2026, 15:40 IST, before the final T2 run and the C2 live run)

The models below are frozen in `test/tmp/v4/core.js` (the page's `dist/core.js` of this build). T2f (the 200 test
farm-years again, with these models) and C2 (500 live farm-years through the Simulation Lab, run once) use that file.

- **Planting day:** two forests. Direct-seeded crops use `est_x2d` (its first 30 of 40 trees) and transplanted crops
  `est_x2t` (first 30 of 40), packed as `establish` and `establishT`. Both learned from 1.5 million simulated checks
  (crop mix: 50 % library crops, 25 % jittered, 25 % invented, so that imported crops are covered) plus every recorded
  farm check of their type from sets A, D, E and G (G had about 140 farm-years then); 30 % of the direct-seeded
  forest's simulated checks fall on days with at least 25 mm of rain within three days, the hard case for
  direct-seeded crops. Chosen on T2v3 among one forest for all crops (est_x1), the two-forest pair with 40 trees,
  the same with trees combined by "cover" instead of the median, a larger direct-seeded forest (est_x3d: 2 million
  cases, half on heavy-rain days, smaller leaves) and the pair cut to 25 or 30 trees.
- **Yield forecast:** `yield_x1`, learned from the recorded seasons of sets D, E and G.
- **Spray efficacy:** the v3 forest's first 15 of its 25 trees (to keep the page under 10 MB; on T2v3 99.25 % against
  99.28 % with all 25).
- **Soil suitability, pest risk, time to stress, harvest readiness:** the v3 forests, unchanged.
- The pack is 8.1 MB (1,069,253 nodes) and the page 9.1 MB; the forests decode in about 0.3 s in Node.
