"""
minisim_engine.py — VerifierGate 的 minisim 引擎後端

VerifierGate 原本的四步（Verilator lint、Yosys synth、iverilog 模擬、Yosys sat/equiv）
各有一個 minisim 對應：mlint、msynth（沒裝時以 msim -elab 代替可綜合檢查）、msim、
mformal / mequiv。全部經 mmcp（minisim 的 MCP server，`mmcp -call TOOL JSON`）呼叫，
回 JSON，不用猜 log 字串。

找 mmcp 的順序：$MMCP，$MINISIM_HOME/bin/mmcp，PATH。
付費工具（msynth、mformal、mequiv）要 minisim-tools 的外掛與授權；沒有時對應一步回 None，
VerifierGate 會改用開源工具那一步。
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path


class EngineMissing(RuntimeError):
    pass


def mmcp_path() -> str | None:
    for cand in (os.environ.get("MMCP"),
                 os.path.join(os.environ["MINISIM_HOME"], "bin", "mmcp") if os.environ.get("MINISIM_HOME") else None,
                 shutil.which("mmcp")):
        if cand and os.path.isfile(cand):
            return cand
    return None


def call(workdir: Path, tool: str, args: dict, timeout: int = 900) -> tuple[dict, bool]:
    """One mmcp tool call in workdir; (result, is_error)."""
    exe = mmcp_path()
    if not exe:
        raise EngineMissing("mmcp not found (set MMCP or MINISIM_HOME, or put it on PATH)")
    cmd = ([os.environ.get("PYTHON", "python3")] if exe.endswith(".py") else []) + \
          [exe, "-root", str(workdir), "-call", tool, json.dumps(args)]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return {"error": "timeout", "timed_out": True}, True
    if p.returncode == 2 and "no tool" in p.stderr:
        return {"error": f"{tool} not installed", "missing": True}, True
    try:
        out = json.loads(p.stdout)
    except ValueError:
        return {"error": (p.stderr or p.stdout)[-2000:]}, True
    return out, p.returncode != 0


def available(workdir: Path) -> set[str]:
    exe = mmcp_path()
    if not exe:
        return set()
    cmd = ([os.environ.get("PYTHON", "python3")] if exe.endswith(".py") else []) + [exe, "-root", str(workdir), "-list"]
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    try:
        return {t["name"] for t in json.loads(p.stdout)}
    except ValueError:
        return set()


def _filelist(workdir: Path) -> list[str]:
    return ["-f", "files.f"] if (workdir / "files.f").exists() else []


def lint(workdir: Path, top: str) -> tuple[bool, str, int, dict]:
    """mlint on the files.f set: (ok, text, warning+error count, report).
    ok = no error-severity finding (Verilator -Wall 的「無 error」對應)."""
    r, err = call(workdir, "mlint_check", {"files": [], "top": top, "args": _filelist(workdir), "max_findings": 50})
    if err or not r.get("ok"):
        return False, json.dumps(r)[:4000], 0, r
    s = r.get("summary") or {}
    n = int(s.get("error", 0)) + int(s.get("warning", 0))
    text = "\n".join(f"{f.get('file')}:{f.get('line')}: {f.get('severity')}: [{f.get('rule')}] {f.get('text')}"
                     for f in r.get("findings", []))
    return int(s.get("error", 0)) == 0, text, n, r


def synth_check(workdir: Path, dut_top: str, rtl: list[str], tools: set[str]) -> tuple[bool | None, dict]:
    """msynth when installed (付費), else msim -elab of the RTL (可解析、可展開，但不是綜合)."""
    if "msynth_run" in tools:
        r, err = call(workdir, "msynth_run", {"files": rtl, "top": dut_top})
        if r.get("licence") is False:
            return None, r
        return (not err and r.get("exit_status") == 0), r
    r, err = call(workdir, "msim_run", {"files": rtl, "top": dut_top, "args": ["-elab"]})
    return (not err and r.get("status") == "pass"), dict(r, proxy="msim -elab")


def sim(workdir: Path, top: str, timeout: int = 600, pass_text: str = "TEST PASS") -> tuple[str, dict]:
    """msim on files.f: 'pass' | 'fail' | 'timeout'. Pass = msim verdict pass AND the
    testbench printed pass_text (the Makefile 'sim' target 的同一判準)."""
    r, err = call(workdir, "msim_run", {"files": [], "top": top, "args": _filelist(workdir), "timeout": timeout},
                  timeout=timeout + 60)
    if r.get("status") == "timeout" or r.get("timed_out"):
        return "timeout", r
    log = ""
    if r.get("log"):
        try:
            log = (workdir / r["log"]).read_text(errors="replace")
        except OSError:
            log = r.get("log_tail", "")
    ok = r.get("passed") and pass_text in log
    return ("pass" if ok else "fail"), r


def formal(workdir: Path, dut_top: str, rtl: list[str], tools: set[str], gold: str | None = None) -> tuple[str | None, dict]:
    """'proved' | 'cex_found' | 'unknown', or None when the tool is not available.
    gold: mequiv between module gold and dut_top in the same RTL (-reftop / -impltop);
    else mformal on the assertions of dut_top."""
    if gold:
        if "mequiv_check" not in tools:
            return None, {}
        r, _ = call(workdir, "mequiv_check", {"ref": rtl, "impl": rtl, "args": ["-reftop", gold, "-impltop", dut_top]})
        if r.get("licence") is False:
            return None, r
        return {"equivalent": "proved", "not_equivalent": "cex_found"}.get(r.get("verdict"), "unknown"), r
    if "mformal_check" not in tools:
        return None, {}
    r, _ = call(workdir, "mformal_check", {"files": rtl, "top": dut_top})
    if r.get("licence") is False:
        return None, r
    props = ((r.get("report") or {}).get("properties")) or []
    results = [p.get("result") for p in props if isinstance(p, dict) and p.get("kind", "assert") == "assert"]
    if any(x == "falsified" for x in results):
        return "cex_found", r
    if results and all(x == "proven" for x in results):
        return "proved", r
    if not results and r.get("exit_status") == 0:
        return "proved", r          # 沒有斷言：與 yosys sat -prove-asserts 的空證明相同
    return "unknown", r
