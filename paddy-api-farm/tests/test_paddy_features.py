"""v2 paddy forests: the extra inputs the farm computes itself (the readiness rule, the soil rules and their probe-noise
re-draws), the anchored forests' arithmetic, and the light path when no time-to-AWD forest is loaded."""
import unittest

import numpy as np

import _helpers  # noqa: F401
from _helpers import engine_at

from paddy.core import paddy_features as PF
from paddy.core.rf import RandomForest
from paddy.sim.advisor import anchored_predict


class ExtraInputs(unittest.TestCase):
    def test_soil_rules_match_agro(self):
        """The vectorised suitability rules give agro.suitability_label's answer on random soils."""
        self.assertTrue(PF.check_soil_rules(n=4000, seed=5))

    def test_soil_redraws_are_probabilities_of_the_reading(self):
        rows = np.array([[6.5, 250, 30, 250, 1.0, 40, 30, 0.5], [4.0, 10, 2, 10, 0.2, 85, 10, 6.0]])
        ex = PF.soil_extra(rows)
        self.assertEqual(ex.shape, (2, 4))
        self.assertTrue(np.allclose(ex[:, 1:].sum(axis=1), 1.0))
        self.assertTrue(np.array_equal(ex, PF.soil_extra(rows)))     # a pure function of the reading (fixed re-draws)
        self.assertEqual(ex[1, 0], 0)                                 # an acid, saline, sandy reading: unsuitable
        self.assertGreater(ex[1, 1], 0.99)

    def test_harvest_rule_is_the_audits_truth(self):
        """On every plant the audit assessed in a season, the rule input equals the readiness truth it scored."""
        from paddy.engine import Engine
        e = Engine(seed=8100481, weatherYear=10482, season='rabi', manager='builtin', keep_states=False, rows=True, models='v1')
        e.run()
        rows = e.audit.rows.get('harvest') or []
        self.assertGreater(len(rows), 100)
        X = np.array([r[0] for r in rows], float)
        y = np.array([r[1] for r in rows], float)
        self.assertTrue(np.array_equal(PF.harvest_rule(X)[:, 0], (y >= 0.5).astype(float)))


class Anchored(unittest.TestCase):
    def _const_forest(self, value, F=3):
        rf = RandomForest(type='reg', nTrees=1, maxDepth=1, minLeaf=1, seed=1)
        X = np.zeros((4, F))
        rf.fit(X, np.full(4, value))
        return rf

    def test_ratio_and_difference(self):
        X = np.array([[0.4, 10.0, 0], [50.0, 0.0, 0]])
        rf = self._const_forest(1.5)
        rf.anchor = {'feature': 0, 'kind': 'ratio', 'min': 1.0}
        self.assertTrue(np.allclose(anchored_predict(rf, X), [1.5, 75.0]))   # the anchor is floored at 1 h
        rf2 = self._const_forest(-0.1)
        rf2.anchor = {'feature': 1, 'kind': 'diff'}
        self.assertTrue(np.allclose(anchored_predict(rf2, X), [9.9, 0.0]))   # never below zero
        rf3 = self._const_forest(7.0)
        self.assertTrue(np.allclose(anchored_predict(rf3, X), [7.0, 7.0]))   # no anchor: the forest itself


class LightPath(unittest.TestCase):
    def test_estimate_is_the_same_without_the_posterior(self):
        """With no time-to-AWD forest and no rows, only the farm's estimate is computed: the same number."""
        e = engine_at(45, 6, seed=11, models='v1')
        adv = e.adv
        n = 0
        for f in range(2):
            thr = e._awd_threshold(f)
            if thr is None or e.tw.fields[f].h <= thr:
                continue
            x_full, est_full = adv.awd_context(f, thr, e.tube_log, e.water_flags, full=True)
            x_light, est_light = adv.awd_context(f, thr, e.tube_log, e.water_flags, full=False)
            self.assertIsNone(x_light)
            self.assertEqual(est_full, est_light)
            self.assertEqual(len(x_full), len(adv.AWD_NAMES))
            n += 1
        self.assertGreater(n, 0)


if __name__ == '__main__':
    unittest.main()
