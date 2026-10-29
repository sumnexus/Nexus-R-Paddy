import unittest

import numpy as np

import _helpers  # noqa: F401
from paddy import config as CFG
from paddy.engine import Engine
from paddy.sim.twin import Twin, establish_adjust


class TwinTests(unittest.TestCase):
    def test_water_balance_closes(self):
        """Over a whole built-in season: inputs − outputs = change in stored water, per field."""
        e = Engine(seed=21, manager='builtin', keep_states=False)
        h0 = [fd.h for fd in e.tw.fields]
        e.run()
        for f, fd in enumerate(e.tw.fields):
            store = lambda h: h if h >= 0 else h * CFG.SPECIFIC_YIELD     # water above or in the soil, mm
            d_store = store(fd.h) - store(h0[f])
            drained_mm = fd.drainedM3 * 1000 / (len(e.tw.cells[f]) * CFG.SQUARE_M2)
            balance = fd.rainMm + fd.irrMm - fd.etMm - fd.percMm - fd.overflowMm - drained_mm
            self.assertAlmostEqual(balance, d_store, delta=2.0, msg=f'field {fd.name}')

    def test_methane_regimes(self):
        a = Engine(seed=22, manager='conventional', keep_states=False).run()
        b = Engine(seed=22, manager='builtin', keep_states=False).run()
        self.assertEqual(a.tw.methane(0).regime, 'continuous')
        self.assertIn(b.tw.methane(0).regime, ('single', 'multiple'))
        self.assertLess(b.summary()['ch4_kg_ha'], a.summary()['ch4_kg_ha'])

    def test_urea_loss_grows_with_depth(self):
        tw = Twin({'seed': 1})
        tw.fields[0].h = 0.0
        shallow = tw.act_fertilise(0, None, 'urea', 100).lostNKgHa
        tw.fields[1].h = 60.0
        deep = tw.act_fertilise(1, None, 'urea', 100).lostNKgHa
        self.assertGreater(deep, shallow)

    def test_leach_and_bund(self):
        tw = Twin({'seed': 1})
        tw.fields[0].h = 140.0
        tw.act_amend(0, None, 'leach', 80)
        self.assertLessEqual(tw.fields[0].h, CFG.BUND_MM)
        self.assertGreater(tw.fields[0].overflowMm, 0)
        self.assertAlmostEqual(Twin.level_after_adding(-100, 30), 0.0)
        self.assertAlmostEqual(Twin.level_after_adding(-100, 10), -100 + 10 / CFG.SPECIFIC_YIELD)

    def test_establishment_factors(self):
        self.assertEqual(establish_adjust(30, 22), 1.0)
        self.assertLess(establish_adjust(120, 22), 1.0)
        self.assertLess(establish_adjust(-150, 22), 1.0)
        self.assertLess(establish_adjust(30, 40), 1.0)

    def test_soil_and_heights(self):
        tw = Twin({'seed': 2})
        for f in range(2):
            self.assertAlmostEqual(float(np.mean(tw.elev[tw.cells[f]])), 0.0, places=6)
        v = tw.valid
        self.assertTrue(np.all((tw.ph[v] >= 4.4) & (tw.ph[v] <= 8.6)))
        self.assertEqual(len(tw.cells[0]), CFG.ROWS * CFG.COLS)

    def test_seedling_age(self):
        tw = Twin({'seed': 1})
        self.assertAlmostEqual(tw.seedling_age(), CFG.NURSERY_AGE_AT_START)
        for _ in range(48):
            tw.step_hour()
        self.assertAlmostEqual(tw.seedling_age(), CFG.NURSERY_AGE_AT_START + 2)


if __name__ == '__main__':
    unittest.main()
