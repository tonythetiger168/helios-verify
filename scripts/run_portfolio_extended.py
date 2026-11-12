"""Container-only upstream portfolio regression; source mounts stay read-only.

Uses /input/ip, /input/vip, /results and private /work snapshots.
All discovered VIPs/checkers are attempted; no-test/skip/timeout are not passes.
"""
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
import gzip
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import time
import zipfile

OUT = Path("/results")
IP = Path("/work/open-ip-portfolio")
VIP = Path("/work/open-vip-portfolio")
STATE = {"status": "running", "phases": {}, "results": {}}


def save():
    temporary = OUT / "progress.tmp"
    temporary.write_text(json.dumps(STATE, indent=2))
    temporary.replace(OUT / "progress.json")


def archive_previous_log(log):
    """Preserve interrupted/failed evidence before a resumed command truncates it."""
    if not log.exists():
        return
    index = 1
    while True:
        previous = log.with_name(f"{log.name}.previous-{index}")
        try:
            with previous.open("xb") as dst, log.open("rb") as src:
                shutil.copyfileobj(src, dst)
            return
        except FileExistsError:
            index += 1


def run(args, cwd, log, timeout, env=None):
    log.parent.mkdir(parents=True, exist_ok=True)
    archive_previous_log(log)
    begin = time.monotonic()
    with log.open("w") as stream:
        p = subprocess.Popen(args, cwd=cwd, stdout=stream, stderr=subprocess.STDOUT,
                             start_new_session=True, env=env)
        timed_out = False
        try:
            code = p.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            os.killpg(p.pid, signal.SIGKILL); code = p.wait()
    text = log.read_text(errors="replace")
    return {"returncode": code, "timeout": timed_out,
            "seconds": round(time.monotonic() - begin, 2), "log": str(log)}, text


def snapshot(source, destination):
    if destination.exists():
        raise RuntimeError(f"Refusing to overwrite existing snapshot: {destination}")
    shutil.copytree(source, destination, ignore=shutil.ignore_patterns(
        ".git", "build", "work", "__pycache__", "simv*", "*.vcd", "releases"))
    for shell in destination.rglob("*.sh"):
        shell.write_bytes(shell.read_bytes().replace(b"\r\n", b"\n"))


def retain(work, target):
    if not work.exists(): return
    for file in work.rglob("*"):
        if not file.is_file() or file.suffix not in (".log", ".json", ".csv", ".dat"):
            continue
        relative = file.relative_to(work)
        dest = target / relative
        dest.parent.mkdir(parents=True, exist_ok=True)
        if file.suffix in (".log", ".dat"):
            with file.open("rb") as src, gzip.open(str(dest) + ".gz", "wb") as dst:
                shutil.copyfileobj(src, dst)
        else: shutil.copyfile(file, dest)


def discard_objects(work):
    if not work.exists(): return
    for folder in sorted(work.rglob("obj*"), reverse=True):
        resolved = folder.resolve()
        if folder.is_dir() and resolved.is_relative_to(work.resolve()):
            shutil.rmtree(resolved)


def parallel(phase, items, operation, workers=1):
    prior = STATE["phases"].get(phase, {})
    if prior.get("status") == "attempted_all":
        print(f"RESUME SKIP {phase}: already attempted", flush=True); return
    STATE["results"].setdefault(phase, [])
    done = {r["name"] for r in STATE["results"][phase]}
    remaining = [item for item in items if str(item) not in done]
    STATE["phases"][phase] = {"status": "running", "total": len(items), "completed": len(done)}
    save()
    print(f"START {phase}: {len(items)} items", flush=True)
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(operation, item): item for item in remaining}
        for future in as_completed(futures):
            name = str(futures[future])
            try: result = future.result()
            except Exception as error:
                result = {"name": name, "status": "harness_error", "detail": str(error)}
            STATE["results"][phase].append(result)
            STATE["phases"][phase]["completed"] += 1
            save()
            print(f"{phase} [{STATE['phases'][phase]['completed']}/{len(items)}] "
                  f"{result.get('name', name)}: {result.get('status', 'recorded')}", flush=True)
    STATE["phases"][phase]["status"] = "attempted_all"
    STATE["phases"][phase]["summary"] = dict(Counter(r.get("status", "recorded")
                                                      for r in STATE["results"][phase]))
    save()


def vip_basic(name):
    logs = OUT / "vip-basic" / name
    result = {"name": name, "steps": {}}
    environment = dict(os.environ, ELAB_STRICT="1")
    info, text = run([sys.executable, "tools/elab_scan.py", str(VIP / "build/vips" / name)],
                     VIP, logs / "elab.log", 120, environment)
    info["status"] = "pass" if info["returncode"] == 0 and re.search(r"^ELAB_ERRORS=0$", text, re.M) else "fail"
    result["steps"]["elab"] = info
    info, text = run([sys.executable, "smoke/flatten.py", name], VIP, logs / "flatten.log", 180)
    info["status"] = "pass" if info["returncode"] == 0 else "error"
    result["steps"]["flatten"] = info
    info, text = run([sys.executable, "smoke/smoke_run.py", name], VIP, logs / "smoke.log", 180)
    matches = re.findall(r"^SMOKE_(PASS|FAIL|SKIP)\b", text, re.M)
    info["status"] = matches[-1].lower() if matches else "error"
    result["steps"]["smoke"] = info
    info, text = run([sys.executable, "tools/run_func_tests.py", name], VIP, logs / "functional.log", 900)
    counts = dict(Counter(re.findall(r"^FUNC_(PASS|FAIL|ERROR|NONE)\b", text, re.M)))
    info["tests"] = counts
    info["status"] = ("timeout" if info["timeout"] else "fail" if counts.get("FAIL") or counts.get("ERROR")
                      else "pass" if info["returncode"] == 0 and counts.get("PASS") else "no_tests")
    result["steps"]["functional"] = info
    retain(VIP / "build/func" / name, logs / "functional-evidence")
    discard_objects(VIP / "build/func" / name)
    retain(VIP / "smoke" / name, logs / "smoke-evidence")
    result["status"] = "pass" if all(r["status"] == "pass" for r in result["steps"].values()) else "review"
    return result


def ip_coverage(name):
    logs = OUT / "ip-coverage" / name
    environment = dict(os.environ, VFLAGS="-j 1")
    info, text = run(["bash", "scripts/cov/run_cov.sh", name], IP, logs / "flow.log", 900, environment)
    marker = re.search(r"^RESULT:\s*(.*)$", text, re.M)
    info.update(name=name, status="timeout" if info["timeout"] else
                "closed" if info["returncode"] == 0 and marker and marker.group(1).startswith("CLOSED") else "not_closed")
    info["metrics"] = re.findall(r"^(?:LINE|TOGGLE|FSM|SVA|FUNCTIONAL) .*$", text, re.M)
    retain(IP / "work/cov" / name, logs / "evidence")
    discard_objects(IP / "work/cov" / name)
    return info


UVM_SUITES = set()
def vip_uvm(name):
    logs = OUT / "vip-uvm" / name
    args = [sys.executable, "tools/run_uvm_tests.py"]
    if name not in UVM_SUITES: args.append("--smoke")
    info, text = run(args + [name], VIP, logs / "flow.log", 1500)
    statuses = re.findall(r"^UVM_(?:SMOKE_)?(PASS|FAIL|WARN|ERROR|NONE)\b", text, re.M)
    info.update(name=name, mode="checked_suite" if name in UVM_SUITES else "uvm_smoke",
                test_statuses=dict(Counter(statuses)))
    info["status"] = ("timeout" if info["timeout"] else "fail" if any(s in statuses for s in ("FAIL", "ERROR"))
                      else "warn" if "WARN" in statuses else "pass" if statuses and set(statuses) == {"PASS"}
                      and info["returncode"] == 0 else "no_test_or_result")
    retain(VIP / "build/uvm" / name, logs / "evidence")
    discard_objects(VIP / "build/uvm" / name)
    return info


def main():
    global STATE
    OUT.mkdir(parents=True, exist_ok=True)
    progress = OUT / "progress.json"
    if progress.exists():
        STATE = json.loads(progress.read_text())
        STATE["status"] = "resuming"
        for phase in STATE.get("phases", {}).values():
            if phase.get("status") == "running": phase["status"] = "interrupted_pending_resume"
    STATE.setdefault("execution_segments", []).append({
        "runner_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "parallel_workers": 1, "uvm_build_jobs": 1})
    os.environ["UVM_JOBS"] = "1"
    # UART dynamic smoke validated this profile; preserve original sources/flags.
    runtime_bin = OUT / 'runtime-bin'
    runtime_bin.mkdir(exist_ok=True)
    wrapper = runtime_bin / 'verilator'
    wrapper.write_text('#!/bin/sh\nexec /usr/local/bin/verilator "$@" --output-groups 0 --output-split 3000 --output-split-cfuncs 500 -CFLAGS "-O0" -MAKEFLAGS "OPT_FAST=-O0 OPT_SLOW=-O0 OPT_GLOBAL=-O0" -j 1\n')
    wrapper.chmod(0o755)
    os.environ['PATH'] = str(runtime_bin) + os.pathsep + os.environ['PATH']
    os.environ['VERILATOR'] = str(wrapper)
    STATE['execution_segments'][-1]['compile_profile'] = 'uart-validated-lowmem-split-O0-v1'
    STATE["tools"] = {}
    for name, args in (("verilator", ["verilator", "--version"]), ("ghdl", ["ghdl", "--version"]),
                       ("pyslang", [sys.executable, "-c", "import pyslang;print(pyslang.__version__)"])):
        STATE["tools"][name] = subprocess.check_output(args, text=True).splitlines()[0]
    if not IP.exists(): snapshot(Path("/input/ip"), IP)
    if not VIP.exists(): snapshot(Path("/input/vip"), VIP)
    if STATE.get("normalization_version", 0) < 2:
        for makefile in IP.glob("Makefile.*"):
            makefile.write_bytes(makefile.read_bytes().replace(b"\r\n", b"\n"))
        if "ip-coverage" in STATE["phases"]:
            STATE["phases"]["ip-coverage-invalid-crlf"] = STATE["phases"].pop("ip-coverage")
            STATE["results"]["ip-coverage-invalid-crlf"] = STATE["results"].pop("ip-coverage", [])
            STATE["phases"]["ip-coverage-invalid-crlf"]["invalidated_reason"] = "Windows CRLF in parsed Makefile variables; not IP failures"
            original = OUT / "ip-coverage"
            archived = OUT / "ip-coverage-invalid-crlf"
            if original.exists() and not archived.exists(): original.rename(archived)
            for result in STATE["results"]["ip-coverage-invalid-crlf"]:
                if "log" in result:
                    result["log"] = result["log"].replace("/ip-coverage/", "/ip-coverage-invalid-crlf/")
        STATE["normalization_version"] = 2
    extracted = VIP / "build/vips"
    already_extracted = extracted.exists() and any(extracted.iterdir())
    extracted.mkdir(parents=True, exist_ok=True)
    for archive in ([] if already_extracted else sorted((VIP / "vips").glob("*.zip"))):
        with zipfile.ZipFile(archive) as z:
            for entry in z.infolist():
                if not (extracted / entry.filename).resolve().is_relative_to(extracted.resolve()):
                    raise RuntimeError("Archive path escapes extraction directory")
            z.extractall(extracted)
    names = sorted(p.name for p in extracted.iterdir() if p.is_dir())
    STATE["vip_count"] = len(names)
    if not names: raise RuntimeError("No VIPs extracted")
    save()
    parallel("vip-basic", names, vip_basic)
    checkers = sorted(p.name[:-7] for p in (IP / "tb/cov").glob("*_sva.sv"))
    parallel("ip-coverage", checkers, ip_coverage)
    sys.path.insert(0, str(VIP / "tools"))
    spec = importlib.util.spec_from_file_location("upstream_uvm", VIP / "tools/run_uvm_tests.py")
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    UVM_SUITES.update(module.SUITES)
    parallel("vip-uvm", names, vip_uvm)
    # Run the upstream coverage measurement; its CSV contains raw denominators/holes.
    STATE["phases"]["vip-coverage"] = {"status": "running"}; save()
    info, text = run([sys.executable, "tools/coverage.py", "-j", "1"], VIP,
                     OUT / "vip-coverage/flow.log", 21600)
    info["status"] = "timeout" if info["timeout"] else "completed" if info["returncode"] == 0 else "review"
    STATE["phases"]["vip-coverage"] = info
    retain(VIP / "build/cov", OUT / "vip-coverage/evidence")
    for report in ("coverage_report.csv", "uvm_report.csv", "uvm_smoke_report.csv"):
        if (VIP / report).exists(): shutil.copyfile(VIP / report, OUT / report)
    STATE["phases"]["independent-crosscheck"] = {"status": "running"}; save()
    info, text = run([sys.executable, "tools/run_ip_crosscheck.py", "--ip-dir", str(IP)],
                     VIP, OUT / "independent-crosscheck/flow.log", 7200)
    info["status"] = "pass" if info["returncode"] == 0 else "review"
    STATE["phases"]["independent-crosscheck"] = info
    retain(VIP / "build/ipcheck", OUT / "independent-crosscheck/evidence")
    if (VIP / "ipcheck_report.csv").exists():
        shutil.copyfile(VIP / "ipcheck_report.csv", OUT / "ipcheck_report.csv")
    STATE["status"] = "attempted_all_configured_phases"; save()
    (OUT / "report.json").write_text(json.dumps(STATE, indent=2))
    print("ALL CONFIGURED PHASES ATTEMPTED (inspect statuses, skips and holes)", flush=True)


if __name__ == "__main__":
    single_request = OUT / "single-vip-request.json"
    if single_request.exists() or (OUT / "single-vip-request.claimed").exists() or (OUT / "single-vip-attempts").exists():
        from run_single_vip_coverage import dispatch
        # Deliberately outside the batch error handler: preserve progress.json.
        sys.exit(dispatch(single_request, VIP, OUT))
    try: main()
    except Exception as error:
        STATE.update(status="orchestrator_error", error=str(error)); save(); raise
