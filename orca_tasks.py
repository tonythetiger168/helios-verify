"""
orca_tasks.py — ORCA v6.3 macro-task 產生與執行器 (helios-verify)

把 orca-v63 這種 49 模組的真實 SoC 註冊成 corpus 的 macro-task：
  1. discover: 掃 rtl/ 模組與 tb/，建立「模組 → 影響它的 TB 集合」對應
     （= VerifyDepth.regression_subset 的實戰資料來源）
  2. tasks:    每個 rtl/ 子目錄一個 mutant 注入點 → 一組 task
  3. run:      複製 repo 到 temp、注入 mutant、make sim、FeedbackRecord
     回報給 calibration_log（與 corpus_runner 產出的資料格式相同）

用法：
  # 先只產生 task 清單（不跑），檢查 subset 對應是否合理
  python orca_tasks.py --orca-root ../orca-v63 --list

  # 全模組乾淨回歸（無 mutant）
  python orca_tasks.py --orca-root ../orca-v63

  # 注入 mutants.json 產生負樣本（大型設計：建議分批，見 --limit）
  python orca_tasks.py --orca-root ../orca-v63 --mutants orca_mutants.json --limit 5

  # 只跑 cpu/ 域＋動態 oracle
  python orca_tasks.py --orca-root ../orca-v63 --domain cpu --oracle ../dynamis
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass, field, asdict
from pathlib import Path

from verifier_gate import VerifierGate, RuleRouter, Patch, SimResult, FormalResult

DOMAIN_DIRS = ["common", "cpu", "ai", "noc", "pad", "soc", "accelerators"]


@dataclass
class OrcaTask:
    task_id: str
    module: str            # rtl/ 下的模組檔（不含副檔名）
    domain: str            # cpu / ai / noc / soc / ...
    subset: list           # 受影響的 TB 名稱（regression_subset 的值）
    mutate_target: str     # mutant 注入的檔案路徑（相對 orca-root）


# ---------- 1. 發現模組與 TB ----------

def discover_modules(orca_root: Path) -> list[dict]:
    mods = []
    rtl = orca_root / "rtl"
    for dom in DOMAIN_DIRS:
        d = rtl / dom
        if not d.exists():
            continue
        for sv in sorted(d.rglob("*.sv")) + sorted(d.rglob("*.v")):
            mods.append({"module": sv.stem, "domain": dom,
                         "relpath": str(sv.relative_to(orca_root))})
    return mods


def discover_tbs(orca_root: Path) -> dict:
    """tb/ 下每個 TB 解析它實例化了哪些模組 → {tb_name: [module,...]}"""
    tbs = {}
    tbdir = orca_root / "tb"
    if not tbdir.exists():
        return tbs
    all_mods = [m["module"] for m in discover_modules(orca_root)]
    pat = re.compile(r"^\s*(" + "|".join(re.escape(m) for m in all_mods) +
                     r")\s+(\w+)\s*\(", re.MULTILINE)
    for tb in sorted(tbdir.rglob("*.sv")) + sorted(tbdir.rglob("*.v")):
        text = tb.read_text()
        tbs[tb.stem] = sorted(set(pat.findall(text)))
    return tbs


def build_subset_map(mods: list, tbs: dict) -> dict:
    """模組 → [觸及它的 TB]。沒 TB 觸及的模組 fallback 到所有 TB。"""
    smap = {}
    all_tb = list(tbs.keys())
    for m in mods:
        touched = [tb for tb, ms in tbs.items() if m["module"] in ms]
        smap[m["module"]] = touched if touched else all_tb
    return smap


# ---------- 2. 產生 tasks ----------

def gen_tasks(orca_root: Path, domain: str = "") -> list[OrcaTask]:
    mods = discover_modules(orca_root)
    tbs = discover_tbs(orca_root)
    smap = build_subset_map(mods, tbs)
    tasks = []
    for m in mods:
        if domain and m["domain"] != domain:
            continue
        tasks.append(OrcaTask(
            task_id=f"orca_{m['module']}",
            module=m["module"], domain=m["domain"],
            subset=smap[m["module"]],
            mutate_target=m["relpath"]))
    return tasks


# ---------- 3. 執行 ----------

def run_orca_task(gate: VerifierGate, orca_root: Path, task: OrcaTask,
                  mutant: dict | None, keep: bool = False) -> dict:
    """把整個 orca repo 複製到 temp（大型 repo：可用 --inplace 跳過複製）。"""
    workdir = Path("/tmp") / f"orca_{task.module}_{uuid.uuid4().hex[:6]}"
    shutil.copytree(orca_root, workdir,
                    ignore=shutil.ignore_patterns(".git", "obj_dir", "*.vcd"))

    try:
        if mutant:
            # 支援兩種格式：dict {路徑: 內容}（mutants_gen 標準）或舊式字串
            content = mutant.get("buggy_content")
            buggy = content.get(task.mutate_target) if isinstance(content, dict) \
                    else content
            f = workdir / task.mutate_target
            if buggy and f.exists():
                f.write_text(buggy)

        # 模擬由 gate.verify() 內部的 verilator_sim（make sim）執行，這裡不重複跑
        patch = Patch(task_type="orca_mutant" if mutant else "orca_regression",
                      diff=mutant["id"] if mutant else "+clean",
                      files=[task.mutate_target], agent_conf=0.55)
        rec = gate.verify(patch, workdir, top="tb_top")

        return {"task": task.task_id, "domain": task.domain,
                "subset_size": len(task.subset),
                "mutant": mutant["id"] if mutant else None,
                "route": rec.route, "sim": rec.sim_result.value,
                "merged": rec.merged, "helios_conf": rec.helios_conf}
    finally:
        if not keep:
            shutil.rmtree(workdir, ignore_errors=True)


# ---------- main ----------

def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--orca-root", required=True, type=Path)
    ap.add_argument("--domain", default="", help="只跑某域：cpu/ai/noc/soc/...")
    ap.add_argument("--mutants", default="", help="orca_mutants.json")
    ap.add_argument("--limit", type=int, default=0, help="只跑前 N 個 task（試點用）")
    ap.add_argument("--oracle", default="")
    ap.add_argument("--router", default="rule", choices=["rule", "helios"],
                    help="rule=啟發式（M1） / helios=HeliosLM scorer（M2）")
    ap.add_argument("--inplace", action="store_true",
                    help="不複製 repo，直接在 orca-root 跑（快但有污染風險）")
    ap.add_argument("--list", action="store_true", help="只印 task 清單不執行")
    args = ap.parse_args()

    if not (args.orca_root / "Makefile").exists():
        sys.exit(f"FATAL: {args.orca_root} 沒有 Makefile")

    tasks = gen_tasks(args.orca_root, args.domain)
    if args.list:
        for t in tasks:
            print(f"{t.domain:<14} {t.module:<24} subset[{len(t.subset)}]: "
                  f"{', '.join(t.subset[:3])}{'...' if len(t.subset) > 3 else ''}")
        print(f"\n共 {len(tasks)} 個 task；沒 TB 觸及的模組 subset=全部（fallback）")
        return

    mutants = (json.loads(Path(args.mutants).read_text()).get("mutants", [])
               if args.mutants else [])
    if args.limit:
        tasks = tasks[:args.limit]

    oracle = None
    if args.oracle:
        from tx_adapter import TxOracle
        oracle = TxOracle(Path(args.oracle))
        print(f"tx oracle: {'enabled' if oracle.enabled else 'FALLBACK'}")

    from helios_router import make_router
    gate = VerifierGate(make_router(args.router, str(Path("tau_config.json"))),
                        oracle=oracle)
    results, t0 = [], time.time()

    runs = [(t, None) for t in tasks]
    for m in mutants:
        affected = [t for t in tasks
                    if t.mutate_target in m.get("affected_files", [])]
        runs += [(t, m) for t in affected]

    for task, mut in runs:
        r = run_orca_task(gate, args.orca_root, task, mut,
                          keep=args.inplace)
        results.append(r)
        tag = f"[{r['mutant']}]" if r["mutant"] else "[clean]"
        print(f"{tag} {r['task']:<28} sim={r['sim']:<7} merged={r['merged']}")

    print(f"\n== {len(results)} runs in {time.time()-t0:.1f}s "
          f"-> calibration_log/ (format identical to corpus_runner)")
    Path("orca_report.json").write_text(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
