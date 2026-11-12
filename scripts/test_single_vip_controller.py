"""Controller startup with relocated fixture paths; no container or RTL run."""
import ast,json,subprocess,sys,tempfile,unittest
from pathlib import Path
from test_single_vip_execution import FIXTURE
SCRIPTS=Path(__file__).resolve().parent
class ControllerTests(unittest.TestCase):
    def exercise(self,named=False):
        with tempfile.TemporaryDirectory() as d:
            folder=Path(d);results=folder/'results';results.mkdir();root=folder/'snapshot'
            (root/'tools').mkdir(parents=True);(root/'vips/APB-VIP').mkdir(parents=True)
            (root/'tools/coverage.py').write_text(FIXTURE.replace('MODE','return 0'))
            request=results/'single-vip-request.json';payload={'vip':'APB-VIP','timeout_seconds':2}
            if named:payload['attempt_id']='apb-001'
            request.write_text(json.dumps(payload))
            progress=results/'progress.json';progress.write_bytes(b'original progress')
            tree=ast.parse((SCRIPTS/'run_portfolio_extended.py').read_text())
            for node in tree.body:
                if isinstance(node,ast.Assign) and len(node.targets)==1 and isinstance(node.targets[0],ast.Name):
                    if node.targets[0].id in ('OUT','VIP'):
                        node.value=ast.Call(func=ast.Name(id='Path',ctx=ast.Load()),args=[ast.Constant(str(results if node.targets[0].id=='OUT' else root))],keywords=[])
                if isinstance(node,ast.FunctionDef) and node.name=='main':
                    node.body=[ast.Raise(exc=ast.Call(func=ast.Name(id='RuntimeError',ctx=ast.Load()),args=[ast.Constant('BATCH MUST NOT RUN')],keywords=[]),cause=None)]
            # Relocate only fixture paths; make any fall-through into batch observable.
            controller=folder/'controller.py';controller.write_text('import sys\nsys.path.insert(0,'+repr(str(SCRIPTS))+')\n'+ast.unparse(ast.fix_missing_locations(tree)))
            def start():return subprocess.run([sys.executable,str(controller)],capture_output=True,text=True,timeout=10)
            first=start();self.assertEqual(first.returncode,0,first.stderr)
            output=(results/'single-vip-attempts/apb-001/single-vip-coverage') if named else results/'single-vip-coverage'
            if named:self.assertFalse((results/'single-vip-request.claimed').exists())
            self.assertTrue((output/'coverage_report.csv').exists())
            saved=(output/'execution.json').read_bytes()
            second=start();self.assertNotEqual(second.returncode,0);self.assertIn('FileExistsError',second.stderr)
            request.unlink();third=start();self.assertNotEqual(third.returncode,0);self.assertIn('FileNotFoundError',third.stderr)
            for result in (first,second,third):self.assertNotIn('BATCH MUST NOT RUN',result.stderr)
            self.assertEqual(saved,(output/'execution.json').read_bytes());self.assertEqual(progress.read_bytes(),b'original progress')
    def test_start_replay_and_orphan_claim(self):self.exercise()
    def test_named_start_replay_and_orphan_claim(self):self.exercise(named=True)
if __name__=='__main__':unittest.main()
