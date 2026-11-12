import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from corpus_runner import run_task
from verifier_gate import FormalResult, SimResult


class CorpusTaskTests(unittest.TestCase):
    def exercise(self, mutant=None, expected="clean"):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "rtl").mkdir()
            rtl = root / "rtl/UART_top.sv"
            rtl.write_text("clean")
            mk = root / "Makefile.UART"
            mk.write_text("sim:\n\t@echo TEST PASS\n")
            seen = []
            test = self

            class InspectGate:
                def verify(self, patch, workdir, **kwargs):
                    seen.append(workdir)
                    test.assertEqual((workdir / "Makefile").read_text(), mk.read_text())
                    test.assertEqual((workdir / "rtl/UART_top.sv").read_text(), expected)
                    test.assertEqual(kwargs, {"top": "tb_UART", "dut_top": "UART_top"})
                    return SimpleNamespace(route="fast_path", sim_result=SimResult.PASS,
                                           formal_result=FormalResult.SKIPPED,
                                           merged=True, helios_conf=0.8)

            result = run_task(InspectGate(), root,
                              {"proto": "UART", "makefile": mk}, mutant)
            self.assertEqual(result["sim"], "pass")
            self.assertEqual(result["mutant"], mutant["id"] if mutant else None)
            self.assertEqual(rtl.read_text(), "clean")
            self.assertFalse(seen[0].exists())

    def test_clean_reaches_gate_with_default_makefile(self):
        self.exercise()

    def test_affected_mutant_is_isolated(self):
        self.exercise({"id": "m1", "affected": ["UART"],
                       "buggy_content": {"rtl/UART_top.sv": "buggy"}}, "buggy")

    def test_unrelated_mutant_does_not_modify_rtl(self):
        self.exercise({"id": "m2", "affected": ["SPI"],
                       "buggy_content": {"rtl/SPI_top.sv": "buggy"}})


if __name__ == "__main__":
    unittest.main()
