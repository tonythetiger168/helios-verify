"""
triage_agent.py — Phase 1: Regression Triage Agent (helios-verify)

Roadmap Phase 1（0–3 個月）痛點：regression 失敗的分類、最小重跑、log 摘要。
不碰設計流程，對接既有 regression 輸出。

流程（每個失敗 TB）：
  1. LogParser 從 iverilog/verilator log 抽出失敗 TB 與錯誤上下文
  2. LLM 分類：rtl_bug / tb_bug / env_flaky / timeout + 產生最小重跑指令
  3. 自我驗證：執行最小重跑 → pass = 環境/偶發；fail = 真實 bug（回饋進 FeedbackRecord）
  4. triage_report.json + calibration_log（task_type="triage"）

LLM 介面可插拔：
  MockLLMClient     規則版（無模型時可用，已驗證）
  OpenAICompatClient 接本地開源模型（Qwen/Nemotron，OPENAI_BASE_URL 設定）

用法：
  python triage_agent.py --log ../open-ip-portfolio/sim.log --llm mock
  OPENAI_BASE_URL=http://localhost:8000/v1 python triage_agent.py --log run_logs/ --llm openai
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass, field, asdict
from pathlib import Path

from verifier_gate import VerifierGate, RuleRouter, Patch, SimResult


# ---------- 1. Log 解析 ----------

FAIL_PAT = re.compile(
    r"(TEST FAIL|Error|ERROR|%Error|assertion .* failed|FAILED)", re.IGNORECASE)
TB_PAT = re.compile(r"(tb_\w+|test_\w+)\.(sv|v)\b")


@dataclass
class Failure:
    tb: str
    log_excerpt: str          # 錯誤點前後 context（LLM 輸入）
    raw_log: Path
    lineno: int = 0


def parse_log(log_path: Path, context_lines: int = 15) -> list:
    text = log_path.read_text(errors="replace")
    lines = text.splitlines()
    failures = []
    for i, ln in enumerate(lines):
        if FAIL_PAT.search(ln):
            # 找這個失敗屬於哪個 TB
            tb = None
            for j in range(i, max(-1, i - 200), -1):
                m = TB_PAT.search(lines[j])
                if m:
                    tb = m.group(1)
                    break
            start = max(0, i - context_lines)
            failures.append(Failure(
                tb=tb or f"unknown@{log_path.stem}",
                log_excerpt="\n".join(lines[start:i + 3]),
                raw_log=log_path, lineno=i + 1))
    # 同一 TB 去重（取第一個失敗點）
    seen, out = set(), []
    for f in failures:
        if f.tb not in seen:
            seen.add(f.tb)
            out.append(f)
    return out


# ---------- 2. LLM 介面 ----------

class LLMClient:
    def classify(self, failure: Failure) -> dict:
        raise NotImplementedError


class MockLLMClient(LLMClient):
    """規則版：log 特徵 → 分類 + 重跑指令。無模型時的自驗證基線。"""

    def classify(self, f: Failure) -> dict:
        low = f.log_excerpt.lower()
        if "timeout" in low or "hang" in low:
            cat, note = "timeout", "模擬超時/懸停，優先查 clock/reset"
        elif "vvp:" in low or "iverilog" in low and "error" in low:
            cat, note = "tb_bug", "編譯/執行期錯誤在 testbench 層"
        elif "x out" in low or "z out" in low or "unknown" in low:
            cat, note = "rtl_bug", "4-state 洩漏（X/Z），疑似 RTL 未初始化"
        elif "mismatch" in low or "expected" in low or "exp=" in low:
            cat, note = "rtl_bug", "功能比對不符（exp=/expected），疑似 DUT 邏輯錯誤"
        elif "test fail" in low:
            cat, note = "rtl_bug", "功能測試未過（編譯無誤），疑似 DUT 邏輯錯誤"
        elif "cannot find" in low or "no such file" in low:
            cat, note = "env_flaky", "環境問題（缺檔案/路徑）"
        else:
            cat, note = "unknown", "特徵不足，需人工複查"
        return {
            "category": cat,
            "summary": note,
            "rerun_cmd": f"make sim TOP={f.tb}",   # 最小重跑：單一 TB
            "confidence": 0.6,
        }


class OpenAICompatClient(LLMClient):
    """接本地開源模型（OpenAI-compatible endpoint）。"""

    PROMPT = """你是數位 IC 驗證工程師。以下是 regression 失敗的 log 片段。
請以 JSON 回覆（不要輸出其他內容）：
{{"category": "rtl_bug|tb_bug|env_flaky|timeout|unknown",
  "summary": "一句話根因摘要",
  "rerun_cmd": "最小重跑指令",
  "confidence": 0到1}}
TB: {tb}
Log:
{log}"""

    def __init__(self, base_url: str | None = None, model: str = "default"):
        import os
        self.base_url = base_url or os.environ.get("OPENAI_BASE_URL",
                                                   "http://localhost:8000/v1")
        self.model = model

    def classify(self, f: Failure) -> dict:
        import urllib.request
        req = {"model": self.model,
               "messages": [{"role": "user", "content": self.PROMPT.format(
                   tb=f.tb, log=f.log_excerpt[:4000])}],   # 截斷防爆 context
               "temperature": 0.2}
        try:
            r = urllib.request.Request(
                f"{self.base_url}/chat/completions",
                data=json.dumps(req).encode(),
                headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(r, timeout=120) as resp:
                out = json.loads(resp.read())
            content = out["choices"][0]["message"]["content"]
            m = re.search(r"\{.*\}", content, re.DOTALL)
            return json.loads(m.group(0)) if m else {"category": "unknown",
                                                     "summary": "LLM 輸出解析失敗",
                                                     "rerun_cmd": "", "confidence": 0.0}
        except Exception as e:
            return {"category": "unknown", "summary": f"LLM 呼叫失敗: {e}",
                    "rerun_cmd": "", "confidence": 0.0}


# ---------- 3. Triage Agent（含自我驗證重跑） ----------

@dataclass
class TriageResult:
    tb: str
    category: str
    summary: str
    rerun_cmd: str
    rerun_result: str = "not_run"     # pass/fail/error/not_run
    verified: bool = False            # 分類被重跑結果佐證 = True
    confidence: float = 0.0
    raw_log: str = ""


class TriageAgent:
    """分類 + 最小重跑自我驗證 + FeedbackRecord 回饋。"""

    def __init__(self, llm: LLMClient, workdir: Path,
                 gate: VerifierGate | None = None):
        self.llm = llm
        self.workdir = workdir
        self.gate = gate or VerifierGate(RuleRouter("tau_config.json"))

    def _rerun(self, cmd: str, timeout: int = 600) -> str:
        if not cmd:
            return "not_run"
        try:
            p = subprocess.run(cmd, cwd=self.workdir, shell=True,
                               capture_output=True, text=True, timeout=timeout)
            out = p.stdout + p.stderr
            if p.returncode == -1: return "timeout"
            return "pass" if (p.returncode == 0 and "TEST PASS" in out) else "fail"
        except subprocess.TimeoutExpired:
            return "timeout"
        except Exception as e:
            return f"error:{e}"

    def triage(self, log_path: Path) -> list:
        results = []
        for f in parse_log(log_path):
            verdict = self.llm.classify(f)
            rerun = self._rerun(verdict.get("rerun_cmd", ""))

            # 自我驗證：分類與重跑結果一致性
            verified = ((verdict["category"] == "env_flaky" and rerun == "pass") or
                        (verdict["category"] in ("rtl_bug", "tb_bug") and rerun == "fail"))
            tr = TriageResult(tb=f.tb, category=verdict["category"],
                              summary=verdict["summary"],
                              rerun_cmd=verdict.get("rerun_cmd", ""),
                              rerun_result=rerun, verified=verified,
                              confidence=verdict.get("confidence", 0.0),
                              raw_log=str(f.raw_log))
            results.append(tr)

            # 回饋進 calibration_log（routing 資料：triage 準確率本身可校準）
            self.gate.verify(
                Patch(task_type="triage",
                      diff=f"{verdict['category']} rerun={rerun}",
                      files=[f.tb], agent_conf=verdict.get("confidence", 0.5)),
                self.workdir, top=f.tb)
        return results


# ---------- main ----------

def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--log", required=True, type=Path,
                    help="失敗的 sim log 檔案，或含多個 .log 的目錄")
    ap.add_argument("--workdir", default=".", type=Path, help="重跑指令的執行目錄")
    ap.add_argument("--llm", default="mock", choices=["mock", "openai"])
    ap.add_argument("--model", default="default")
    ap.add_argument("--out", default="triage_report.json")
    args = ap.parse_args()

    llm = MockLLMClient() if args.llm == "mock" else \
          OpenAICompatClient(model=args.model)

    logs = ([args.log] if args.log.is_file()
            else sorted(args.log.glob("*.log")))
    if not logs:
        sys.exit(f"找不到 log：{args.log}")

    agent = TriageAgent(llm, args.workdir)
    all_results, t0 = [], time.time()
    for lg in logs:
        rs = agent.triage(lg)
        all_results += rs
        for tr in rs:
            flag = "✓" if tr.verified else "?"
            print(f"[{flag}] {tr.tb:<24} {tr.category:<10} "
                  f"rerun={tr.rerun_result:<8} {tr.summary}")

    n_ver = sum(1 for r in all_results if r.verified)
    print(f"\n== {len(all_results)} failures triaged, {n_ver} verified "
          f"in {time.time()-t0:.1f}s -> {args.out}")
    Path(args.out).write_text(
        json.dumps([asdict(r) for r in all_results], indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
