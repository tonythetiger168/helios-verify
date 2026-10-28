"""
tx_adapter.py — Dynamis TX semantic oracle adapter (helios-verify)

把 verifier_gate 的 pass/fail 判定從「grep TEST PASS」升級為語意級：
  golden.jsonl ──┐
                 ├──▶ txdiff ──▶ exit code = FeedbackRecord ground truth
  run.jsonl ─────┘

啟用條件：workdir 存在 tx/ 錄製輸出（tx.jsonl 或 dynamis 設定路徑）。
偵測不到時自動 fallback 到原本的字串比對，不破壞既有流程。

整合方式（open-ip-portfolio 的 testbench）：
  1. 編譯時加 -I<dynamis>/tx/src 與 txdpi.cpp（DPI-C 層）
  2. tb 在關鍵 interface 變化點呼叫：
       import "DPI-C" context function void tx_record(
           input string kind, input longint t, input string fields);
       always @(posedge clk) if (valid)
         tx_record("write", $time, $sformatf("addr=%0d,data=%0d", addr, data));
  3. run 產生 tx.jsonl；golden 版本存入 golden/ 目錄
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from verifier_gate import SimResult


class TxOracle:
    """語意級 oracle：txdiff golden vs run，exit 1 = 語意差異。"""

    def __init__(self, dynamis_root: Path | None = None):
        self.tools = dynamis_root or Path("../dynamis")
        self.txdiff = self.tools / "tx" / "tools" / "txdiff.py"
        self.enabled = self.txdiff.exists()

    def detect(self, workdir: Path) -> Path | None:
        """在 workdir 找錄製輸出；多個時取最新修改。"""
        cands = sorted(workdir.glob("**/tx*.jsonl"),
                       key=lambda p: p.stat().st_mtime)
        return cands[-1] if cands else None

    def run_diff(self, golden: Path, run: Path) -> SimResult:
        if not self.enabled or not golden.exists():
            return SimResult.SKIPPED  # fallback：交給字串比對
        p = subprocess.run(["python3", str(self.txdiff), str(golden), str(run)],
                           capture_output=True, text=True, timeout=300)
        if p.returncode == 0:
            return SimResult.PASS     # 語意一致（對時序抖動魯棒）
        if p.returncode == 1:
            return SimResult.FAIL     # 語意差異：欄位級精確
        return SimResult.TIMEOUT      # txdiff 本身錯誤 → 視為 timeout 標記複查


# ---------- 接進 VerifierGate 的最小修改 ----------

def sim_with_tx_oracle(workdir: Path, top: str,
                       oracle: TxOracle, run_sim_fn, timeout: int = 600) -> SimResult:
    """
    包裝原有的模擬執行：
      1. 跑模擬（run_sim_fn：原本的 make sim / verilator 流程）
      2. 若產生 tx.jsonl 且有 golden → txdiff 語意判定
      3. 否則 fallback 字串比對結果
    golden 約定：workdir/../golden/<proto>.jsonl 或 workdir/golden/tx.jsonl
    """
    str_result = run_sim_fn(workdir, top, timeout)

    if not oracle.enabled:
        return str_result

    run_log = oracle.detect(workdir)
    if run_log is None:
        return str_result  # 此任務未錄製，沿用字串比對

    goldens = [workdir / "golden" / "tx.jsonl",
               *sorted(Path(workdir).parent.glob(f"golden/*.jsonl"))]
    golden = next((g for g in goldens if g.exists()), None)

    tx_result = oracle.run_diff(golden, run_log) if golden else SimResult.SKIPPED
    if tx_result == SimResult.SKIPPED:
        return str_result

    # 語意判定與字串判定衝突時，以語意為準並記警告（此為關鍵校準信號：
    # TEST PASS 但 txdiff FAIL = testbench 檢查不足，應回報）
    if str_result != tx_result:
        print(f"[tx_adapter] MISMATCH str={str_result.value} tx={tx_result.value} "
              f"in {workdir} — golden 檢查可能不足，請人工複查")
    return tx_result


# ---------- 使用範例 ----------

if __name__ == "__main__":
    from verifier_gate import VerifierGate, RuleRouter, Patch

    oracle = TxOracle(Path("../dynamis"))

    def my_sim(workdir: Path, top: str, timeout: int) -> SimResult:
        # 原本的模擬流程（字串比對版）
        import subprocess
        p = subprocess.run(["make", "sim", f"TOP={top}"],
                           cwd=workdir, capture_output=True, text=True, timeout=timeout)
        if p.returncode == 0 and "TEST PASS" in p.stdout:
            return SimResult.PASS
        if p.returncode == -1:
            return SimResult.TIMEOUT
        return SimResult.FAIL

    # 接入方式：在 VerifierGate.verify 裡把 verilator_sim 換成 sim_with_tx_oracle
    # （M2 一併修改 verifier_gate.py，加 --oracle dynamis 開關）
    print("tx_adapter ready:", "enabled" if oracle.enabled else "fallback-mode")
    print("txdiff found:", oracle.txdiff)
