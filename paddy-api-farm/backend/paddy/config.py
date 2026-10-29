"""Configuration: farm layout, physical constants, decision-loop limits and the decision API settings.

Every number here is explained in README.md ("Numbers and their sources"). Settings that come from the
environment (the decision API) are read by ``api_settings()``; nothing here ever holds the API key itself.
"""
from __future__ import annotations

import os
import re

# ── the farm ──
FIELDS = ('A', 'B')          # two bunded paddy fields, side by side with a bund between them
ROWS = 6                     # rows of 1 m² squares in each field
COLS = 6                     # columns of 1 m² squares in each field ("one 6 × 6 plot split in two": ROWS=6, COLS=3)
SQUARE_M2 = 1.0
LAT = 12.97                  # Bengaluru
SEASONS = {'kharif': 6, 'rabi': 11}   # season → start month (transplanting window opens on the 1st)
START_YEAR = 2026

# ── field water (see README: paddy water model) ──
BUND_MM = 150                # bund height: water above it overflows
MAX_TARGET_MM = 100          # deepest water the farm may ask for
SPECIFIC_YIELD = 0.30        # water released per mm of water-table fall in the puddled topsoil
PERC_HEAD_MM = 150           # percolation rises by the ponded depth over this (Darcy head through the plough pan)
PERC_STOP_MM = 100           # percolation stops once the perched water table is this far below the surface
BUND_SEEP_PER_DAY = 0.04     # lateral seepage through the bunds: this share of the ponded depth per day
N_LEACH_PER_MM = 0.00015     # share of available N carried down per mm of percolation (× 0.4 + sand share)
TUBE_MIN_MM = -300           # a field water tube reads down to 30 cm below the surface
PUMP_M3_PER_H = 3.0          # one small pump shared by both fields
DRAIN_MM_PER_H = 40          # outlet capacity when draining ponded water
PUMP_KWH_PER_M3 = 0.42       # pumping energy (same figure as the original project)
AWD_THRESHOLD_MM = -150      # "safe AWD": re-flood when the water is 15 cm below the surface
CH4_EF_KG_HA_DAY = 0.85      # IPCC 2019 Refinement, South Asia baseline (continuously flooded, no amendment)
CH4_SF_WATER = {'continuous': 1.0, 'single': 0.71, 'multiple': 0.55}
CH4_CFOA_COMPOST = 0.17      # IPCC 2019: conversion factor for compost (per dry tonne per hectare)
COMPOST_DRY_MATTER = 0.6     # share of dry matter in the compost the farm applies

# ── sensing ──
CAMERA_HOURS = (6, 8, 10, 12, 14, 16, 18)   # camera passes over both fields
SOIL_SCAN_HOUR = 6                           # daily soil scan

# ── the decision loop ──
DECISION_HOURS = (6, 18)     # fixed decision points every simulated day
TRIGGER_MIN_GAP_H = 1.0      # at most one trigger call per simulated hour
MAX_DECISIONS_PER_CALL = 12
MAX_CALLS_PER_DAY = 6        # simulated day (API_MAX_CALLS_PER_DAY overrides)
MAX_CALLS_PER_SEASON = 600
MAX_CALLS_PER_REAL_HOUR = 240
SEASON_MAX_DAYS = 230
TRANSPLANT_DEADLINE_DAY = 75  # a field not transplanted by then is abandoned for the season
NURSERY_AGE_AT_START = 18     # the nursery was sown 18 days before the season's first morning


def _env(name, default=None):
    v = os.environ.get(name)
    return default if v is None or v == '' else v


def load_dotenv(path='.env'):
    """Read KEY=VALUE lines from a .env file into the environment (existing variables win). Skipped when
    PADDY_NO_DOTENV=1 (the tests set it, so a real key in .env can never reach a test)."""
    if os.environ.get('PADDY_NO_DOTENV') == '1':
        return
    for p in (path, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), '.env')):
        if not os.path.isfile(p):
            continue
        with open(p, encoding='utf-8') as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith('#') or '=' not in line:
                    continue
                k, v = line.split('=', 1)
                k, v = k.strip(), v.strip()
                if k.startswith('export '):
                    k = k[7:].strip()
                if v[:1] in ('"', "'") and v[0] in v[1:]:
                    v = v[1:v.index(v[0], 1)]            # quoted: everything inside the quotes
                else:
                    v = re.split(r'\s+#', v, maxsplit=1)[0].strip()   # unquoted: drop a trailing "  # comment"
                if k and k not in os.environ:
                    os.environ[k] = v
        break


def api_settings():
    """The decision API's settings from the environment. The key is reported only as present or not."""
    return {
        'base_url': _env('API_BASE_URL', ''),
        'model': _env('API_MODEL', ''),
        'adapter': _env('API_ADAPTER', 'chat_completions'),
        'timeout_s': float(_env('API_TIMEOUT_S', '60')),
        'max_calls_per_day': int(_env('API_MAX_CALLS_PER_DAY', str(MAX_CALLS_PER_DAY))),
        'max_calls_per_season': int(_env('API_MAX_CALLS_PER_SEASON', str(MAX_CALLS_PER_SEASON))),
        'max_calls_per_real_hour': int(_env('API_MAX_CALLS_PER_REAL_HOUR', str(MAX_CALLS_PER_REAL_HOUR))),
        'temperature': float(_env('API_TEMPERATURE', '0.2')),
        'json_mode': _env('API_JSON_MODE', '1') not in ('0', 'false', 'no'),
        'fallback': _env('API_FALLBACK', 'hold'),     # 'hold' or 'builtin'
        'retries': int(_env('API_RETRIES', '3')),
        'key_present': bool(_env('API_KEY')),
    }
