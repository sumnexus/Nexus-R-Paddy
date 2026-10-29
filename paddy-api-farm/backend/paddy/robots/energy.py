# Copied from the Agri Robotics Playground's Python port (agriplay/energy.py) for the Paddy API Farm v2.
# Changes: imports come from paddy.core.util. Everything else is unchanged; how the paddy farm sizes and steps it is in
# paddy/robots/system.py and the README.
"""Power system: solar PV array beside the farm, lithium battery, biogas generator fed by crop
residue, grid as last resort. Every watt the arms, pump, sensors and AI computer draw is
accounted for.
"""
from __future__ import annotations

from ..core.util import clamp, Ring, js_round

CFG = dict(
    pvKwp=2.4,  # PV array size (kW peak): 8 × 300 W panels
    derate=0.80,  # wiring, inverter, soiling
    battKwh=10,  # usable battery capacity (LiFePO₄)
    socMin=0.12, socStartGen=0.22, socStopGen=0.45,
    effCharge=0.95, effDischarge=0.95, battMaxKw=6,
    genKw=3.0,  # biogas generator
    m3PerKwh=0.65,  # biogas use
    m3PerKgResidue=0.06,  # fresh residue → biogas
    pumpKwhPerM3=0.42,  # irrigation pump (lift + pressure)
    # frame sensing: soil probes + electronics always on; the scan bar's motor, cameras and NIR lamps scale
    # with how often it sweeps (duty 1 = every 20 min)
    sensing=dict(baseW=40, scanW=120),
    compute=dict(idleW=55, busyW=40),  # edge AI computer; extra while planning/driving arms
    commsW=15,
    nightLights=45,  # W at night (frame LEDs, IR for the cameras)
)


class EnergySystem:
    def __init__(self, cfg=None):
        self.cfg = dict(CFG)
        self.cfg.update(cfg or {})
        self.soc = 0.7
        self.biogasM3 = 4.0
        self.genOn = False
        self.pvKw = self.loadKw = self.netKw = 0.0
        self.totals = dict(pvKwh=0.0, loadKwh=0.0, genKwh=0.0, gridKwh=0.0, curtailedKwh=0.0, battInKwh=0.0, battOutKwh=0.0)
        self.byCat = {}  # kWh by category
        self.today = dict(pvKwh=0.0, loadKwh=0.0, genKwh=0.0, gridKwh=0.0)
        self.daily = []
        self.series = dict(pv=Ring(192), load=Ring(192), soc=Ring(192), t=Ring(192))  # 48 h at 15 min
        self._acc = {}
        self._series_acc = dict(pv=0.0, load=0.0, n=0.0)
        self._last_series_t = -1e9
        self.events = []

    def add_load(self, cat, watts, dt):
        """Arms and pumps report energy as it is used (W × s)."""
        self._acc[cat] = self._acc.get(cat, 0.0) + watts * dt

    def add_pump_water(self, litres):
        kwh = litres / 1000 * self.cfg['pumpKwhPerM3']
        self._acc['pump'] = self._acc.get('pump', 0.0) + kwh * 3.6e6

    def add_residue(self, kg):
        self.biogasM3 += kg * self.cfg['m3PerKgResidue']

    def step(self, dt, ghi, air_t, is_night, t, opts=None):
        """Advance dt seconds with irradiance ghi (W/m²) and air temperature (°C)."""
        opts = opts or {}
        c = self.cfg
        h = dt / 3600
        cell_t = air_t + 25 * ghi / 1000  # PV with temperature derating (cell ≈ air + 25 °C at 1 kW/m²)
        self.pvKw = c['pvKwp'] * (ghi / 1000) * c['derate'] * (1 - 0.004 * max(0.0, cell_t - 25))
        duty = opts.get('scanDuty', 1)
        sens_w = 0 if c.get('sensingOn') is False else c['sensing']['baseW'] + c['sensing']['scanW'] * duty + (c['nightLights'] if is_night else 0)
        comp_w = c['compute']['idleW'] + (c['compute']['busyW'] if opts.get('busy') else 0) + c['commsW']
        trap_w = c.get('trapsW') or 0  # optional pheromone-trap network: 4 traps with camera and radio, 3 W each
        base_w = sens_w + comp_w + trap_w
        acc = self._acc
        acc['sensing'] = acc.get('sensing', 0.0) + sens_w * dt
        acc['compute'] = acc.get('compute', 0.0) + comp_w * dt
        if trap_w:
            acc['traps'] = acc.get('traps', 0.0) + trap_w * dt
        load_j = 0.0
        for k, v in acc.items():
            load_j += v
            self.byCat[k] = self.byCat.get(k, 0.0) + v / 3.6e6
        self._acc = {}
        load_kwh = load_j / 3.6e6
        self.loadKw = load_kwh / h if h > 0 else base_w / 1000
        pv_kwh = self.pvKw * h
        net = pv_kwh - load_kwh  # + surplus, − deficit
        gen_kwh = grid_kwh = curtailed = 0.0
        # generator hysteresis
        if not self.genOn and c.get('biogas') is not False and self.soc < c['socStartGen'] and self.biogasM3 > 0.3:
            self.genOn = True
            self.events.append(dict(t=t, kind='gen-on'))
        if self.genOn and (self.soc > c['socStopGen'] or self.biogasM3 <= 0.05):
            self.genOn = False
            self.events.append(dict(t=t, kind='gen-off'))
        if self.genOn:
            gen_kwh = min(c['genKw'] * h, self.biogasM3 / c['m3PerKwh'])
            self.biogasM3 -= gen_kwh * c['m3PerKwh']
            net += gen_kwh
        cap = c['battKwh']
        max_e = c['battMaxKw'] * h
        if net >= 0:
            room = (1 - self.soc) * cap
            into = min(net * c['effCharge'], room, max_e)
            self.soc += into / cap
            curtailed = net - into / c['effCharge']
            self.totals['battInKwh'] += into
        else:
            need = -net
            avail = max(0.0, (self.soc - c['socMin']) * cap) * c['effDischarge']
            out = min(need, avail, max_e)
            self.soc -= out / c['effDischarge'] / cap
            self.totals['battOutKwh'] += out
            grid_kwh = need - out
        self.soc = clamp(self.soc, 0, 1)
        self.netKw = (pv_kwh + gen_kwh - load_kwh) / h if h > 0 else 0.0
        T = self.totals
        T['pvKwh'] += pv_kwh
        T['loadKwh'] += load_kwh
        T['genKwh'] += gen_kwh
        T['gridKwh'] += grid_kwh
        T['curtailedKwh'] += max(0.0, curtailed)
        D = self.today
        D['pvKwh'] += pv_kwh
        D['loadKwh'] += load_kwh
        D['genKwh'] += gen_kwh
        D['gridKwh'] += grid_kwh
        # 15-minute series for the 48 h chart
        sa = self._series_acc
        sa['pv'] += self.pvKw * dt
        sa['load'] += self.loadKw * dt
        sa['n'] += dt
        if t - self._last_series_t >= 900:
            n = max(1.0, sa['n'])
            self.series['pv'].push(sa['pv'] / n)
            self.series['load'].push(sa['load'] / n)
            self.series['soc'].push(self.soc * 100)
            self.series['t'].push(t)
            self._series_acc = dict(pv=0.0, load=0.0, n=0.0)
            self._last_series_t = t

    def close_day(self, day):
        self.daily.append(dict(day=day, **self.today))
        if len(self.daily) > 120:
            self.daily.pop(0)
        self.today = dict(pvKwh=0.0, loadKwh=0.0, genKwh=0.0, gridKwh=0.0)

    def self_sufficiency(self):
        T = self.totals
        return 1 - T['gridKwh'] / T['loadKwh'] if T['loadKwh'] > 0 else 1.0


def size_for(area):
    """Size the plant for a field of ``area`` m² (the defaults are for the 24 × 16 m standard plot)."""
    k = area / 384
    s = 0.55 + 0.45 * k
    return dict(
        pvKwp=float(f"{max(1.2, js_round(CFG['pvKwp'] * s / 0.3) * 0.3):.1f}"),
        battKwh=max(6, js_round(CFG['battKwh'] * (0.6 + 0.4 * k) / 2) * 2),
        sensing=dict(baseW=js_round(CFG['sensing']['baseW'] * (0.5 + 0.5 * k)), scanW=js_round(CFG['sensing']['scanW'] * (0.6 + 0.4 * k))),
    )
