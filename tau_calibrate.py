"""
tau_calibrate.py — τ 校準 job v0.1 (helios-verify project)
 讀 calibration_log/ → 校準 confidence → 更新 tau_config.json（保證單調）
 輸出 tau_report.json（ECE / fail rate / calibration curve）+ tau_history.json（審計軌跡）

排程建議：每累積 100 筆回饋跑一次，或每週一次 cron。
"""

from __future__ import annotations

import json
import math
import time
from dataclasses import dataclass, asdict
from enum import Enum
from pathlib import Path

class Route(str, Enum):
    FAST_PATH = "fast_path"; DEEP_VERIFY = "deep_verify"; REJECT = "reject"

LOG_DIR = Path("calibration_log")
CFG_PATH = Path("tau_config.json")
HISTORY_PATH = Path("tau_history.json")
REPORT_PATH = Path("tau_report.json")

MIN_SAMPLES = 100
FAST_FAIL_THRESHOLD = 0.05
TAU_STEP = 0.02
MAX_ECE_TARGET = 0.10
BUCKETS = 10

@dataclass
class TauConfig:
    tau_fast: float = 0.7
    tau_reject: float = 0.15
    version: int = 1
    updated_at: float = 0.0

# ---------------- 資料載入 ----------------

def load_records() -> list:
    recs = []
    skipped = 0
    for f in sorted(LOG_DIR.glob("*.json")):
        try:
            recs.append(json.loads(f.read_text()))
        except json.JSONDecodeError:
            skipped += 1
    if skipped:
        print(f"warning: {skipped} corrupted log file(s) skipped")
    return recs

def record_outcome(r: dict):
    """1=merged(pass), 0=fail, None=不計入校準。"""
    sim = r.get("sim_result"); formal = r.get("formal_result")
    if sim in (None, "skipped"):
        return None
    if sim in ("fail", "timeout"):
        return 0
    if formal in ("cex_found", "unknown") and r.get("route") != "fast_path":
        return 0
    if r.get("merged"):
        return 1
    return 0

# ---------------- 校準指標 ----------------

def ece(records: list, n_buckets: int = BUCKETS) -> float:
    """ECE = Σ (|bucket|/N) * |acc - conf|"""
    pts = [(x["helios_conf"], y) for x in records
           if (y := record_outcome(x)) is not None and x.get("helios_conf", -1) >= 0]
    if not pts:
        return float("nan")
    total_n = len(pts)
    ece_sum = 0.0
    for b in range(n_buckets):
        items = [(c, y) for c, y in pts if min(int(c * n_buckets), n_buckets - 1) == b]
        if not items:
            continue
        acc = sum(y for _, y in items) / len(items)
        avg_conf = sum(c for c, _ in items) / len(items)
        ece_sum += (len(items) / total_n) * abs(acc - avg_conf)
    return ece_sum

def bucket_fail_rate(records: list, route: Route):
    sel = [record_outcome(r) for r in records if r.get("route") == route.value]
    sel = [x for x in sel if x is not None]
    return (1.0 - sum(sel) / len(sel)) if sel else None

def calibration_curve(records: list) -> list:
    """conf → 實測 accuracy；這就是 HeliosLM domain fine-tune 的標籤來源。"""
    pts = [(r["helios_conf"], record_outcome(r)) for r in records
           if r.get("helios_conf", -1) >= 0]
    pts = [(c, y) for c, y in pts if y is not None]
    curve = []
    for b in range(BUCKETS):
        items = [(c, y) for c, y in pts if int(c * BUCKETS) == b]
        if items:
            curve.append({"bucket": round(b / BUCKETS, 2),
                          "n": len(items),
                          "avg_conf": round(sum(c for c, _ in items) / len(items), 4),
                          "acc": round(sum(y for _, y in items) / len(items), 4)})
    return curve

# ---------------- 單調更新 ----------------

def update_tau(cfg: TauConfig, records: list) -> tuple:
    """嚴格單調：τ_fast 只升不降，τ_reject 只降不升。"""
    changes = []
    new = TauConfig(**asdict(cfg))

    fr = bucket_fail_rate(records, Route.FAST_PATH)
    if fr is not None and fr > FAST_FAIL_THRESHOLD:
        bump = math.ceil((fr - FAST_FAIL_THRESHOLD) / TAU_STEP) * TAU_STEP
        new.tau_fast = min(0.95, round(cfg.tau_fast + bump, 4))
        if new.tau_fast > cfg.tau_fast:
            changes.append(f"tau_fast {cfg.tau_fast} -> {new.tau_fast} "
                           f"(fast_path fail rate {fr:.3f} > {FAST_FAIL_THRESHOLD})")

    pts = [(r["helios_conf"], record_outcome(r)) for r in records
           if r.get("route") == Route.DEEP_VERIFY.value and r.get("helios_conf", -1) >= 0]
    pts = [(c, y) for c, y in pts if y is not None]
    if len(pts) >= 30:
        low_conf_pass = sorted(c for c, y in pts if y == 1)
        if low_conf_pass:
            p05 = low_conf_pass[min(int(len(low_conf_pass) * 0.05), len(low_conf_pass) - 1)]
            candidate = max(0.05, round(p05 - 0.05, 4))
            if candidate < cfg.tau_reject:
                new.tau_reject = candidate
                changes.append(f"tau_reject {cfg.tau_reject} -> {new.tau_reject} "
                               f"(deep_verify pass@low-conf boundary)")

    assert new.tau_fast > new.tau_reject, "invariant violated: tau_fast <= tau_reject"
    if changes:
        new.version = cfg.version + 1
        new.updated_at = time.time()
    return new, changes

# ---------------- 主流程 ----------------

def main() -> None:
    records = load_records()
    cfg = TauConfig(**json.loads(CFG_PATH.read_text())) if CFG_PATH.exists() else TauConfig()

    report = {
        "run_at": time.time(),
        "n_records": len(records),
        "sample_counts": {r.value: sum(1 for x in records if x.get("route") == r.value)
                          for r in Route},
        "fail_rates": {r.value: bucket_fail_rate(records, r) for r in Route},
        "ece": ece(records),
        "ece_target": MAX_ECE_TARGET,
        "calibration_curve": calibration_curve(records),
    }

    if len(records) >= MIN_SAMPLES:
        new_cfg, changes = update_tau(cfg, records)
        report["tau_update"] = {"applied": bool(changes), "changes": changes,
                                "old": asdict(cfg), "new": asdict(new_cfg)}
        if changes:
            hist = json.loads(HISTORY_PATH.read_text()) if HISTORY_PATH.exists() else []
            hist.append({"ts": time.time(), "from": asdict(cfg), "to": asdict(new_cfg),
                         "reason": changes, "n_records": len(records)})
            HISTORY_PATH.write_text(json.dumps(hist, indent=2))
            CFG_PATH.write_text(json.dumps(asdict(new_cfg), indent=2))
    else:
        report["tau_update"] = {"applied": False,
                                "reason": f"insufficient samples ({len(records)} < {MIN_SAMPLES})"}

    REPORT_PATH.write_text(json.dumps(report, indent=2, default=str))
    print(json.dumps(report["tau_update"], indent=2))

if __name__ == "__main__":
    main()
