"""Where everything is on the paddy farm, and how far each arm can reach.

Coordinates are metres on the ground plane, the same convention as the playground's field: x runs along the rows
(east), z down the columns (south), the origin is the centre of the twin's internal grid. The grid is 13 squares by 6:
field A's six columns, the 1 m bund between the fields, field B's six columns (``paddy.sim.twin``). Square centres are
at x = column + 0.5 − 6.5 and z = row + 0.5 − 3, so field A spans x −6.5…−0.5 and field B 0.5…6.5, both z −3…3.

Around the two basins runs a 0.5 m outer bund. Each field has an inlet on the north bund, fed by a pipe from the
shared pump, and an outlet on the south bund into the drain channel (inlet and outlet at opposite corners, so the
water crosses the field). West of field A is the service bay: four docks, the seedling magazine, the fertiliser and
amendment store, the spray mixing station and the harvest chute. The cube sensor frame covers the bay and both fields;
the arms' trolleys ride its top rails at 4.6 m.

The playground's layout put the four docks 4 m apart down the side of a 16 m plot. These fields are only 6 m deep, so
the frame reaches 2.3 m beyond the bunds to the north and south (z −5.8…5.8) and the docks sit 3.0–3.1 m apart. That is
more than the traffic manager's corridor width for every pair (keep-out radii + 0.25 m margin + 0.3 m), so an arm leaving
its dock does not make a parked neighbour move aside. The two arms that travel most (watering and pest) park at the ends
of the row, the planting arm and the big harvest arm in the middle.
"""
from __future__ import annotations

import math

from .arms import DEFS
from .. import config as CFG

GRID_W, GRID_H = 13, 6                 # the twin's internal grid (columns, rows)
BUND_W = 0.5                           # outer bund width (m)
FIELD_X = {0: (-6.5, -0.5), 1: (0.5, 6.5)}
FIELD_Z = (-3.0, 3.0)
OUTER = dict(x0=-6.5 - BUND_W, x1=6.5 + BUND_W, z0=-3.0 - BUND_W, z1=3.0 + BUND_W)

DOCK_X, STATION_X = -11.2, -8.4
# docks in arm order (1 planting, 2 watering, 3 pest, 4 harvest); north to south: watering, planting, harvest, pest
DOCKS = [dict(x=DOCK_X, z=-1.5), dict(x=DOCK_X, z=-4.6), dict(x=DOCK_X, z=4.6), dict(x=DOCK_X, z=1.6)]
STATIONS = {
    'store': dict(x=STATION_X, z=-3.9, name='Fertiliser and amendment store'),
    'seed': dict(x=STATION_X, z=-1.4, name='Seedling magazine'),
    'chute': dict(x=STATION_X, z=1.5, name='Harvest chute'),
    'chem': dict(x=STATION_X, z=4.0, name='Spray mixing station'),
}
# the water gates the watering arm sets: inlets on the north bund (fed by the pipe from the shared pump), outlets on
# the south bund (into the drain channel)
GATES = {
    ('inlet', 0): dict(x=-4.5, z=OUTER['z0'] + BUND_W / 2, name='Field A inlet'),
    ('outlet', 0): dict(x=-1.5, z=OUTER['z1'] - BUND_W / 2, name='Field A outlet'),
    ('inlet', 1): dict(x=1.5, z=OUTER['z0'] + BUND_W / 2, name='Field B inlet'),
    ('outlet', 1): dict(x=4.5, z=OUTER['z1'] - BUND_W / 2, name='Field B outlet'),
}
GATE_HANDLE_Y = 0.6                    # the gate's handle and float stem stand 0.6 m above the field surface
PUMP = dict(x=-7.7, z=-4.6)            # the shared pump, at the north-west corner of field A
PIPE_Z = OUTER['z0'] - 0.35            # supply pipe along the north side
DRAIN_Z = OUTER['z1'] + 0.35           # drain channel along the south side

# arm centres stay inside these bounds (the traffic manager clamps to them); the frame is a little larger
BOUNDS = dict(x0=DOCK_X - 1.0, x1=GRID_W / 2 + 0.6, z0=-5.1, z1=5.1)
FRAME = dict(x0=DOCK_X - 1.5, x1=GRID_W / 2 + 1.1, z0=-5.8, z1=5.8, top=5.0, rail=4.6)

# ── arm geometry (the 3D view draws exactly this) ──
# Upper arm and forearm lengths and the lowest shoulder height come from the playground's scene (links 0.62 and
# 0.58 m × the arm's scale). The playground lowered the shoulder to 1.18 m (1.55 m for the harvester), which cannot
# reach the soil 0.8 m to the side; here the mast extends further, to a shoulder at 0.95 m (1.40 m for the
# harvester), so every square of a stop is inside the arm's reach with the elbow bent.
LINK1, LINK2 = 0.62, 0.58
LOW_Y = {'harvester': 1.40}
LOW_Y_DEFAULT = 0.95
MAST_MIN = 0.85                        # the telescopic mast's shortest length (raised)
ELBOW_MIN_DEG, ELBOW_MAX_DEG = 25.0, 172.0   # inner elbow angle: not folded flat, not locked straight
SOIL_TOOL_Y = 0.05                     # tool tip just above the soil (under the water when the field is flooded)


def square_xz(i):
    """Internal square index → (x, z) of its centre."""
    col, row = int(i) % GRID_W, int(i) // GRID_W
    return col + 0.5 - GRID_W / 2, row + 0.5 - GRID_H / 2


def defn(arm_id):
    return DEFS[arm_id - 1]


def arm_geometry(d):
    """Link lengths, lowest shoulder height and mast range of an arm definition."""
    S = d['scale']
    low = LOW_Y.get(d['role'], LOW_Y_DEFAULT)
    rail = FRAME['rail']
    return dict(L1=LINK1 * S, L2=LINK2 * S, lowY=low, railY=rail, mastMin=MAST_MIN, mastMax=rail - 0.25 - low)


def reach_check(d, stop_x, stop_z, tx, ty, tz):
    """Can the arm, its trolley at (stop_x, stop_z) and its mast fully lowered, put its tool on (tx, ty, tz)?

    Returns a dict: ok, horizontal offset, shoulder-to-tool distance, the inner elbow angle and the shoulder's pitch
    below the horizontal (degrees), and the reasons for any failure."""
    g = arm_geometry(d)
    h = math.hypot(tx - stop_x, tz - stop_z)
    v = g['lowY'] - ty
    dist = math.hypot(h, v)
    L1, L2 = g['L1'], g['L2']
    why = []
    if h > d['reach'] + 1e-6:
        why.append(f'{h:.3f} m to the side is beyond the {d["reach"]} m reach')
    if v <= 0:
        why.append('target above the shoulder')
    dmax, dmin = L1 + L2, abs(L1 - L2)
    if dist >= dmax or dist <= dmin:
        why.append(f'{dist:.3f} m from the shoulder is outside {dmin:.2f}–{dmax:.2f} m')
        elbow = 180.0 if dist >= dmax else 0.0
        pitch = math.degrees(math.atan2(v, h))
    else:
        c = (L1 * L1 + L2 * L2 - dist * dist) / (2 * L1 * L2)
        elbow = math.degrees(math.acos(max(-1.0, min(1.0, c))))
        # the shoulder points the upper arm above the shoulder→tool line by the angle opposite the forearm
        a = math.degrees(math.acos(max(-1.0, min(1.0, (L1 * L1 + dist * dist - L2 * L2) / (2 * L1 * dist)))))
        pitch = math.degrees(math.atan2(v, h)) - a
    if not (ELBOW_MIN_DEG <= elbow <= ELBOW_MAX_DEG):
        why.append(f'elbow at {elbow:.1f}° is outside {ELBOW_MIN_DEG:.0f}–{ELBOW_MAX_DEG:.0f}°')
    mast = g['railY'] - 0.25 - g['lowY']
    if not (g['mastMin'] <= mast <= g['mastMax'] + 1e-9):
        why.append('mast out of range')
    return dict(ok=not why, h=h, dist=dist, elbow=elbow, pitch=pitch, why=why)


def layout_info():
    """The farm's geometry for the website's 3D view (everything in metres, the twin's coordinates)."""
    from .system import CODES
    return {'grid': {'W': GRID_W, 'H': GRID_H, 'bund_col': GRID_W // 2}, 'fields': {'A': {'x': FIELD_X[0], 'z': FIELD_Z}, 'B': {'x': FIELD_X[1], 'z': FIELD_Z}},
            'outer': OUTER, 'bund_w': BUND_W, 'docks': DOCKS, 'stations': STATIONS,
            'gates': {f'{k[0]}_{"AB"[k[1]]}': v for k, v in GATES.items()}, 'gate_handle_y': GATE_HANDLE_Y, 'pump': PUMP, 'pipe_z': PIPE_Z,
            'drain_z': DRAIN_Z, 'bounds': BOUNDS, 'frame': FRAME, 'link': [LINK1, LINK2], 'low_y': {'default': LOW_Y_DEFAULT, **LOW_Y},
            'mast_min': MAST_MIN, 'soil_tool_y': SOIL_TOOL_Y, 'awd_threshold_mm': CFG.AWD_THRESHOLD_MM, 'bund_mm': CFG.BUND_MM, 'arms': [{k: d[k] for k in ('id', 'name', 'title', 'role', 'color', 'radius', 'scale',
                                                                                       'reach', 'capacity', 'capKind', 'station', 'speed', 'lowerTime',
                                                                                       'raiseTime', 'reachMove')} for d in DEFS],
            'codes': CODES}
