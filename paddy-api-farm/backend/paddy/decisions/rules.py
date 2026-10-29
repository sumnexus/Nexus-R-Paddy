"""The farm's rule book: what may be sprayed on paddy, dose caps, weather limits and the water rules.

The guardrails enforce these numbers, the prompt tells the decision API about them and the built-in manager
follows them. README.md ("Guardrails") gives the reason and the source for each number.

The pesticide list is a SIMULATED label list modelled on the Indian registration (CIB&RC "major uses") of these
products on rice. Check the current label of a real product before any real use.
"""
from __future__ import annotations

from ..core import data as D

# product → paddy pests it is labelled for, the label dose and the waiting period (pre-harvest interval) for rice,
# from the CIB&RC "major uses" lists (compiled by Junagadh Agricultural University, 2025). The twin applies the label
# dose itself: the decision maker chooses the product and the squares, never the dose.
PADDY_LABEL = {
    'chlorantraniliprole': {'pests': ['yellow_stem_borer', 'leaf_folder'], 'label_dose': '150 ml/ha of 18.5% SC (30 g a.i./ha)', 'phi_days': 47,
                            'ai_g_ha': 30.0},
    'pymetrozine': {'pests': ['brown_planthopper'], 'label_dose': '300 g/ha of 50% WG (150 g a.i./ha)', 'phi_days': 19, 'ai_g_ha': 150.0},
    'imidacloprid': {'pests': ['brown_planthopper'], 'label_dose': '100–125 ml/ha of 17.8% SL (20–25 g a.i./ha)', 'phi_days': 40, 'ai_g_ha': 25.0},
    'neem': {'pests': ['brown_planthopper', 'leaf_folder', 'yellow_stem_borer'], 'label_dose': '1.5–2.5 l/ha of azadirachtin 0.15% EC (2.25–3.75 g a.i./ha)',
             'phi_days': 5, 'ai_g_ha': 3.75},
    # a microbial: dosed as product, not counted in grams of active ingredient (reported as kg of product instead)
    'bt': {'pests': ['leaf_folder', 'yellow_stem_borer'], 'label_dose': '1.5 kg/ha of Bt kurstaki', 'phi_days': 0, 'ai_g_ha': 0.0,
           'bio_kg_ha': 1.5},
}


def ai_g_per_m2(product):
    """Grams of chemical or botanical active ingredient one 1 m² spot spray at the label dose puts out (the upper
    end of a label range). Microbials (Bt) count 0 here and are reported as kg of product (bio_kg_per_m2)."""
    lab = PADDY_LABEL.get(product)
    return lab['ai_g_ha'] / 10000.0 if lab else D.PESTICIDES[product].aiPerSite


def bio_kg_per_m2(product):
    lab = PADDY_LABEL.get(product)
    return (lab.get('bio_kg_ha') or 0.0) / 10000.0 if lab else 0.0


def phi_days(product):
    """Waiting period between the last spray and harvest, days (the rice label's; the library's for anything else)."""
    lab = PADDY_LABEL.get(product)
    return lab['phi_days'] if lab else D.PESTICIDES[product].phi


# fertiliser: largest single application (kg of product per ha) and the seasonal nitrogen limits (kg N/ha)
FERT_CAP_KG_HA = {'urea': 130, 'dap': 250, 'mop': 150, 'ssp': 400, 'complex_10_26_26': 300}
N_SEASON_WARN = 120
N_SEASON_CAP = 150
LATE_N_PROGRESS = 0.75          # nitrogen after flowering is mostly wasted

# soil amendments: largest single application (g/m², mm for leach) and the seasonal total per square (2 ×)
AMEND_CAP = {'lime': 400, 'gypsum': 500, 'sulphur': 100, 'compost': 3000, 'leach': 80}
AMEND_SEASON_FACTOR = 2.0

# spraying
SPRAY_RAIN_MM_6H = 2.0          # forecast rain in the six hours after spraying that rejects a spray
SPRAY_WIND_MS = 4.0             # forecast wind above which a spray drifts
SPRAY_HOURS = (5.5, 20.5)       # daylight working window
BEE_TOXIC = 0.5                 # products above this bee-toxicity score are "bee-toxic"
BEE_WINDOWS = ((5.5, 7.0), (17.5, 19.5))   # when bee-toxic products may be used on a flowering crop
BEE_SEVERE = 0.25               # …and only against an infestation this severe
MOA_REPEAT_DAYS = 14            # same IRAC group on the same square within this many days → warning
SPRAYS_WARN = 4                 # sprays on one square in a season → warning above this
SPRAYS_CAP = 8                  # → rejected above this

# water
DRAIN_DOWN_PROGRESS = 0.90      # from here on the field is drained for harvest: no irrigation
MAX_TARGET_MM = 100
MIN_TARGET_MM = -100
TRANSPLANT_DEEP_MM = 50         # deeper standing water drowns young seedlings
TRANSPLANT_DRY_MM = -50         # the field has dried out
GAP_FILL_DAYS = 15              # failed squares may be re-planted within this many days of transplanting
OLD_SEEDLINGS_DAYS = 30
PUDDLE_MIN_MM = 20

# harvest
MATURE_PROGRESS = 0.96          # median thermal-time progress below which a harvest is "immature"
WET_RAIN_2D_MM = 6.0

# the decision loop
WHEN_MAX_H = 48                 # a decision may be scheduled at most this far ahead
WAIT_MAX_H = 72


def labelled_for(product, pest=None):
    lab = PADDY_LABEL.get(product)
    if lab is None:
        return False
    return pest is None or pest in lab['pests']


def products_for(pest):
    """Labelled products for a paddy pest, best base efficacy first."""
    out = [p for p, lab in PADDY_LABEL.items() if pest in lab['pests']]
    return sorted(out, key=lambda p: -D.pesticide_efficacy(p, pest))


def in_window(h, win):
    return win[0] <= h <= win[1]


def bee_window(h):
    return any(in_window(h, w) for w in BEE_WINDOWS)
