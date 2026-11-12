import tempfile,unittest
from pathlib import Path
from run_single_vip_coverage import validate
class RequestTests(unittest.TestCase):
    def test_requests(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);(root/'tools').mkdir();source=root/'tools/coverage.py';source.write_text('# AI_EDA_PER_VIP_CHECKPOINT_V2')
            self.assertEqual(validate({'vip':'UART-UVM-VIP','timeout_seconds':300},root)[:2],('UART-UVM-VIP',300))
            for name in ('','-j','../APB-VIP','APB-VIP AHB-VIP'):
                with self.assertRaises(ValueError):validate({'vip':name,'timeout_seconds':300},root)
            with self.assertRaises(ValueError):validate({'vip':'APB-VIP','timeout_seconds':True},root)
            with self.assertRaises(ValueError):validate({'vip':'APB-VIP','timeout_seconds':3601},root)
            source.write_text('# V1')
            with self.assertRaises(ValueError):validate({'vip':'APB-VIP','timeout_seconds':300},root)
if __name__=='__main__':unittest.main()
