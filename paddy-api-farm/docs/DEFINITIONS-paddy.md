# How the original definitions apply on the paddy farm

`DEFINITIONS.md` in this folder is copied unchanged from the Agri Robotics Playground (`test/acc/DEFINITIONS.md`).
The paddy farm checks the same forests with the same truths and pass marks. This note says only how each definition
maps onto two 6 × 6 paddy fields with no robot arms. The code is `backend/paddy/sim/audit.py`.

| Forest | Who is scored here | Prediction | Truth | Pass |
|---|---|---|---|---|
| Soil suitability | every square a `soil_check` probes | the class with the highest probability, from the probe reading | the agronomic rules applied to the square's true soil | right class ≥ 95 % |
| Pest risk | once a day at 08:00: every live plant in a field where the cameras see a paddy pest, for each pest seen there, if the plant's own seen severity of it is below 8 %. Plants the farm doesn't ask the forest about (no sighting on them or within two squares) count as a prediction of 0, as in the original | ≥ 0.5 = "will reach 8 %" (the forest's calibrated score) | the plant's severity reaches 8 % within 7 days if nothing is sprayed: the twin's pest dynamics run forward with the weather that actually came | accuracy ≥ 95 % and balanced accuracy ≥ 95 % |
| Yield forecast | every field-season that is harvested | the forecast made when the field is transplanted (the yield forest with this farm's management flags: soil test, smart water, spot spraying for the built-in and API managers; none of them for the conventional manager) | harvested kg ÷ attainable kg (5.5 t/ha) over every square planted, failed ones included | within ±10 points ≥ 95 % |
| Planting day (transplanted crop) | every square of a puddled, not yet transplanted field at every 06:00 and 18:00 decision point, and every square at the transplanting itself | the chance the seedling establishes if transplanted now: the transplanted-crop forest, times the farm's practice factors from the water tube, the surveyed square heights and the seedlings' age | the twin's true chance: the original formula on the true soil water and the weather that actually came over the next three days, times the same practice factors on the true water depth | within ±5 points ≥ 95 %; and per field and check, "transplant" (field mean ≥ 72 %) matches the same rule on the true chances ≥ 95 % |
| Spray efficacy | every square sprayed | the share killed, from what the farm knows when it sprays: measured wind, temperature, humidity and sun, the hourly rain forecast for 6 hours, the product, the pest as the close-up look identified it and its tracked age | the share actually killed (with the rain that actually fell) | within ±5 points ≥ 95 % |
| Harvest readiness | every plant at ≥ 96 % progress assessed at a 06:00 or 18:00 decision point | ≥ 0.5 = ready | the ready rule at the assessment | right ≥ 95 % |

Also reported, not a pass mark, as in the original: the spray prediction made when a spray is decided, for the hour it
will be applied ("spray efficacy, planned").

The **time-to-stress** forest is not used: paddy water is managed by the field's water tube and the AWD thresholds,
as in the original, where paddy was never scored by it.

**The practice factors** on the planting-day prediction and truth are new here because transplanting into a paddy
field depends on things the original formula never met: seedlings drown in deep water (above 50 mm the chance falls
0.5 % per mm, floor 30 %), dry out on a drained field (below −30 mm, 0.5 % per mm, floor 60 %) and establish less well
when old (above 30 days, 1 % per day, floor 80 %). The prediction applies them to what the farm sees (tube ±3 mm,
square heights from a ±5 mm survey); the truth applies them to the true depth. Under good practice they are all 1 and
the check is the original one.
