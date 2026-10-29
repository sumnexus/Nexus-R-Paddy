import json
import unittest

import _helpers  # noqa: F401
from paddy.decisions import schema as S


def reply(*ds, **kw):
    return {'decisions': list(ds), **kw}


class SchemaTests(unittest.TestCase):
    def test_valid_reply(self):
        ds, errs = S.validate(reply({'field': 'A', 'action': 'irrigate', 'params': {'target_depth_mm': 50}, 'reason': 'refill'}))
        self.assertEqual(errs, [])
        self.assertEqual(ds[0]['field'], 'A')
        self.assertEqual(ds[0]['when'], 'now')

    def test_field_case_and_squares(self):
        ds, errs = S.validate(reply({'field': 'b', 'action': 'scout', 'squares': ['B3,4', '1,1', [2, 6], ' 5 , 5 ', 'B3,4'], 'reason': 'look'}))
        self.assertEqual(errs, [])
        self.assertEqual(ds[0]['field'], 'B')
        self.assertEqual(ds[0]['squares'], [(3, 4), (1, 1), (2, 6), (5, 5)])   # duplicate removed

    def test_bad_squares(self):
        for bad in (['A3,4'], ['7,1'], ['0,2'], ['x'], [[1.5, 2]], [[True, 1]], []):
            ds, errs = S.validate(reply({'field': 'B', 'action': 'scout', 'squares': bad, 'reason': 'r'}))
            self.assertEqual(ds, [], bad)
            self.assertTrue(errs, bad)

    def test_square_required(self):
        ds, errs = S.validate(reply({'field': 'A', 'action': 'spray', 'params': {'product': 'bt'}, 'reason': 'r'}))
        self.assertEqual(ds, [])
        self.assertIn('needs a list of squares', errs[0])

    def test_unknown_action_and_keys(self):
        ds, errs = S.validate(reply({'field': 'A', 'action': 'plough', 'reason': 'r'},
                                    {'field': 'A', 'action': 'hold', 'reason': 'r', 'shell': 'rm -rf /'},
                                    {'field': 'A', 'action': 'irrigate', 'params': {'target_depth_mm': 50, 'pump': 9}, 'reason': 'r'}))
        self.assertEqual(ds, [])
        self.assertEqual(len(errs), 3)

    def test_non_finite_numbers_rejected(self):
        raw = '{"decisions":[{"field":"A","action":"fertilise","params":{"product":"urea","kg_per_ha":NaN},"reason":"r"}]}'
        ds, errs = S.validate(json.loads(raw))
        self.assertEqual(ds, [])
        raw = '{"decisions":[{"field":"A","action":"irrigate","params":{"target_depth_mm":Infinity},"reason":"r"}]}'
        ds, errs = S.validate(json.loads(raw))
        self.assertEqual(ds, [])

    def test_missing_reason_and_field(self):
        ds, errs = S.validate(reply({'field': 'A', 'action': 'hold'}, {'action': 'drain', 'params': {'to_depth_mm': 0}, 'reason': 'r'}))
        self.assertEqual(ds, [])
        self.assertEqual(len(errs), 2)

    def test_wait_without_field(self):
        ds, errs = S.validate(reply({'field': None, 'action': 'wait', 'params': {'hours': 3}, 'reason': 'rain'}))
        self.assertEqual(errs, [])
        self.assertEqual(ds[0]['params']['hours'], 3)

    def test_garbage(self):
        for g in (None, 5, 'text', [], {'decisions': 'x'}, {'notes': 'hi'}):
            ds, errs = S.validate(g)
            self.assertEqual(ds, [])
            self.assertTrue(errs)

    def test_partial_validity(self):
        ds, errs = S.validate(reply({'field': 'A', 'action': 'hold', 'reason': 'ok'}, {'field': 'A', 'action': 'fly', 'reason': 'no'}))
        self.assertEqual(len(ds), 1)
        self.assertEqual(len(errs), 1)

    def test_extract_json(self):
        self.assertEqual(S.extract_json('```json\n{"decisions": []}\n```')['decisions'], [])
        self.assertEqual(S.extract_json('Sure! {"decisions": [], "notes": "x"} done')['notes'], 'x')
        with self.assertRaises(ValueError):
            S.extract_json('no json here')

    def test_published_schema_matches(self):
        sch = S.response_schema()
        self.assertEqual(sch['properties']['decisions']['items']['properties']['action']['enum'], S.ACTIONS)
        self.assertEqual(len(sch['properties']['decisions']['items']['allOf']), len(S.ACTIONS))


if __name__ == '__main__':
    unittest.main()
