"""The .env file: values, quotes, inline comments, and the rule that the real environment wins."""
import os
import tempfile
import unittest

from _helpers import BACKEND  # noqa: F401  (puts the backend on sys.path)
from paddy import config as CFG


class DotEnv(unittest.TestCase):
    def setUp(self):
        self.saved = {k: os.environ.get(k) for k in ('PADDY_T_A', 'PADDY_T_B', 'PADDY_T_C', 'PADDY_T_D', 'PADDY_T_E', 'PADDY_T_F')}
        for k in self.saved:
            os.environ.pop(k, None)

    def tearDown(self):
        for k, v in self.saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def test_parsing(self):
        os.environ['PADDY_T_F'] = 'from-the-environment'
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, '.env')
            with open(p, 'w', encoding='utf-8') as fh:
                fh.write('# a comment\n'
                         'PADDY_T_A=chat_completions        # chat_completions | anthropic_messages\n'
                         'PADDY_T_B="value with # hash"  # comment\n'
                         "export PADDY_T_C='quoted'\n"
                         'PADDY_T_D=abc#def\n'
                         'PADDY_T_E=\n'
                         'PADDY_T_F=from-the-file\n')
            os.environ.pop('PADDY_NO_DOTENV', None)
            try:
                CFG.load_dotenv(p)
            finally:
                os.environ['PADDY_NO_DOTENV'] = '1'
        self.assertEqual(os.environ['PADDY_T_A'], 'chat_completions')
        self.assertEqual(os.environ['PADDY_T_B'], 'value with # hash')
        self.assertEqual(os.environ['PADDY_T_C'], 'quoted')
        self.assertEqual(os.environ['PADDY_T_D'], 'abc#def')        # a # inside a value is kept
        self.assertEqual(os.environ['PADDY_T_E'], '')
        self.assertEqual(os.environ['PADDY_T_F'], 'from-the-environment')   # the real environment wins

    def test_tests_never_read_dotenv(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, '.env')
            with open(p, 'w', encoding='utf-8') as fh:
                fh.write('PADDY_T_A=should-not-load\n')
            CFG.load_dotenv(p)            # PADDY_NO_DOTENV=1 is set by the test helpers
        self.assertNotIn('PADDY_T_A', os.environ)


if __name__ == '__main__':
    unittest.main()
