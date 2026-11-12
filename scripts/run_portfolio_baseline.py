"""Run all protocol Makefiles in isolated Linux workdirs, retaining per-step logs.

Usage: python run_portfolio_baseline.py --ip-root PATH --output PATH --minisim PATH
Runs the upstream sim/syn/apb targets. Minisim is a separate lowering-only probe;
an unsupported frontend is never recorded as an RTL simulation pass.
"""
import argparse
import hashlib
import fcntl
from collections import Counter
from collections import deque
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time


MEMORY_MB = 0


def command(args, cwd, logfile, timeout):
    if MEMORY_MB:
        args = ["prlimit", f"--as={MEMORY_MB * 1024 * 1024}", "--"] + args
    start = time.monotonic()
    with logfile.open("w") as output:
        process = subprocess.Popen(args, cwd=cwd, stdout=output, stderr=subprocess.STDOUT,
                                   start_new_session=True)
        timed_out = False
        try:
            code = process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            try: os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError: pass
            code = process.wait()
    first, last = [], deque(maxlen=40)
    failed = passed = limited = False
    with logfile.open(errors="replace") as source:
        for line in source:
            if len(first) < 40: first.append(line)
            last.append(line)
            failed |= bool(re.search(r"\bTEST\s+FAIL(?:ED)?\b|\bSVA_FAIL\b|ERROR:|FATAL:", line))
            passed |= bool(re.search(r"\bTEST\s+PASS(?:ED)?\b", line))
            limited |= bool(re.search(r"bad_alloc|Cannot allocate memory|MemoryError|out of memory", line, re.I))
    text = "".join(first) + "\n...\n" + "".join(last)
    return {"returncode": code, "timeout": timed_out,
            "resource_limited": limited, "failure_marker": failed, "pass_marker": passed,
            "seconds": round(time.monotonic() - start, 3), "log": str(logfile)}, text


def main():
    global MEMORY_MB
    parser = argparse.ArgumentParser()
    parser.add_argument("--ip-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--minisim", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--memory-mb", type=int, default=1536)
    args = parser.parse_args()
    root, out, minisim = args.ip_root.resolve(), args.output.resolve(), args.minisim.resolve()
    out.mkdir(parents=True, exist_ok=True)
    lock = open(Path(tempfile.gettempdir()) / ("helios-portfolio-" +
                hashlib.sha256(str(out).encode()).hexdigest()[:16] + ".lock"), "w")
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    (out / "controller.json").write_text(json.dumps({"pid": os.getpid(), "workers": args.workers,
            "memory_mb": args.memory_mb, "output": str(out)}, indent=2))
    MEMORY_MB = args.memory_mb
    if MEMORY_MB and not shutil.which("prlimit"):
        raise RuntimeError("prlimit is required for the configured process memory limit")
    manifest = {"repo": "tonythetiger168/open-ip-portfolio",
                "head": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip(),
                "tools": {}, "results": []}
    previous = out / "progress.json"
    if previous.exists():
        if not args.resume: raise RuntimeError("Existing progress found; use --resume")
        old = json.loads(previous.read_text())
        if old["head"] != manifest["head"]: raise RuntimeError("Cannot resume a different repo revision")
        manifest = old
    manifest.setdefault("execution_segments", []).append({"workers": args.workers,
        "process_memory_mb": MEMORY_MB, "timeout_seconds": args.timeout,
        "previously_completed": len(manifest["results"]),
        "minisim_frontend_sha256": hashlib.sha256((minisim / "tools/pyslang_lower.py").read_bytes()).hexdigest()})
    for tool in ("iverilog", "yosys", "verilator", "make"):
        probe = subprocess.run([tool, "-V" if tool == "iverilog" else "--version"],
                               capture_output=True, text=True)
        manifest["tools"][tool] = (probe.stdout + probe.stderr).splitlines()[0]
    cache = Path.home() / ".cache/ai-eda-runs"
    cache.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="helios_portfolio_", dir=cache) as folder:
        base = Path(folder)
        snapshot = base / "snapshot"
        shutil.copytree(root, snapshot, ignore=shutil.ignore_patterns(
            ".git", "work", "releases", "docs", "__pycache__", "simv*", "*.vcd"))
        makefiles = sorted(snapshot.glob("Makefile.*"))
        manifest["protocols"] = len(makefiles)
        completed = {r["protocol"] for r in manifest["results"]}
        makefiles = [mk for mk in makefiles if mk.name[len("Makefile."):] not in completed]
        print(f"RUN {len(makefiles)} IPs at {manifest['head']} with {args.workers} workers", flush=True)

        def one(mk):
            proto = mk.name[len("Makefile."):]
            work = base / proto
            work.mkdir()
            for entry in snapshot.iterdir():
                (work / entry.name).symlink_to(entry, target_is_directory=entry.is_dir())
            logs = out / "logs" / proto
            logs.mkdir(parents=True, exist_ok=True)
            result = {"protocol": proto, "steps": {}}
            for target in ("sim", "syn", "apb"):
                info, text = command(["make", "-f", mk.name, target], work,
                                     logs / f"{target}.log", args.timeout)
                passed = info["returncode"] == 0 and not info["failure_marker"]
                if target != "syn":
                    passed = passed and info["pass_marker"]
                info["status"] = ("timeout" if info["timeout"] else "resource_limited" if info["resource_limited"]
                                  else "pass" if passed else "process_killed" if info["returncode"] < 0 else "fail")
                result["steps"][target] = info
            module = re.search(r"^S\s*:?=\s*(\S+)", mk.read_text(), re.M)
            if module:
                source = snapshot / "rtl" / (module.group(1) + "_top.sv")
                info, text = command([sys.executable, str(minisim / "tools/pyslang_lower.py"),
                                      str(source), "--output", str(work / "native-model")],
                                     work, logs / "minisim.log", min(args.timeout, 60))
                info["status"] = ("timeout" if info["timeout"] else
                                  "lowered_not_simulated" if info["returncode"] == 0 else
                                  "unsupported" if "UNSUPPORTED:" in text else "error")
                info["detail"] = text.strip()[-1000:]
                result["steps"]["minisim"] = info
            if work.resolve().is_relative_to(base.resolve()) and work != snapshot:
                shutil.rmtree(work)
            return result

        with ThreadPoolExecutor(max_workers=args.workers) as executor:
            pending = {executor.submit(one, mk): mk for mk in makefiles}
            for future in as_completed(pending):
                result = future.result()
                manifest["results"].append(result)
                print(f"[{len(manifest['results'])}/{manifest['protocols']}] {result['protocol']} " +
                      " ".join(f"{t}={r['status']}" for t, r in result["steps"].items()), flush=True)
                (out / "progress.json").write_text(json.dumps(manifest, indent=2))
    manifest["results"].sort(key=lambda r: r["protocol"])
    manifest["summary"] = {target: dict(Counter(r["steps"][target]["status"]
                              for r in manifest["results"] if target in r["steps"]))
                           for target in ("sim", "syn", "apb", "minisim")}
    (out / "report.json").write_text(json.dumps(manifest, indent=2))
    print(json.dumps(manifest["summary"], indent=2), flush=True)


if __name__ == "__main__":
    main()
