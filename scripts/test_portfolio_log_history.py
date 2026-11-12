import importlib.util
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('runner', Path(__file__).with_name('run_portfolio_extended.py'))
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)

class LogHistoryTests(unittest.TestCase):
    def test_new_log(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d)/'flow.log'
            runner.archive_previous_log(p)
            self.assertEqual(list(Path(d).iterdir()), [])

    def test_resume_preserves_interrupted_bytes(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d)/'flow.log'
            p.write_bytes(b'compile killed\n\xff')
            runner.archive_previous_log(p)
            p.write_text('new attempt')
            self.assertEqual(p.with_name('flow.log.previous-1').read_bytes(), b'compile killed\n\xff')

    def test_multiple_resumes_do_not_overwrite_history(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d)/'flow.log'
            for value in ('first', 'second', ''):
                p.write_text(value)
                runner.archive_previous_log(p)
            self.assertEqual([p.with_name(f'flow.log.previous-{i}').read_text() for i in (1,2,3)], ['first','second',''])

if __name__ == '__main__':
    unittest.main()
