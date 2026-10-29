# v2 working log: findings and changes (folded into the README at the end)

## F1. The v1 soil check scored half the squares on stale advice (measurement bug)

- **Found:** while caching the twin's field progress for speed, checking which actions bump `Twin.version` (the
  advisor caches its advice by version and hour). `act_soil_check` did not.
- **Effect in v1:** the 06:00 call on day 0 builds the state (soil advice from the daily *scan*, cached), then carries
  out `soil_check A`, `irrigate A`, `soil_check B`, … The audit of field A's probes hit the cache and scored the
  scan-based classes; `irrigate A` bumped the version, so field B was scored on the *probe*-based classes, as the
  definition says.
- **Numbers (20 kharif seasons, seeds 40–59, weather years 540–559; not a model test set):** scan-based classes 95.4 %
  correct, probe-based 98.1 % (1,440 squares each); the v1 audit reported 96.9 %, the average of the two (field A on the
  scan, field B on the probe). v1's campaign figure, 96.7 % (76 % of seasons at ≥ 95 %), was measured the same way.
- **Fix:** `act_soil_check` and the arms' `probe_square` bump the version, so the audit always scores the forest on the
  probe reading. Decisions and the farm's course are unchanged (the built-in manager never reads soil advice in the
  same hour as a probe): the v1 demo season's decision and history digests are identical before and after; its own
  soil score goes from 87.5 % to 90.3 % (72 squares).
- **Why it doesn't make the world easier:** the probe, its noise and the truth are unchanged; the check now measures
  what the definition always said.
