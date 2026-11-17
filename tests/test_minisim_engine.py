"""minisim 引擎後端（minisim_engine.py + VerifierGate engine=minisim）的測試。

需要 mmcp（MMCP、MINISIM_HOME 或 PATH）；沒有時整組跳過。
    python -m unittest tests/test_minisim_engine.py
"""
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import minisim_engine as me                                  # noqa: E402
from verifier_gate import (VerifierGate, RuleRouter, Patch,  # noqa: E402
                           SimResult, FormalResult)

HAVE = me.mmcp_path() is not None


def demo_copy(tmp):
    d = Path(tmp) / "w"
    d.mkdir()
    for f in ("files.f", "tau_config.json"):
        shutil.copy(ROOT / f, d / f)
    shutil.copytree(ROOT / "rtl", d / "rtl")
    return d


@unittest.skipUnless(HAVE, "mmcp not installed")
class Engine(unittest.TestCase):
    def test_good_and_mutant(self):
        with tempfile.TemporaryDirectory() as tmp:
            w = demo_copy(tmp)
            ok, _, n, rep = me.lint(w, "tb_alu")
            self.assertTrue(ok, rep)
            verdict, r = me.sim(w, "tb_alu")
            self.assertEqual(verdict, "pass", r)
            alu = w / "rtl" / "alu.v"
            alu.write_text(alu.read_text().replace("y = a - b;", "y = b - a;"))   # a mutant the testbench catches
            verdict, r = me.sim(w, "tb_alu")
            self.assertEqual(verdict, "fail", r)
            alu.write_text(alu.read_text().replace("OP_AND:  y = a & b;", "OP_AND:  y = a & ;"))
            ok, text, _, _ = me.lint(w, "tb_alu")
            self.assertFalse(ok)

    def test_gate(self):
        with tempfile.TemporaryDirectory() as tmp:
            w = demo_copy(tmp)
            gate = VerifierGate(RuleRouter(str(w / "tau_config.json")), log_dir=w / "log", engine="minisim")
            rec = gate.verify(Patch(task_type="rtl_fix", diff="", agent_conf=0.1), w, top="tb_alu", dut_top="alu")
            self.assertEqual(rec.engine, "minisim")
            self.assertEqual(rec.sim_result, SimResult.PASS)
            self.assertIn(rec.formal_result, (FormalResult.PROVED, FormalResult.UNKNOWN, FormalResult.CEX_FOUND))
            logged = json.loads(next((w / "log").glob("*.json")).read_text())
            self.assertEqual(logged["engine"], "minisim")

    def test_bad_engine(self):
        with self.assertRaises(ValueError):
            VerifierGate(RuleRouter("tau_config.json"), log_dir=Path(tempfile.mkdtemp()), engine="vcs")


if __name__ == "__main__":
    unittest.main()
