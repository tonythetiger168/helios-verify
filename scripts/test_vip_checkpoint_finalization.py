import ast,csv,os,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from patch_vip_coverage_checkpoint import patched,MARKER
ROOT=Path(__file__).resolve().parents[1]
class FinalizationTests(unittest.TestCase):
    def sources(self):
        folder=ROOT/'build/vip-coverage-checkpoint'
        return (folder/'upstream-original.py').read_text(),(folder/'upstream-patched.py').read_text()
    def test_fresh_and_upgrade_match(self):
        original,legacy=self.sources()
        self.assertEqual(patched(original),patched(legacy))
    def test_idempotent(self):
        text=patched(self.sources()[0]);self.assertEqual(patched(text),text)
    def test_final_uses_checkpoint(self):
        tree=ast.parse(patched(self.sources()[0]));main=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='main')
        self.assertIsInstance(main.body[-2],ast.Expr)
        self.assertEqual(main.body[-2].value.func.id,'checkpoint_rows')
        self.assertNotIn('with open(REPORT, "w"',ast.unparse(main))
    def test_replace_failure_preserves_report(self):
        tree=ast.parse(patched(self.sources()[0]));helper=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='checkpoint_rows')
        with tempfile.TemporaryDirectory() as d:
            report=Path(d)/'coverage.csv';report.write_text('vip,tests,covered_tests\nold,1,1\n')
            before=report.read_bytes();scope={'REPORT':str(report),'csv':csv,'os':os,'METRICS':[]}
            exec(compile(ast.Module(body=[helper],type_ignores=[]),'helper','exec'),scope)
            with patch.object(Path,'replace',side_effect=OSError('injected interruption')):
                with self.assertRaises(OSError):scope['checkpoint_rows']([['new',1,1]])
            self.assertEqual(before,report.read_bytes())
            scope['checkpoint_rows']([['new',1,1]])
            with report.open(newline='') as f:rows=list(csv.reader(f))
            self.assertEqual([r[0] for r in rows[1:]],['new','old'])
if __name__=='__main__':unittest.main()
