"""
corpus_runner.py — calibration corpus batch runner (helios-verify)

掃描 open-ip-portfolio（或任何符合慣例的目錄），把每個協議當成一個
verification task 餵給 VerifierGate，批量累積 calibration_log。

前置：
    git clone https://github.com/tonythetiger168/open-ip-portfolio.git ../open-ip-portfolio
    需要 iverilog / yosys / verilator（open-ip-portfolio 自帶 setup_tools.sh）

用法：
    python corpus_runner.py --ip-root ../open-ip-portfolio            # 全量 117 任務
    python corpus_runner.py --ip-root ../open-ip-portfolio --proto I2C,UART
    python corpus_runner.py --ip-root ../open-ip-portfolio --family MEMCH  # shared-core 家族回歸
    python corpus_runner.py --ip-root ../open-ip-portfolio --mutants docs/mutants.json  # 注入已知 bug 產生負樣本

配對校準（M1 vs M2，compat_check.py 輸入）：
    python corpus_runner.py --ip-root ../open-ip-portfolio --proto UART \
        --mutants mutants.json --router rule   --cal-log cal_rule.jsonl
    python corpus_runner.py --ip-root ../open-ip-portfolio --proto UART \
        --mutants mutants.json --router helios --cal-log cal_helios.jsonl
    python ../compat_check.py --rule-log cal_rule.jsonl --helios-log cal_helios.jsonl
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
import time
import zlib
from pathlib import Path

from verifier_gate import (VerifierGate, RuleRouter, Patch, SimResult,
                           FormalResult)


# ---------- 環境探測 ----------

def check_tools() -> dict:
    return {t: bool(shutil.which(t)) for t in ["iverilog", "yosys", "verilator", "make"]}


# ---------- 任務發現 ----------

def discover_tasks(ip_root: Path) -> list[dict]:
    """從 Makefile.<PROTO> 發現任務；解析 shared-core 家族標記（若有 docs 索引）。"""
    tasks = []
    for mk in sorted(ip_root.glob("Makefile.*")):
        proto = mk.name.replace("Makefile.", "")
        top_sv = ip_root / "rtl" / f"{proto}_top.sv"
        if not top_sv.exists():
            # 部分協議 top 名稱與 Makefile 不同，退回掃 rtl/ 目錄
            candidates = list((ip_root / "rtl").glob("*_top.sv"))
            if not candidates:
                continue
        tasks.append({"proto": proto, "makefile": mk})
    return tasks


def load_family_index(ip_root: Path) -> dict:
    """shared-core 家族索引：docs/ 下的 COVERAGE.md 或自定義 families.json。"""
    fx = ip_root / "families.json"
    if fx.exists():
        return json.loads(fx.read_text())
    return {}


# ---------- 單任務執行 ----------

def _task_sources(ip_root: Path, proto: str, makefile_text: str) -> list[str]:
    """此協議 lint/sim 所需的最小檔案集：top + tb + Makefile DEPS。"""
    files = [f"rtl/{proto}_top.sv", f"tb/{proto}_tb.sv"]
    m = re.search(r"^DEPS\s*:?=\s*(.*)$", makefile_text, re.MULTILINE)
    if m:
        files += m.group(1).split()
    return [f for f in files if (ip_root / f).exists()]


def _write_files_f(workdir: Path, sources: list[str]) -> None:
    """VerifierGate 的 verilator lint 需要 files.f；+incdir 供 *.svh include。"""
    lines = ["+incdir+rtl", "+incdir+tb"] + sources
    (workdir / "files.f").write_text("\n".join(lines) + "\n")


def run_task(gate: VerifierGate, ip_root: Path, task: dict,
             mutant: dict | None = None) -> dict:
    proto = task["proto"]
    workdir = Path(tempfile.mkdtemp(prefix=f"hv_{proto}_"))

    try:
        # 複製必要子集（rtl/tb/sim/syn + golden + Makefile），避免污染原 repo
        # golden/ 供 txdiff 語意判定（dynamis oracle 使用）
        for d in ["rtl", "tb", "sim", "syn", "golden"]:
            src = ip_root / d
            if src.exists():
                shutil.copytree(src, workdir / d)
        # VerifierGate 的模擬走 `make sim`，Makefile 必須叫 Makefile
        mk_text = task["makefile"].read_text()
        (workdir / "Makefile").write_text(mk_text)
        _write_files_f(workdir, _task_sources(ip_root, proto, mk_text))

        # mutant 注入：revert 已知修復 = 產生 labeled negative sample
        # 格式（mutants_gen.py 標準）：buggy_content = {repo相對路徑: 內容}
        mfiles = mutant.get("buggy_content", {}) if mutant else {}
        hits = bool(mutant) and ((proto in mutant.get("affected", [])) or
                                 any(proto.lower() in f.lower() for f in mfiles))
        if mutant and hits:
            for rel, buggy in mfiles.items():
                f = workdir / rel
                if f.exists():
                    f.write_text(buggy)

        # 以 Makefile 的 sim/syn target 為 ground truth 流程
        # patch_id 必須 deterministic：compat_check.py 靠它 join 兩輪 router 的記錄
        pid = f"{proto}:{mutant['id']}" if mutant else f"{proto}:clean"
        # 試點語料的 agent_conf 由 patch id 穩定雜湊產生（兩輪 router 看到相同
        # 特徵向量才能配對）。常數 conf 會讓 M1 分數沒有變異，compat 檢定退化。
        agent_conf = 0.2 + 0.75 * (zlib.crc32(pid.encode()) / 2**32)
        patch = Patch(task_type="corpus_regression",
                      diff=f"+mutant:{mutant['id']}" if mutant else "+clean",
                      files=[f"rtl/{proto}_top.sv"],
                      agent_conf=agent_conf,
                      patch_id=pid)
        rec = gate.verify(patch, workdir, top=f"{proto}_tb",
                          dut_top=f"{proto}_top")

        return {"proto": proto, "mutant": mutant["id"] if mutant else None,
                "patch": pid,
                "route": rec.route, "sim": rec.sim_result.value,
                "formal": rec.formal_result.value, "merged": rec.merged,
                "helios_conf": rec.helios_conf}
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


# ---------- 主流程 ----------

def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ip-root", required=True, type=Path)
    ap.add_argument("--proto", default="", help="逗號分隔的協議白名單")
    ap.add_argument("--family", default="", help="只跑某 shared-core 家族")
    ap.add_argument("--mutants", default="", help="mutants.json：注入已知 bug")
    ap.add_argument("--oracle", default="", help="dynamis repo 路徑：啟用 txdiff 語意判定")
    ap.add_argument("--router", default="rule", choices=["rule", "helios"],
                    help="rule=啟發式（M1） / helios=HeliosLM scorer（M2）")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--out", default="corpus_report.json",
                    help="每任務 route/sim/merged 摘要 JSON")
    ap.add_argument("--cal-log", default="",
                    help="compat_check.py 用的 JSONL（預設 cal_<router>.jsonl）")
    args = ap.parse_args()

    tools = check_tools()
    if not all([tools["iverilog"], tools["yosys"], tools["make"]]):
        print(f"FATAL: missing tools {tools}", file=sys.stderr); sys.exit(2)

    tasks = discover_tasks(args.ip_root)
    families = load_family_index(args.ip_root)

    if args.proto:
        keep = set(args.proto.split(","))
        tasks = [t for t in tasks if t["proto"] in keep]
    if args.family and args.family in families:
        keep = set(families[args.family]["variants"])
        tasks = [t for t in tasks if t["proto"] in keep]

    mutants = []
    if args.mutants:
        mutants = json.loads(Path(args.mutants).read_text()).get("mutants", [])

    oracle = None
    if args.oracle:
        from tx_adapter import TxOracle
        oracle = TxOracle(Path(args.oracle))
        print(f"tx oracle: {'enabled' if oracle.enabled else 'FALLBACK (txdiff not found)'}")

    from helios_router import make_router
    gate = VerifierGate(make_router(args.router, str(Path("tau_config.json"))),
                        oracle=oracle)
    results, t0 = [], time.time()

    # 乾淨回歸
    for t in tasks:
        r = run_task(gate, args.ip_root, t)
        results.append(r)
        print(f"[clean ] {r['proto']:<28} sim={r['sim']:<7} merged={r['merged']}")

    # mutant 注入：每個 mutant 影響的家族各跑一遍 → 負樣本
    for m in mutants:
        for t in tasks:
            if t["proto"] in m.get("affected", []):
                r = run_task(gate, args.ip_root, t, mutant=m)
                results.append(r)
                print(f"[mutant] {r['proto']:<26} {m['id']:<10} sim={r['sim']:<7} "
                      f"merged={r['merged']}  (expect merged=False)")

    n = len(results)
    n_fail = sum(1 for r in results if r["sim"] != "pass")
    print(f"\n== {n} tasks in {time.time()-t0:.1f}s | sim-fail: {n_fail} "
          f"| records -> calibration_log/ (next: tau_calibrate.py)")

    Path(args.out).write_text(json.dumps(results, indent=2))

    # compat_check.py 配對輸入：patch（deterministic）/ conf / label（pipeline 判定，
    # 與 router 決策無關）。同一 corpus 分別以 rule / helios 跑兩輪即可 join。
    cal_log = Path(args.cal_log or f"cal_{args.router}.jsonl")
    with open(cal_log, "w") as f:
        for r in results:
            f.write(json.dumps({
                "patch": r["patch"], "conf": r["helios_conf"],
                "label": bool(r["merged"]),
                "route": r["route"], "sim": r["sim"], "formal": r["formal"],
            }) + "\n")
    print(f"compat log -> {cal_log}  (compat_check.py --field conf)")


if __name__ == "__main__":
    main()
