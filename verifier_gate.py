"""
verifier_gate.py — Verifier 層骨架 v0.1 (helios-verify project)
 Static checks (Verilator lint + Yosys synth) -> Router -> sim/formal -> FeedbackRecord
 Router 介面預留：M1 用 RuleRouter（rule-based τ），M2 換 HeliosLM scorer。

用法：
    python verifier_gate.py            # demo：fast_path 與 deep_verify 各跑實例
"""

from __future__ import annotations

import json
import subprocess
import time
import uuid
from dataclasses import dataclass, field, asdict
from enum import Enum
from pathlib import Path


# ---------------- Schema ----------------

class Route(str, Enum):
    FAST_PATH = "fast_path"
    DEEP_VERIFY = "deep_verify"
    REJECT = "reject"

class SimResult(str, Enum):
    PASS = "pass"; FAIL = "fail"; TIMEOUT = "timeout"; SKIPPED = "skipped"

class FormalResult(str, Enum):
    PROVED = "proved"; CEX_FOUND = "cex_found"; UNKNOWN = "unknown"; SKIPPED = "skipped"

@dataclass
class VerifyDepth:
    run_simulation: bool = True
    run_formal: bool = False
    regression_subset: str = "affected_modules"

@dataclass
class StaticSignals:
    lines_changed: int = 0
    files_touched: int = 1
    verilator_lint_warnings: int = 0
    sv_syntax_check: str = "unknown"   # pass / fail / unknown
    historical_task_success_rate: float = 0.5

@dataclass
class Patch:
    task_type: str                     # debug_triage | coverage_test | rtl_fix | assertion_add
    diff: str = ""
    files: list = field(default_factory=list)
    agent_conf: float = 0.5

@dataclass
class FeedbackRecord:
    patch_id: str
    route: str
    static_signals: StaticSignals
    sim_result: SimResult = SimResult.SKIPPED
    formal_result: FormalResult = FormalResult.SKIPPED
    merged: bool = False
    helios_conf: float = -1.0
    timestamp: float = field(default_factory=time.time)


# ---------------- Router ----------------

class RuleRouter:
    """M1 過渡版。τ 從 tau_config.json 讀；單調更新由 tau_calibrate.py 負責。"""

    def __init__(self, cfg_path: str = "tau_config.json"):
        cfg = json.loads(Path(cfg_path).read_text()) if Path(cfg_path).exists() \
              else {"tau_fast": 0.7, "tau_reject": 0.15}
        self.tau_fast, self.tau_reject = cfg["tau_fast"], cfg["tau_reject"]

    def route(self, patch: Patch, sig: StaticSignals) -> tuple:
        # M1 啟發式 conf，代理 HeliosLM；M2 換成 helios.score(features)
        conf = (patch.agent_conf * 0.3
                + sig.historical_task_success_rate * 0.4
                + (0.2 if sig.sv_syntax_check == "pass" else 0.0)
                + max(0.0, 0.1 - sig.verilator_lint_warnings * 0.05))
        if conf >= self.tau_fast:
            return Route.FAST_PATH, VerifyDepth(run_formal=False), conf
        if conf < self.tau_reject:
            return Route.REJECT, VerifyDepth(run_simulation=False), conf
        return Route.DEEP_VERIFY, VerifyDepth(run_formal=True), conf


# ---------------- Tool runners ----------------

def _run(cmd: list, cwd: Path, timeout: int = 300) -> tuple:
    try:
        p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout)
        return p.returncode, p.stdout + p.stderr
    except subprocess.TimeoutExpired:
        return -1, "TIMEOUT"
    except OSError as e:   # FileNotFoundError / PermissionError：工具未安裝或不可執行
        return -2, f"TOOL_MISSING: {e}"

def verilator_lint(workdir: Path, top: str) -> tuple:
    rc, out = _run(["verilator", "--lint-only", "-Wall", "--timing",
                    "--top-module", top, "-f", "files.f"], workdir)
    return rc == 0, out, out.count("Warning")

def verilator_sim(workdir: Path, top: str, timeout: int = 600,
                  oracle=None) -> SimResult:
    """模擬經由 Makefile 'sim' target（iverilog + vvp，TEST FAIL 時 make 非零退出）。
    傳入 oracle（TxOracle）時，語意判定優先；偵測不到 tx 錄製則 fallback 字串比對。"""
    rc, out = _run(["make", "sim", f"TOP={top}"], workdir, timeout)
    if rc == -1: return SimResult.TIMEOUT
    str_result = (SimResult.PASS
                  if (rc == 0 and "TEST PASS" in out) else SimResult.FAIL)
    if oracle is not None:
        try:
            from tx_adapter import sim_with_tx_oracle
            return sim_with_tx_oracle(workdir, top, oracle,
                                      lambda w, t, to: str_result, timeout)
        except ImportError:
            pass  # tx_adapter 不存在時維持字串比對
    return str_result

def _rtl_sources(workdir: Path) -> list[str]:
    """Return RTL sources for Yosys, preferring files.f but excluding testbenches."""
    sources = []
    files_f = workdir / "files.f"
    if files_f.exists():
        for line in files_f.read_text().splitlines():
            item = line.strip()
            if not item or item.startswith("#"):
                continue
            name = Path(item).name.lower()
            if item.endswith((".v", ".sv")) and not name.startswith(("tb_", "test_")):
                sources.append(item)
    if not sources:
        sources = [str(p.relative_to(workdir))
                   for p in sorted((workdir / "rtl").glob("*.v")) +
                          sorted((workdir / "rtl").glob("*.sv"))]
    return sources


def _read_rtl_tcl(workdir: Path) -> str:
    sources = _rtl_sources(workdir)
    if not sources:
        raise FileNotFoundError(f"no RTL sources found under {workdir}")
    sv = any(s.endswith(".sv") for s in sources)
    return "read_verilog " + ("-sv " if sv else "") + " ".join(sources)


def yosys_synth_check(workdir: Path, top: str) -> tuple:
    tcl = f"{_read_rtl_tcl(workdir)}; hierarchy -top {top}; synth; check"
    rc, out = _run(["yosys", "-p", tcl], workdir)
    return rc == 0 and "ERROR" not in out, out

def yosys_formal(workdir: Path, top: str, gold: str | None = None) -> FormalResult:
    """gold=None 跑 SAT assertion check；有 golden 時跑等價驗證。"""
    read_rtl = _read_rtl_tcl(workdir)
    if gold:
        tcl = (f"{read_rtl}; prep -top {top}; "
               f"equiv_make {gold} {top} equiv; prep -top equiv; "
               f"equiv_simple; equiv_status")
    else:
        tcl = f"{read_rtl}; prep -top {top}; sat -verify -prove-asserts"
    rc, out = _run(["yosys", "-p", tcl], workdir, timeout=900)
    if rc != 0: return FormalResult.UNKNOWN
    if "SAT proof finished" in out or "Proved" in out: return FormalResult.PROVED
    if "Counter" in out or "cex" in out.lower(): return FormalResult.CEX_FOUND
    return FormalResult.UNKNOWN


# ---------------- VerifierGate ----------------

class VerifierGate:
    def __init__(self, router: RuleRouter, log_dir: Path = Path("calibration_log"),
                 oracle=None):
        self.router = router
        self.oracle = oracle          # TxOracle 或 None（字串比對）
        self.log_dir = log_dir
        self.log_dir.mkdir(exist_ok=True)

    def verify(self, patch: Patch, workdir: Path, top: str,
               gold: str | None = None, dut_top: str | None = None) -> FeedbackRecord:
        pid = str(uuid.uuid4())[:8]
        sig = StaticSignals(lines_changed=patch.diff.count("\n+"),
                            files_touched=len(patch.files) or 1)
        rec = FeedbackRecord(patch_id=pid, route="", static_signals=sig)
        dut_top = dut_top or top

        # 1. 靜態檢查（永遠跑，便宜）
        lint_ok, lint_out, sig.verilator_lint_warnings = verilator_lint(workdir, top)
        synth_ok, _ = yosys_synth_check(workdir, dut_top)
        sig.sv_syntax_check = "pass" if (lint_ok and synth_ok) else "fail"

        # 2. Routing
        route, depth, conf = self.router.route(patch, sig)
        rec.route, rec.helios_conf = route.value, conf
        if route == Route.REJECT:
            self._log(rec)
            return rec

        # 3. 模擬
        rec.sim_result = (verilator_sim(workdir, top, oracle=self.oracle)
                          if depth.run_simulation else SimResult.SKIPPED)

        # 4. 形式驗證（conf 低或 depth 要求時）
        if depth.run_formal or conf < 0.3:
            rec.formal_result = yosys_formal(workdir, dut_top, gold)

        # 5. Merge 判定
        rec.merged = (rec.sim_result == SimResult.PASS and
                      rec.formal_result in (FormalResult.PROVED, FormalResult.SKIPPED))
        self._log(rec)
        return rec

    def _log(self, rec: FeedbackRecord) -> None:
        with open(self.log_dir / f"{rec.patch_id}.json", "w") as f:
            json.dump(asdict(rec), f, indent=2, default=str)


# ---------------- Demo ----------------

if __name__ == "__main__":
    workdir = Path(__file__).parent
    gate = VerifierGate(RuleRouter(workdir / "tau_config.json"))

    cases = [
        Patch(task_type="rtl_fix", diff="+1 line", agent_conf=0.9),   # -> fast_path
        Patch(task_type="debug_triage", diff="+10\n+lines", agent_conf=0.5),  # -> deep_verify
        Patch(task_type="rtl_fix", diff="", agent_conf=0.1),          # low-confidence deep_verify
    ]
    for p in cases:
        rec = gate.verify(p, workdir, top="tb_alu", dut_top="alu")
        print(f"[{rec.route:>11}] conf={rec.helios_conf:.3f} "
              f"sim={rec.sim_result.value} formal={rec.formal_result.value} "
              f"merged={rec.merged}")
    print(f"\nFeedbackRecord -> {gate.log_dir}/")
    print("Next: run `python tau_calibrate.py` after >=100 records accumulate.")
