import json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from run_single_vip_coverage import dispatch
class DispatchTests(unittest.TestCase):
    def setup_paths(self,folder):
        results=Path(folder);root=results/'snapshot';(root/'tools').mkdir(parents=True)
        (root/'tools/coverage.py').write_text('# AI_EDA_PER_VIP_CHECKPOINT_V2')
        request=results/'single-vip-request.json';request.write_text(json.dumps({'vip':'APB-VIP','timeout_seconds':30}))
        (results/'progress.json').write_bytes(b'original progress')
        return request,root,results
    def test_claim_blocks_replay(self):
        with tempfile.TemporaryDirectory() as d:
            request,root,results=self.setup_paths(d)
            with patch('run_single_vip_coverage.execute',return_value={'timeout':False,'returncode':0}) as execute:
                self.assertEqual(dispatch(request,root,results),0)
                with self.assertRaises(FileExistsError):dispatch(request,root,results)
                execute.assert_called_once()
            self.assertEqual((results/'progress.json').read_bytes(),b'original progress')
    def test_failure_retains_claim(self):
        with tempfile.TemporaryDirectory() as d:
            request,root,results=self.setup_paths(d)
            with patch('run_single_vip_coverage.execute',side_effect=RuntimeError('injected failure')):
                with self.assertRaises(RuntimeError):dispatch(request,root,results)
            self.assertTrue((results/'single-vip-request.claimed').exists())
            self.assertEqual((results/'progress.json').read_bytes(),b'original progress')
    def test_named_attempt_preserves_legacy(self):
        with tempfile.TemporaryDirectory() as d:
            request,root,results=self.setup_paths(d)
            legacy=results/'single-vip-coverage';legacy.mkdir();(legacy/'coverage.csv').write_bytes(b'UART evidence')
            (results/'single-vip-request.claimed').write_bytes(b'legacy claim')
            request.write_text(json.dumps({'vip':'APB-VIP','timeout_seconds':30,'attempt_id':'apb-001'}))
            with patch('run_single_vip_coverage.execute',return_value={'timeout':False,'returncode':0}) as execute:
                self.assertEqual(dispatch(request,root,results),0)
                self.assertEqual(execute.call_args.args[2],results/'single-vip-attempts/apb-001/single-vip-coverage')
                with self.assertRaises(FileExistsError):dispatch(request,root,results)
            self.assertEqual((legacy/'coverage.csv').read_bytes(),b'UART evidence')
            self.assertEqual((results/'single-vip-request.claimed').read_bytes(),b'legacy claim')
    def test_attempt_path_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            request,root,results=self.setup_paths(d)
            request.write_text(json.dumps({'vip':'APB-VIP','timeout_seconds':30,'attempt_id':'../escape'}))
            with self.assertRaises(ValueError):dispatch(request,root,results)
            self.assertFalse((results/'single-vip-attempts').exists())
if __name__=='__main__':unittest.main()
