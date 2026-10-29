"""The model round's tools: scoring as the audit does, the paddy forest pack round trip, the suite a farm runs."""
import os
import tempfile
import unittest

import numpy as np

import _helpers  # noqa: F401

from paddy import modelround as MR
from paddy.engine import default_models, model_suite


class Scoring(unittest.TestCase):
    def test_hits_follow_the_audit_definitions(self):
        y = np.array([10.0, 50.0, 80.0])
        h, w, sc = MR.hits('awd', np.array([15.9, 59.9, 0.0]), y)
        self.assertEqual(list(h), [True, True, False])        # ±6 h; ±20 % of 50 = 10 h
        self.assertEqual(list(sc), [True, True, False])       # truths of 72 h or more are not scored
        h, _, _ = MR.hits('plan', np.array([0.5, 0.5]), np.array([0.549, 0.551]))
        self.assertEqual(list(h), [True, False])
        h, _, _ = MR.hits('harvest', np.array([0.49, 0.51]), np.array([0.0, 1.0]))
        self.assertEqual(list(h), [True, True])

    def test_breakdown_and_wilson(self):
        rng = np.random.default_rng(1)
        n = 400
        meta = np.array([(i // 20, ['builtin', 'conventional', 'api-no-advice', 'builtin-cf'][(i // 20) % 4], (i // 20) % 2) for i in range(n)], dtype=object)
        y = rng.uniform(0, 1, n)
        pred = y + np.where(rng.uniform(0, 1, n) < 0.9, 0.0, 0.2)
        b = MR.breakdown('spray', pred, y, None, meta, bootstrap=True)
        self.assertAlmostEqual(b['pooled']['rate'], float(np.mean(np.abs(pred - y) <= 0.05)), places=12)
        lo, hi = b['pooled']['wilson']
        self.assertTrue(lo < b['pooled']['rate'] < hi)
        self.assertTrue(b['pooled']['boot'][0] <= b['pooled']['rate'] <= b['pooled']['boot'][1])
        self.assertEqual(sum(b[m]['n'] for m in ('builtin', 'conventional', 'api-no-advice', 'builtin-cf')), n)


class Pack(unittest.TestCase):
    def test_round_trip_and_suite(self):
        rng = np.random.default_rng(2)
        X = rng.normal(size=(600, 5))
        y = 10 + 3 * X[:, 0] - 2 * X[:, 1]
        rf = MR.train('reg', X, y, nTrees=6, maxDepth=6, minLeaf=3, leafStat='cover', combine='cover', tol=MR.TOLS['awd'])
        d = tempfile.mkdtemp(prefix='pack-')
        npz, meta = MR.save_pack({'awd': (rf, {'tol': 'awd'})}, os.path.join(d, 'p.npz'), os.path.join(d, 'p.json'))
        forests, info = MR.load_pack(npz, meta)
        self.assertIn('awd', forests)
        p0, p1 = rf.predict_batch(X[:50]), forests['awd'].predict_batch(X[:50])
        self.assertTrue(np.allclose(p0, p1, atol=1e-4))       # thresholds and leaves are kept as float32
        suite = model_suite(npz)
        self.assertIn('awd', suite.m)
        self.assertIn('soil', suite.m)                        # the v1 forests are still there
        self.assertNotIn('awd', model_suite('v1').m)          # and the v1 suite is untouched by the pack
        # no models named: the installed paddy pack when there is one, else v1's forests
        self.assertEqual(set(model_suite(None).m), set(model_suite(default_models()).m))


if __name__ == '__main__':
    unittest.main()
