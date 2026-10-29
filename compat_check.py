#!/usr/bin/env python3
"""M1->M2 Router calibration compatibility check (Phase 1+2).

Reads two calibration logs produced by corpus_runner.py:
  --rule-log   calibration_log produced with --router rule   (M1 scores)
  --helios-log calibration_log produced with --router helios (M2 scores)

Both logs must contain records for the SAME patches. Records are joined
on the patch identifier. The verdict label (pass/fail) must come from the
deterministic VerifierGate pipeline, NOT from the router decision, so that
labels are router-independent.

Usage:
  python compat_check.py --rule-log cal_rule.jsonl --helios-log cal_helios.jsonl \
      --tau-fast 0.7 --tau-reject 0.15 [--field conf]

Exit code: 0 = compatible (rho >= 0.8 and all acceptance gates pass)
           1 = incompatible or insufficient data
"""

import argparse
import json
import math
import sys
from bisect import bisect_left

try:
    from scipy import stats
except ImportError:
    stats = None


# ---------------------------------------------------------------- records

def load_log(path):
    """Load calibration log; supports JSONL (one object per line) or a JSON
    list. Auto-detects common field names for patch id / confidence / label."""
    records = []
    with open(path) as f:
        text = f.read().strip()
    if not text:
        return records
    if text.startswith("["):
        items = json.loads(text)
    else:
        items = [json.loads(line) for line in text.splitlines() if line.strip()]
    for it in items:
        records.append(it)
    return records


def pick(d, candidates):
    for c in candidates:
        if c in d and d[c] is not None:
            return d[c]
    return None


def extract(records, conf_field=None):
    """Return list of dicts: {patch, conf, label}."""
    out = []
    for i, r in enumerate(records):
        patch = pick(r, ["patch", "patch_id", "mutant", "id", "name", "target"])
        conf = pick(r, [conf_field] if conf_field else [])
        if conf is None:
            conf = pick(r, ["conf", "confidence", "score", "conf_m1", "conf_m2"])
        label_raw = pick(r, ["label", "verdict", "pass", "passed", "ok", "result"])
        if patch is None:
            patch = f"__row{i}"
        if isinstance(label_raw, bool):
            label = 1 if label_raw else 0
        elif isinstance(label_raw, (int, float)):
            label = int(label_raw)
        elif isinstance(label_raw, str):
            label = 1 if label_raw.lower() in ("pass", "passed", "ok", "true", "1") else 0
        else:
            label = None
        if conf is None:
            print(f"[warn] record {i}: no confidence field, skipped", file=sys.stderr)
            continue
        out.append({"patch": str(patch), "conf": float(conf), "label": label})
    return out


# ---------------------------------------------------------------- bootstrap

def bootstrap_ci_rho(x, y, n_boot=1000, seed=42):
    """Percentile bootstrap CI for Spearman rho."""
    import random
    random.seed(seed)
    n = len(x)
    if stats is None or n < 10:
        return None, None
    rhos = []
    for _ in range(n_boot):
        idx = [random.randrange(n) for _ in range(n)]
        if len(set(idx)) < 5:
            continue
        bx = [x[i] for i in idx]
        by = [y[i] for i in idx]
        if len(set(bx)) < 3 or len(set(by)) < 3:
            continue
        r = stats.spearmanr(bx, by).statistic
        rhos.append(r)
    if not rhos:
        return None, None
    rhos.sort()
    lo = rhos[int(0.025 * len(rhos))]
    hi = rhos[int(0.975 * len(rhos)) - 1]
    return lo, hi


# ---------------------------------------------------------------- ECE

def ece(conf, label, n_bins=10):
    """Expected calibration error; label in {0,1} (1 = pass)."""
    pairs = sorted(zip(conf, label))
    n = len(pairs)
    if n == 0:
        return None
    e = 0.0
    per_bin = []
    for b in range(n_bins):
        lo, hi = b / n_bins, (b + 1) / n_bins
        seg = [(c, l) for c, l in pairs if (lo <= c < hi) or (b == n_bins - 1 and c == 1.0)]
        if not seg:
            per_bin.append((lo, hi, 0, None, None))
            continue
        avg_conf = sum(c for c, _ in seg) / len(seg)
        acc = sum(l for _, l in seg) / len(seg)
        e += (len(seg) / n) * abs(avg_conf - acc)
        per_bin.append((lo, hi, len(seg), avg_conf, acc))
    return e, per_bin


# ---------------------------------------------------------------- mapping

def quantile_mapping(tau, src_confs, dst_confs):
    """Map threshold tau from src score space to dst score space via
    empirical CDFs: find the src-quantile of tau, then the dst value at
    the same quantile."""
    s = sorted(src_confs)
    d = sorted(dst_confs)
    # fraction of src scores strictly below tau
    q = bisect_left(s, tau) / len(s)
    idx = min(len(d) - 1, int(q * len(d)))
    return d[idx]


def isotonic_map_fit(src, dst):
    """Fit monotone mapping src -> dst with isotonic regression (PAVA).
    Returns a callable. Uses squared error; ties handled by averaging."""
    order = sorted(range(len(src)), key=lambda i: src[i])
    xs = [src[i] for i in order]
    ys = [dst[i] for i in order]
    # pool adjacent violators; blocks: [x_first, x_last, level, weight]
    blocks = []
    for x, y in zip(xs, ys):
        blocks.append([x, x, y, 1])
        while len(blocks) >= 2 and blocks[-2][2] > blocks[-1][2]:
            b1, b2 = blocks[-2], blocks[-1]
            w = b1[3] + b2[3]
            lv = (b1[2] * b1[3] + b2[2] * b2[3]) / w
            blocks[-2:] = [[b1[0], b2[1], lv, w]]
    def f(v):
        if v <= blocks[0][0]:
            return blocks[0][2]
        if v >= blocks[-1][1]:
            return blocks[-1][2]
        for i in range(1, len(blocks)):
            if v <= blocks[i][1]:
                # linear interpolation between adjacent fitted levels
                x0, x1 = blocks[i - 1][1], blocks[i][0]
                y0, y1 = blocks[i - 1][2], blocks[i][2]
                if x1 == x0:
                    return y1
                return y0 + (y1 - y0) * (v - x0) / (x1 - x0)
        return blocks[-1][2]
    return f


# ---------------------------------------------------------------- decisions

def route(conf, tau_fast, tau_reject):
    if conf >= tau_fast:
        return "fast"
    if conf < tau_reject:
        return "reject"
    return "deep"


def agreement(d1, d2):
    n = len(d1)
    if n == 0:
        return None, 0
    same = sum(1 for a, b in zip(d1, d2) if a == b)
    return same / n, n


def mcnemar(d1, d2):
    """McNemar test (no continuity correction) on decision disagreements."""
    b = c = 0
    for a, bb in zip(d1, d2):
        if a != bb:
            if a == "fast":
                b += 1
            else:
                c += 1
    if b + c == 0:
        return 1.0
    chi2 = (b - c) ** 2 / (b + c)
    if stats is not None:
        p = 1 - stats.chi2.cdf(chi2, df=1)
    else:
        p = math.erfc(math.sqrt(chi2 / 2))
    return p


# ---------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--rule-log", required=True)
    ap.add_argument("--helios-log", required=True)
    ap.add_argument("--tau-fast", type=float, default=0.7)
    ap.add_argument("--tau-reject", type=float, default=0.15)
    ap.add_argument("--field", default=None,
                    help="confidence field name if logs use non-default names")
    ap.add_argument("--min-n", type=int, default=60)
    args = ap.parse_args()

    rule = extract(load_log(args.rule_log), args.field)
    hel = extract(load_log(args.helios_log), args.field)
    hmap = {r["patch"]: r for r in hel}

    paired = []
    for r in rule:
        h = hmap.get(r["patch"])
        if h is not None and r["label"] is not None and h["label"] is not None \
                and r["label"] == h["label"]:
            paired.append((r["conf"], h["conf"], r["label"], r["patch"]))

    print(f"rule records: {len(rule)}, helios records: {len(hel)}, paired: {len(paired)}")
    if len(paired) < args.min_n:
        print(f"[FAIL] paired samples {len(paired)} < --min-n {args.min_n}; "
              "collect more mutants before judging compatibility.")
        sys.exit(1)

    x = [p[0] for p in paired]   # M1 conf
    y = [p[1] for p in paired]   # M2 conf
    labels = [p[2] for p in paired]

    n_fail = sum(1 for l in labels if l == 0)
    n_pass = sum(1 for l in labels if l == 1)
    print(f"labels: {n_pass} pass, {n_fail} fail "
          f"(fail fraction {n_fail/len(labels):.2%})")
    if n_fail < 20:
        print("[warn] fewer than 20 fail cases; tau_reject conclusions are weak")

    # ---- Phase 1: rank correlation -----------------------------------
    if stats is not None:
        rho_res = stats.spearmanr(x, y)
        rho = rho_res.statistic
        pval = rho_res.pvalue
    else:
        # fall back to Pearson on ranks
        rx = [sorted(x).index(v) for v in x]
        ry = [sorted(y).index(v) for v in y]
        rho = pearson(rx, ry)
        pval = None
    lo, hi = bootstrap_ci_rho(x, y)
    print(f"\n=== Phase 1: rank correlation ===")
    print(f"Spearman rho = {rho:.3f} (p={pval:.2g})" if pval is not None
          else f"Spearman rho = {rho:.3f}")
    if lo is not None:
        print(f"bootstrap 95% CI: [{lo:.3f}, {hi:.3f}]")

    # per-region rho (split by M1 conf: fail region / gray / pass region)
    regions = [("reject-region", lambda c: c < args.tau_reject),
               ("gray-region", lambda c: args.tau_reject <= c < args.tau_fast),
               ("fast-region", lambda c: c >= args.tau_fast)]
    print("per-region rho:")
    for name, sel in regions:
        xs = [a for a, b in zip(x, y) if sel(a)]
        ys = [b for a, b in zip(x, y) if sel(a)]
        if len(xs) >= 8 and stats is not None and len(set(xs)) > 2:
            r = stats.spearmanr(xs, ys).statistic
            print(f"  {name}: n={len(xs)}, rho={r:.3f}")
        else:
            print(f"  {name}: n={len(xs)}, rho=n/a (too few samples)")

    if rho >= 0.8:
        verdict1 = "PASS"
    elif rho >= 0.5:
        verdict1 = "GRAY"
    else:
        verdict1 = "FAIL"
    print(f"Phase 1 verdict: {verdict1} (rho{' CI-lower' if lo is not None else ''} "
          f"{'%.2f' % (lo if lo is not None else rho)})")

    # top-10 divergent patches
    div = sorted(paired, key=lambda p: abs(p[0] - p[1]), reverse=True)[:10]
    print("\ntop-10 divergent patches (M1_conf, M2_conf, label):")
    for a, b, l, name in div:
        print(f"  {name}: {a:.3f} vs {b:.3f} label={l}")

    # ---- Phase 2: threshold mapping -----------------------------------
    print("\n=== Phase 2: threshold transfer ===")
    tau_f_q = quantile_mapping(args.tau_fast, x, y)
    tau_r_q = quantile_mapping(args.tau_reject, x, y)
    iso = isotonic_map_fit(x, y)
    tau_f_i = iso(args.tau_fast)
    tau_r_i = iso(args.tau_reject)
    print(f"quantile mapping: tau_fast {args.tau_fast} -> {tau_f_q:.3f}, "
          f"tau_reject {args.tau_reject} -> {tau_r_q:.3f}")
    print(f"isotonic mapping: tau_fast {args.tau_fast} -> {tau_f_i:.3f}, "
          f"tau_reject {args.tau_reject} -> {tau_r_i:.3f}")

    # ECE comparison
    ece_m1, bins_m1 = ece(x, labels)
    ece_m2_raw, _ = ece(y, labels)
    print(f"\nECE M1 (rule scores)   = {ece_m1:.4f}")
    print(f"ECE M2 (helios scores) = {ece_m2_raw:.4f}  (raw, own calibration)")

    # decision agreement under quantile-mapped thresholds
    d_m1 = [route(c, args.tau_fast, args.tau_reject) for c in x]
    d_m2_mapped = [route(c, tau_f_q, tau_r_q) for c in y]
    d_m2_iso = [route(c, tau_f_i, tau_r_i) for c in y]

    for tag, d2 in [("quantile", d_m2_mapped), ("isotonic", d_m2_iso)]:
        print(f"\ndecision agreement (M1@orig vs M2@{tag}-tau):")
        for region in ("fast", "deep", "reject"):
            sel = [i for i, d in enumerate(d_m1) if d == region]
            if sel:
                a = sum(1 for i in sel if d2[i] == region) / len(sel)
                print(f"  {region}-region: {a:.2%} (n={len(sel)})")
        ov, n = agreement(d_m1, d2)
        print(f"  overall: {ov:.2%}")
        p = mcnemar(d_m1, d2)
        print(f"  McNemar p = {p:.4f} {'(n.s.)' if p > 0.05 else '(significant!)'}")

    # mapped-threshold calibration quality on M2 scores
    ece_m2_map, bins_m2 = ece(y, labels)
    print("\nM2 calibration buckets (isotonic tau):")
    print("  bin-range        n   avg_conf    acc")
    for lo_b, hi_b, n_b, ac, acc in bins_m2:
        if n_b:
            flag = ""
            if ac is not None and abs(ac - acc) >= 0.1:
                flag = "  <-- |conf-acc| >= 0.1"
            print(f"  [{lo_b:.1f},{hi_b:.1f}) {n_b:5d}   "
                  f"{ac:.3f}    {acc:.3f}{flag}")

    # ---- final gate ----------------------------------------------------
    print("\n=== Final gate ===")
    gates = []
    gates.append(("rho >= 0.8", rho >= 0.8))
    gates.append(("ECE(M2) <= ECE(M1)+0.03", ece_m2_raw <= ece_m1 + 0.03))
    fast_idx = [i for i, d in enumerate(d_m1) if d == "fast"]
    fast_agr = (sum(1 for i in fast_idx if d_m2_iso[i] == "fast") / len(fast_idx)
                if fast_idx else 1.0)
    gates.append((f"fast agreement >= 95% ({fast_agr:.2%})", fast_agr >= 0.95))
    rej_idx = [i for i, d in enumerate(d_m1) if d == "reject"]
    rej_agr = (sum(1 for i in rej_idx if d_m2_iso[i] == "reject") / len(rej_idx)
               if rej_idx else 1.0)
    gates.append((f"reject agreement >= 90% ({rej_agr:.2%})", rej_agr >= 0.90))
    bad_bins = sum(1 for _, _, n_b, ac, acc in bins_m2
                   if n_b and ac is not None and abs(ac - acc) >= 0.1)
    gates.append((f"bad buckets (<0.1 gap): {bad_bins} == 0", bad_bins == 0))

    all_pass = all(g for _, g in gates)
    for desc, ok in gates:
        print(f"  [{'PASS' if ok else 'FAIL'}] {desc}")

    if all_pass and verdict1 == "PASS":
        print("\nRESULT: COMPATIBLE — mapped thresholds usable; "
              "still prefer >=300 M2-only shadow samples before go-live")
        sys.exit(0)
    else:
        print("\nRESULT: NOT COMPATIBLE or insufficient — "
              "use M2 shadow mode (Phase 3), do not transfer tau from M1 data")
        sys.exit(1)


def pearson(a, b):
    n = len(a)
    ma = sum(a) / n
    mb = sum(b) / n
    cov = sum((x - ma) * (y - mb) for x, y in zip(a, b))
    va = math.sqrt(sum((x - ma) ** 2 for x in a))
    vb = math.sqrt(sum((y - mb) ** 2 for y in b))
    return cov / (va * vb) if va * vb else 0.0


if __name__ == "__main__":
    main()
