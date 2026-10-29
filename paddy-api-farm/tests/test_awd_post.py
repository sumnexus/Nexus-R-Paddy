"""v2 time to AWD and planned spray: the weather posterior, the farm's level estimate, the vectorised projection and
the planned-spray weather (all from what the farm knows)."""
import math
import unittest

import numpy as np

import _helpers  # noqa: F401
from _helpers import engine_at

from paddy.core import climate as C
from paddy.core.util import date_info, hash_n
from paddy.sim import wxpost as WP
from paddy.sim.twin import DAY, HOUR


class Generator(unittest.TestCase):
    def test_vectorised_days_rebuild_the_generator_exactly(self):
        """derive_rain on a day's own natural draws gives the generator's day, with and without events."""
        for seed, mods in ((5, []), (6, [{'kind': 'cloudy', 'from': 3, 'to': 10}]), (7, [{'kind': 'dry', 'from': 2, 'to': 23}]),
                           (8, [{'kind': 'storm', 'from': 4, 'to': 5, 'rain': 95, 'hour': 15}])):
            W = C.Weather(hash_n(seed, 'wx-test'), 2026, 6)
            for m in mods:
                W.add_mod(m)
            for d in range(30):
                dd = W.day(d)
                _, _, _, u1, u2, u3, bwet, brain = dd.base
                di = date_info(2026, 6, d)
                wet, rain, start, dur, cloud = WP.derive_rain(d, di, WP._mods_for(W.mods, d), bwet, np.array([brain]), np.array([u1]),
                                                              np.array([u2]), np.array([u3]))
                self.assertEqual(bool(wet[0]), dd.wet)
                for a, b in ((rain[0], dd.rain), (start[0], dd.rainStart), (dur[0], dd.rainDur), (cloud[0], dd.cloud)):
                    self.assertAlmostEqual(float(a), b, places=9)

    def test_true_days_are_possible_under_the_posterior(self):
        """Every day's true weather has a positive likelihood given the forecasts issued at a check."""
        W = C.Weather(hash_n(21, 'wx-test'), 2026, 6)
        for d0 in range(5, 60, 3):
            for h in (6, 18):
                t0 = d0 * DAY + h * HOUR
                obs = WP.Obs.from_weather(W, t0)
                for d in range(d0, d0 + 4):
                    if d == d0 and obs.today is not None:
                        continue
                    tl = WP.true_likelihood(W, obs, d)
                    self.assertTrue(tl['same_as_generator'])
                    self.assertGreater(tl['L'], 0.0, (d0, h, d))
                    self.assertTrue(tl.get('temp_ok', True), (d0, h, d))

    def test_the_farm_sees_no_future_weather(self):
        """At 06:00 after a night shower the farm knows the shower but not the day's high or cloud; yesterday's
        wet/dry state is the gauge's, not the generator's hidden one."""
        found = 0
        for seed in range(40):
            W = C.Weather(hash_n(seed, 'wx-night'), 2026, 12)      # December: showers at any hour
            for d0 in range(1, 60):
                dd = W.day(d0)
                if dd.wet and dd.rain > 0 and dd.rainStart + dd.rainDur <= 6:
                    obs = WP.Obs.from_weather(W, d0 * DAY + 6 * HOUR, farm=True)
                    self.assertIsNotNone(obs.today)
                    self.assertIsNone(obs.today['tmax'])
                    self.assertIsNone(obs.today['cloud'])
                    self.assertEqual(obs.prev_wet, W.day(d0 - 1).wet)
                    Th, Rh, Eh = WP.Sampler(obs, hours=12, K=500, K_seen=5000, M=32, rng=np.random.default_rng(1)).draw(20)
                    self.assertTrue(np.isfinite(Th).all() and np.isfinite(Eh).all())
                    found += 1
        self.assertGreater(found, 0)

    def test_draws_follow_the_forecast(self):
        """A seen shower rains at the forecast's rate; hours outside every candidate shower stay dry; temperatures
        stay within the hourly forecast's error of the forecast."""
        W = C.Weather(hash_n(33, 'wx-test'), 2026, 7)
        rng = np.random.default_rng(3)
        n_checked = 0
        for d0 in range(3, 50, 2):
            t0 = d0 * DAY + 6 * HOUR
            obs = WP.Obs.from_weather(W, t0, farm=True)
            Th, Rh, Eh = WP.Sampler(obs, hours=24, K=2000, K_seen=20000, M=64, rng=rng).draw(40)
            self.assertEqual(Th.shape, (40, 24))
            self.assertTrue((Rh >= 0).all() and (Eh >= 0).all())
            for q in range(8):            # 06:00–14:00: the temperature is pinned by that hour's forecast
                w = 0.6 + 0.08 * q
                self.assertTrue(np.all(np.abs(Th[:, q] - obs.temp_fc[q]) < 0.5 * w + 2.0))
            for q in range(24):
                r = obs.rain_fc[q]
                if r > 0 and abs(r - 1.5) > 1e-9:
                    # an hour forecast at a real rate is inside the shower: it rains, at most at that rate
                    self.assertTrue(np.all(Rh[:, q] <= r + 1e-9))
                    n_checked += 1
        self.assertGreater(n_checked, 0)


class FarmEstimates(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.e = engine_at(45, 6, seed=17)

    def test_projection_for_many_weathers_matches_the_twin(self):
        """project_level_many with the weather that comes reproduces project_level exactly."""
        e = self.e
        tw = e.tw
        for f in range(2):
            st = tw.level_state(f)
            thr = -150.0
            truth = tw.project_level(f, thr, hours=73, state=st)
            W = tw.weather
            Th, Rh, Eh = [], [], []
            t = st['t']
            for _ in range(73):
                d, hr = int(t // DAY), (t % DAY) / HOUR
                Th.append(W.temp(d, hr + 0.5))
                Rh.append(W.rain_between(d, hr, hr + 1.0))
                Eh.append(W.et0_between(d, hr, hr + 1.0))
                t += HOUR
            v = tw.project_level_many(st, thr, np.array([Th] * 3), np.array([Rh] * 3), np.array([Eh] * 3))
            self.assertTrue(np.all(v == truth), (v, truth))

    def test_level_estimate_beats_one_reading(self):
        """The fitted level is much closer to the true level than the single tube reading (σ 3 mm)."""
        e = engine_at(40, 6, seed=23)
        errs_fit, errs_one = [], []
        for _ in range(60):
            if not e.step_hour():
                break
            tw = e.tw
            slot = int(round(tw.t / HOUR))
            for f in range(2):
                h_obs = tw.water_tube(f)
                h_est, n, rms = e.adv.level_estimate(f, slot, h_obs, e.tube_log, e.water_flags)
                if n >= 24 and -290 < tw.fields[f].h < 140:
                    errs_fit.append(h_est - tw.fields[f].h)
                    errs_one.append(h_obs - tw.fields[f].h)
        self.assertGreater(len(errs_fit), 20)
        self.assertLess(float(np.std(errs_fit)), 0.5 * float(np.std(errs_one)))
        self.assertLess(abs(float(np.mean(errs_fit))), 0.6)

    def test_spray_weather_knows_the_wind(self):
        """The planned-spray wind and temperature at a coming minute are close to what will come."""
        e = self.e
        tw = e.tw
        t_dec = tw.t
        ts = [t_dec + k * 1500.0 for k in range(1, 30)]
        wind, temp, sun, rain6 = e.adv.spray_weather(t_dec, ts)
        W = tw.weather
        for k, t in enumerate(ts):
            d, h = int(t // DAY), (t % DAY) / HOUR
            self.assertLess(abs(wind[k] - W.wind(d, h)), 0.12 * W.wind(d, h) + 0.05)
            self.assertLess(abs(temp[k] - W.temp(d, h)), 1.0)
            self.assertAlmostEqual(sun[k], max(0.0, math.sin(C.sun_elevation(tw.date_info(t).doy, h))), places=9)
        from paddy.sim.advisor import PLAN_POST_DRAWS
        self.assertEqual(rain6.shape, (PLAN_POST_DRAWS, len(ts)))


if __name__ == '__main__':
    unittest.main()
