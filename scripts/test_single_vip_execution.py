"""Real child-process tests with synthetic coverage, not RTL simulation."""
import csv,json,tempfile,unittest,subprocess,sys
from pathlib import Path
from run_single_vip_coverage import execute
FIXTURE="""# AI_EDA_PER_VIP_CHECKPOINT_V2
import csv,os,time
from types import SimpleNamespace
def rf():return SimpleNamespace(VIPS=os.path.join(os.path.dirname(__file__),'..','vips'))
def main(argv):
    assert os.environ['UVM_JOBS']=='1'\n    assert os.environ['VERILATOR']==os.path.join(os.path.dirname(REPORT),'runtime-bin','verilator')\n    assert os.environ['PATH'].split(os.pathsep)[0]==os.path.dirname(os.environ['VERILATOR'])\n    assert argv==['-j','1','APB-VIP'],argv
    with open(REPORT,'w',newline='') as f:csv.writer(f).writerows([['vip','tests','covered_tests'],['APB-VIP',1,1]])
    print('fixture checkpoint',flush=True)
    MODE
"""
class ExecutionTests(unittest.TestCase):
    def exercise(self,mode):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)/'snapshot';(root/'tools').mkdir(parents=True);(root/'vips/APB-VIP').mkdir(parents=True)
            source=root/'tools/coverage.py';source.write_text(FIXTURE.replace('MODE',mode))
            out=Path(d)/'output'
            result=execute({'vip':'APB-VIP','timeout_seconds':1},root,out)
            self.assertTrue(result['csv_exists']);self.assertIn('APB-VIP',(out/'coverage_report.csv').read_text())
            with (out/'coverage_report.csv').open(newline='') as f:self.assertEqual(list(csv.reader(f)),[['vip','tests','covered_tests'],['APB-VIP','1','1']])
            self.assertEqual(result,json.loads((out/'execution.json').read_text()))
            self.assertIn('fixture checkpoint',(out/'flow.log').read_text())
            before=(out/'coverage_report.csv').read_bytes()
            with self.assertRaises(FileExistsError):execute({'vip':'APB-VIP','timeout_seconds':1},root,out)
            self.assertEqual(before,(out/'coverage_report.csv').read_bytes())
            return result
    def test_success(self):
        result=self.exercise('return 0');self.assertEqual(result['returncode'],0);self.assertFalse(result['timeout'])
    def test_failure_retains_checkpoint(self):
        result=self.exercise('return 7');self.assertEqual(result['returncode'],7);self.assertFalse(result['timeout'])
    def test_timeout_retains_checkpoint(self):
        result=self.exercise('time.sleep(10);return 0');self.assertTrue(result['timeout']);self.assertNotEqual(result['returncode'],0)
    def test_cli_exit_codes(self):
        for mode,expected in [('return 0',0),('return 7',1),('time.sleep(10);return 0',124)]:
            with self.subTest(expected=expected),tempfile.TemporaryDirectory() as d:
                root=Path(d)/'snapshot';(root/'tools').mkdir(parents=True);(root/'vips/APB-VIP').mkdir(parents=True)
                (root/'tools/coverage.py').write_text(FIXTURE.replace('MODE',mode))
                request=Path(d)/'request.json';request.write_text(json.dumps({'vip':'APB-VIP','timeout_seconds':1}))
                out=Path(d)/'output'
                result=subprocess.run([sys.executable,str(Path(__file__).with_name('run_single_vip_coverage.py')),'--request',str(request),'--root',str(root),'--output',str(out)],capture_output=True,text=True,timeout=10)
                self.assertEqual(result.returncode,expected,result.stderr)
                info=json.loads(result.stdout);self.assertEqual(info,json.loads((out/'execution.json').read_text()))
                self.assertEqual(info['timeout'],expected==124)
                if expected==1:self.assertEqual(info['returncode'],7)
                with (out/'coverage_report.csv').open(newline='') as f:self.assertEqual(len(list(csv.reader(f))),2)
    def test_fork_pool_module_pickling(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)/'snapshot';(root/'tools').mkdir(parents=True);(root/'vips/APB-VIP').mkdir(parents=True)
            source=FIXTURE.replace('import csv,os,time','import csv,os,time\nfrom multiprocessing import get_context\ndef worker(name):return name')
            source=source.replace("    assert argv==", "    with get_context('fork').Pool(1) as pool:assert list(pool.imap(worker,['APB-VIP']))==['APB-VIP']\n    assert argv==")
            (root/'tools/coverage.py').write_text(source.replace('MODE','return 0'))
            result=execute({'vip':'APB-VIP','timeout_seconds':5},root,Path(d)/'output')
            self.assertEqual(result['returncode'],0,(Path(d)/'output/flow.log').read_text())
            self.assertTrue(result['csv_exists'])
if __name__=='__main__':unittest.main()
