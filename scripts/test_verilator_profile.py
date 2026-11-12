import json,subprocess,sys,tempfile,unittest,time
from pathlib import Path
from verilator_profile import render_wrapper
class ProfileTests(unittest.TestCase):
    def test_normalizes_jobs_and_preserves_output_and_failure(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);backend=root/'backend';backend.write_text('#!'+sys.executable+'\nimport sys\nprint("COMPILER full failure detail")\nsys.exit(7)\n');backend.chmod(0o755)
            wrapper=root/'verilator';logs=root/'logs';wrapper.write_text(render_wrapper(backend,logs));wrapper.chmod(0o755)
            p=subprocess.run([str(wrapper),'-j','2','-j4','--build-jobs=8','--build-jobs','3','dut.sv'],capture_output=True,text=True)
            self.assertEqual(p.returncode,7);self.assertIn('full failure detail',p.stdout)
            metadata=next(path for path in logs.glob('*.json') if not path.name.endswith('.exit.json'))
            effective=json.loads(metadata.read_text())['effective'];self.assertEqual(effective.count('-j'),1);self.assertEqual(effective[-2:],['-j','1']);self.assertIn('dut.sv',effective)
            self.assertNotIn('-j4',effective);self.assertNotIn('--build-jobs=8',effective)
            self.assertIn('full failure detail',next(logs.glob('*.log')).read_text())
            self.assertEqual(json.loads(next(logs.glob('*.exit.json')).read_text())['returncode'],7)
    def test_small_output_is_flushed_while_backend_runs(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);backend=root/'backend';backend.write_text('#!'+sys.executable+'\nimport time\nprint("EARLY compiler evidence",flush=True)\ntime.sleep(1)\n');backend.chmod(0o755)
            wrapper=root/'verilator';logs=root/'logs';wrapper.write_text(render_wrapper(backend,logs));wrapper.chmod(0o755)
            with subprocess.Popen([str(wrapper),'dut.sv'],stdout=subprocess.DEVNULL) as process:
                deadline=time.monotonic()+0.7;visible=False
                while time.monotonic()<deadline:
                    files=list(logs.glob('*.log')) if logs.exists() else []
                    if files and 'EARLY compiler evidence' in files[0].read_text():visible=True;break
                    time.sleep(0.02)
                self.assertTrue(visible,'small compiler output was buffered until exit')
                self.assertIsNone(process.poll());self.assertEqual(process.wait(timeout=3),0)
if __name__=='__main__':unittest.main()
