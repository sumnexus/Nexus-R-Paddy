import copy
import unittest
from unittest import mock

import _helpers
from _helpers import FakeEngine, season_snapshots
from paddy.decisions import guardrails as G
from paddy.decisions import schema as S
from paddy.sim.twin import DAY, HOUR, PEST_IDX


def one(tw, d):
    ds, errs = S.validate({'decisions': [d]})
    assert not errs, errs
    return G.check(FakeEngine(tw), ds[0]), ds[0]


class GuardrailTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.snap = season_snapshots()

    def tw(self, name):
        return copy.deepcopy(self.snap[name])

    def test_fertiliser_caps(self):
        tw = self.tw('young')
        v, _ = one(tw, {'field': 'A', 'action': 'fertilise', 'params': {'product': 'urea', 'kg_per_ha': 131}, 'reason': 'r'})
        self.assertEqual(v.status, 'rejected')
        v, _ = one(tw, {'field': 'A', 'action': 'fertilise', 'params': {'product': 'urea', 'kg_per_ha': 60}, 'reason': 'r'})
        self.assertTrue(v.ok)
        tw.nApplied[tw.cells[0]] = 140.0
        v, _ = one(tw, {'field': 'A', 'action': 'fertilise', 'params': {'product': 'urea', 'kg_per_ha': 60}, 'reason': 'r'})
        self.assertEqual(v.status, 'rejected')
        self.assertIn('seasonal cap', v.messages[0])

    def test_spray_label_weather_and_window(self):
        tw = self.tw('young')
        base = {'field': 'A', 'action': 'spray', 'squares': ['A1,1'], 'reason': 'r'}
        v, _ = one(tw, {**base, 'params': {'product': 'emamectin'}})
        self.assertEqual(v.status, 'rejected')
        v, _ = one(tw, {**base, 'params': {'product': 'pymetrozine', 'target_pest': 'leaf_folder'}})
        self.assertEqual(v.status, 'rejected')
        with mock.patch.object(G, '_rain_and_wind', return_value=(3.0, 1.0)):
            v, _ = one(tw, {**base, 'params': {'product': 'bt', 'target_pest': 'leaf_folder'}})
        self.assertEqual(v.status, 'rejected')
        self.assertIn('rain', v.messages[0])
        with mock.patch.object(G, '_rain_and_wind', return_value=(0.0, 6.0)):
            v, _ = one(tw, {**base, 'params': {'product': 'bt', 'target_pest': 'leaf_folder'}})
        self.assertIn('drifts', v.messages[0])
        with mock.patch.object(G, '_rain_and_wind', return_value=(0.0, 1.0)):
            v, _ = one(tw, {**base, 'params': {'product': 'bt', 'target_pest': 'leaf_folder'}, 'when': '23:00'})
        self.assertEqual(v.status, 'rejected')
        self.assertIn('window', v.messages[0])

    def test_bee_toxic_on_flowering_crop(self):
        tw = self.tw('flowering')
        i = tw.sq_index(0, 0, 0)
        tw.oPestId[i] = PEST_IDX['brown_planthopper']
        tw.oPest[i] = 0.1
        base = {'field': 'A', 'action': 'spray', 'squares': ['A1,1'], 'params': {'product': 'imidacloprid', 'target_pest': 'brown_planthopper'}, 'reason': 'r'}
        with mock.patch.object(G, '_rain_and_wind', return_value=(0.0, 1.0)), mock.patch.object(G, '_flowering', return_value=True), \
                mock.patch.object(G, '_days_to_harvest', return_value=60):
            v, _ = one(tw, {**base, 'when': '12:00'})
            self.assertEqual(v.status, 'rejected')
            v, _ = one(tw, {**base, 'when': '18:00'})
            self.assertEqual(v.status, 'rejected')          # dusk, but not severe enough
            tw.oPest[i] = 0.3
            v, _ = one(tw, {**base, 'when': '18:00'})
            self.assertTrue(v.ok, v.messages)

    def test_phi(self):
        tw = self.tw('young')
        with mock.patch.object(G, '_rain_and_wind', return_value=(0.0, 1.0)), mock.patch.object(G, '_days_to_harvest', return_value=10):
            v, _ = one(tw, {'field': 'A', 'action': 'spray', 'squares': ['A1,1'], 'params': {'product': 'pymetrozine'}, 'reason': 'r'})
        self.assertEqual(v.status, 'rejected')
        self.assertIn('pre-harvest interval', v.messages[0])

    def test_water_rules(self):
        tw = self.tw('drain')
        v, _ = one(tw, {'field': 'A', 'action': 'irrigate', 'params': {'target_depth_mm': 50}, 'reason': 'r'})
        self.assertEqual(v.status, 'rejected')
        tw = self.tw('young')
        v, _ = one(tw, {'field': 'A', 'action': 'irrigate', 'params': {'target_depth_mm': 101}, 'reason': 'r'})
        self.assertEqual(v.status, 'rejected')
        v, _ = one(tw, {'field': 'A', 'action': 'drain', 'params': {'to_depth_mm': -40}, 'reason': 'r'})
        self.assertEqual(v.status, 'warning')
        self.assertEqual(v.decision['params']['to_depth_mm'], 0.0)

    def test_puddle_and_transplant(self):
        tw = self.tw('land')
        v, _ = one(tw, {'field': 'A', 'action': 'transplant', 'reason': 'r'})
        self.assertEqual(v.status, 'rejected')
        if tw.water_tube(0) < 20:
            v, _ = one(tw, {'field': 'A', 'action': 'puddle', 'reason': 'r'})
            self.assertEqual(v.status, 'rejected')
        tw = self.tw('young')
        v, _ = one(tw, {'field': 'A', 'action': 'puddle', 'reason': 'r'})
        self.assertEqual(v.status, 'rejected')
        v, _ = one(tw, {'field': 'A', 'action': 'transplant', 'reason': 'r'})
        dead = [i for i in tw.cells[0] if tw.has[i] and not tw.alive[i]]
        if dead:
            self.assertEqual(v.status, 'warning')          # gap-filling the squares that died
        else:
            self.assertEqual(v.status, 'rejected')

    def test_harvest(self):
        tw = self.tw('young')
        v, _ = one(tw, {'field': 'A', 'action': 'harvest', 'reason': 'r'})
        self.assertEqual(v.status, 'rejected')
        v, _ = one(tw, {'field': 'A', 'action': 'harvest', 'params': {'override_immature': True}, 'reason': 'r'})
        self.assertEqual(v.status, 'warning')
        tw = self.tw('ripe')
        tw.noHarvestBefore[tw.cells[0]] = tw.t + DAY
        v, _ = one(tw, {'field': 'A', 'action': 'harvest', 'reason': 'r'})
        self.assertEqual(v.status, 'rejected')

    def test_amend_caps_and_leach_overflow(self):
        tw = self.tw('land')
        v, _ = one(tw, {'field': 'A', 'action': 'amend', 'squares': ['A1,1'], 'params': {'product': 'lime', 'dose': 401}, 'reason': 'r'})
        self.assertEqual(v.status, 'rejected')
        tw.amendG['lime'][tw.sq_index(0, 0, 0)] = 700
        v, _ = one(tw, {'field': 'A', 'action': 'amend', 'squares': ['A1,1'], 'params': {'product': 'lime', 'dose': 200}, 'reason': 'r'})
        self.assertEqual(v.status, 'rejected')
        tw.fields[0].h = 130.0
        every = [f'A{r},{c}' for r in range(1, 7) for c in range(1, 7)]
        v, _ = one(tw, {'field': 'A', 'action': 'amend', 'squares': every, 'params': {'product': 'leach', 'dose': 40}, 'reason': 'r'})
        self.assertEqual(v.status, 'rejected')
        self.assertIn('overflow', v.messages[-1])

    def test_call_level_checks(self):
        tw = self.tw('young')
        eng = FakeEngine(tw)
        ds, _ = S.validate({'decisions': [{'field': 'A', 'action': 'irrigate', 'params': {'target_depth_mm': 50}, 'reason': 'r'},
                                          {'field': 'A', 'action': 'drain', 'params': {'to_depth_mm': 0}, 'reason': 'r'},
                                          {'field': 'B', 'action': 'hold', 'reason': 'r'}, {'field': 'B', 'action': 'hold', 'reason': 'r'}]
                                         + [{'field': 'B', 'action': 'scout', 'squares': [f'{k % 6 + 1},1'], 'reason': 'r'} for k in range(12)]})
        vs = G.check_call(eng, ds)
        self.assertTrue(vs[0].ok)
        self.assertEqual(vs[1].status, 'rejected')      # contradicts the irrigation
        self.assertEqual(vs[3].status, 'rejected')      # duplicate
        self.assertTrue(all(v.status == 'rejected' for v in vs[12:]))   # more than 12 decisions

    def test_parse_when(self):
        tw = self.tw('young')
        t0 = tw.t
        t, err = G.parse_when(tw, 'now')
        self.assertEqual(t, t0)
        t, err = G.parse_when(tw, '05:00')
        self.assertIsNone(err)
        self.assertTrue(0 < t - t0 <= DAY)
        t, err = G.parse_when(tw, tw.iso(t0 - 3 * HOUR))
        self.assertIsNotNone(err)
        t, err = G.parse_when(tw, tw.iso(t0 + 60 * HOUR))
        self.assertIsNotNone(err)
        t, err = G.parse_when(tw, 'tomorrow')
        self.assertIsNotNone(err)


if __name__ == '__main__':
    unittest.main()
