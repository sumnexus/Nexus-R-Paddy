"""The built-in and conventional managers' newer rules, the label-based pesticide accounting and the state's
days to maturity."""
import copy
import unittest

from _helpers import FakeEngine, engine_at, season_snapshots
from paddy.decisions import guardrails as G
from paddy.decisions import managers as M
from paddy.decisions import rules as R
from paddy.decisions.state import build_state


def _growing_field(**over):
    F = {'phase': 'growing', 'progress': 0.3, 'days_after_transplanting': 8.0,
         'water': {'tube_mm': 30, 'pumping': False, 'draining': False},
         'inputs': {'splits': [['basal', 50, ''], ['tillering', 25, ''], ['panicle_initiation', 25, '']], 'sprays': 0},
         'soil': {'avg': {'n': 260}}, 'pests': {}, 'advice': {}, 'plants': {'planted': 36, 'alive': 34, 'dead': 2}}
    F.update(over)
    return F


def _state(F, name='A', season='kharif'):
    return {'now': {'time': '2026-07-01T06:00', 'season': season}, 'weather': {'forecast': [{'rain_mm': 0, 'rain_prob': 0, 'tmax': 30}] * 3,
                                                                                'rain_last_24h_mm': 0}, 'fields': {name: F}}


class GapFill(unittest.TestCase):
    def test_inside_the_window(self):
        F = _growing_field(plants={'planted': 36, 'alive': 34, 'dead': 2, 'dead_squares': ['A1,1', 'A2,2']}, days_after_transplanting=7.5)
        out = M.gap_fill('A', F)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]['action'], 'transplant')
        self.assertEqual(out[0]['squares'], ['A1,1', 'A2,2'])

    def test_outside_the_window_or_nothing_dead(self):
        for dat in (5.0, 11.0):
            F = _growing_field(plants={'planted': 36, 'alive': 34, 'dead': 2, 'dead_squares': ['A1,1']}, days_after_transplanting=dat)
            self.assertEqual(M.gap_fill('A', F), [])
        self.assertEqual(M.gap_fill('A', _growing_field()), [])

    def test_guardrail_accepts_a_gap_fill_with_a_note(self):
        snaps = season_snapshots()
        tw = copy.deepcopy(snaps['young'])
        cs = tw.cells[0]
        i = int(cs[0])
        tw.alive[i] = False               # a seedling that died
        d = {'field': 'A', 'action': 'transplant', 'params': {}, 'squares': [(1, 1)], 'when': 'now', 'reason': 'gap filling'}
        v = G.check(FakeEngine(tw), d)
        self.assertTrue(v.ok, v.messages)
        self.assertEqual(v.status, 'warning')


class LateRewet(unittest.TestCase):
    def test_rewets_to_the_surface_only_near_the_drain_down(self):
        mgr = M.BuiltinManager()
        F = _growing_field(progress=0.87, days_after_transplanting=105, water={'tube_mm': -145, 'pumping': False, 'draining': False})
        out = mgr._growing(_state(F), 'A', F, [])
        irr = [d for d in out if d['action'] == 'irrigate']
        self.assertEqual(len(irr), 1)
        self.assertEqual(irr[0]['params']['target_depth_mm'], 0)

    def test_full_reflood_earlier(self):
        mgr = M.BuiltinManager()
        F = _growing_field(progress=0.8, days_after_transplanting=95, water={'tube_mm': -145, 'pumping': False, 'draining': False})
        out = mgr._growing(_state(F), 'A', F, [])
        irr = [d for d in out if d['action'] == 'irrigate']
        self.assertEqual(irr[0]['params']['target_depth_mm'], 50)


class PesticideAccounting(unittest.TestCase):
    def test_label_doses(self):
        snaps = season_snapshots()
        tw = copy.deepcopy(snaps['young'])
        fd = tw.fields[0]
        ai0, bio0 = fd.aiG, fd.bioKg
        tw.act_spray(0, [(1, 1), (1, 2)], 'chlorantraniliprole', 'yellow_stem_borer')
        self.assertAlmostEqual(fd.aiG - ai0, 2 * 30 / 10000)          # 30 g a.i./ha on 2 m²
        tw.act_spray(0, [(2, 1)], 'bt', 'leaf_folder')
        self.assertAlmostEqual(fd.aiG - ai0, 2 * 30 / 10000)          # a microbial adds no grams of a.i. …
        self.assertAlmostEqual(fd.bioKg - bio0, 1.5 / 10000)          # … but 1.5 kg/ha of product
        self.assertAlmostEqual(R.ai_g_per_m2('pymetrozine'), 150 / 10000)


class StateDaysToMaturity(unittest.TestCase):
    def test_matches_the_guardrail(self):
        e = engine_at(60)
        st = build_state(e, ['scheduled'], advice=None)
        for f, name in enumerate(('A', 'B')):
            F = st['fields'][name]
            self.assertIn('days_to_maturity', F)
            self.assertAlmostEqual(F['days_to_maturity'], G._days_to_harvest(e.tw, f), delta=0.06)


if __name__ == '__main__':
    unittest.main()


class AwdTiming(unittest.TestCase):
    """v2: in the margin above the AWD line where v1 re-floods at once, the built-in manager lets the field keep drying
    when the time-to-AWD forest expects the line only after the next decision point; at or past the line, without the
    forest, or with the line due before the next decision point, it re-floods at once as v1 does."""

    def _field(self, tube, hrs, scheduled=None, src='forest', progress=0.3):
        water = {'tube_mm': tube, 'pumping': False, 'draining': False}
        if scheduled:
            water['scheduled'] = scheduled
        return _growing_field(progress=progress, days_after_transplanting=30, water=water,
                              advice={'water': {'hours_to_awd_threshold': hrs, 'threshold_mm': -150, 'from': src}})

    def _irr(self, F, notes=None):
        mgr = M.BuiltinManager()
        return [d for d in mgr._growing(_state(F), 'A', F, notes if notes is not None else []) if d['action'] == 'irrigate']

    def test_keeps_drying_when_the_line_comes_after_the_next_decision_point(self):
        notes = []
        self.assertEqual(self._irr(self._field(-145, 20.0), notes), [])
        self.assertTrue(any('keep drying' in n for n in notes))

    def test_refloods_now_when_the_line_comes_first(self):
        irr = self._irr(self._field(-145, 6.0))
        self.assertEqual(len(irr), 1)
        self.assertEqual(irr[0].get('when', 'now'), 'now')
        self.assertEqual(irr[0]['params']['target_depth_mm'], 50)

    def test_at_or_past_the_line_always_now(self):
        for hrs in (25.0, None):
            irr = self._irr(self._field(-152, hrs))
            self.assertEqual(len(irr), 1)
            self.assertEqual(irr[0].get('when', 'now'), 'now')

    def test_without_the_forest_v1s_rule(self):
        for F in (self._field(-142, 30.0, src='water-balance estimate'), self._field(-142, None)):
            self.assertEqual(len(self._irr(F)), 1)
        self.assertEqual(len(self._irr(self._field(-142, 30.0), )), 0)   # the forest is what lets it wait

    def test_nothing_above_the_margin_or_when_already_scheduled(self):
        self.assertEqual(self._irr(self._field(-120, 2.0)), [])          # the tube's trigger at the line re-floods it
        self.assertEqual(self._irr(self._field(-145, 5.0, scheduled={'action': 'irrigate', 'at': '2026-07-01T13:00', 'target_mm': 50})), [])

    def test_near_the_drain_down_only_up_to_the_surface(self):
        irr = self._irr(self._field(-145, 5.0, progress=0.87))
        self.assertEqual(len(irr), 1)
        self.assertEqual(irr[0]['params']['target_depth_mm'], 0)
        self.assertEqual(self._irr(self._field(-145, 20.0, progress=0.87)), [])

    def test_the_state_shows_a_scheduled_irrigation(self):
        e = engine_at(30, 6, seed=11)
        d = {'field': 'A', 'action': 'irrigate', 'params': {'target_depth_mm': 50}, 'when': e.tw.iso(e.tw.t + 5 * 3600), 'reason': 'test'}
        v = G.check(e, d)
        self.assertTrue(v.ok, v.messages)
        rec = e._new_decision(d, v, 'c-test', 'test')
        self.assertEqual(rec['status'], 'scheduled')
        st = build_state(e, ['scheduled'])
        self.assertEqual(st['fields']['A']['water']['scheduled']['action'], 'irrigate')
        self.assertEqual(st['fields']['A']['water']['scheduled']['at'], e.tw.iso(e.tw.t + 5 * 3600))
        self.assertNotIn('scheduled', st['fields']['B']['water'])
