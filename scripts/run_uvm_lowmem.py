"""Single UART UVM diagnostic in an isolated container; no source rewrite."""
import json
import os
from pathlib import Path
import signal
import subprocess
import time

root = Path('/diagnostic')
os.chdir(root)
def counters():
    return dict(line.split() for line in Path('/sys/fs/cgroup/memory.events').read_text().splitlines())
wrapper = root/'verilator-lowmem'
wrapper.write_text('#!/bin/sh\nexec /usr/local/bin/verilator --output-groups 0 --output-split 3000 --output-split-cfuncs 500 -CFLAGS "-O0" -MAKEFLAGS "OPT_FAST=-O0 OPT_SLOW=-O0 OPT_GLOBAL=-O0" "$@"\n')
wrapper.chmod(0o755)
before = counters()
begin = time.monotonic()
(root/'report.json').write_text(json.dumps(dict(status='running', pid=os.getpid(), memory_events_before=before)))
env = {**os.environ, 'VERILATOR':str(wrapper), 'UVM_JOBS':'1', 'UVM_CORE':'/opt/uvm-core/src'}
with (root/'flow.log').open('w') as log:
    p = subprocess.Popen(['/opt/eda-python/bin/python','-u','tools/run_uvm_tests.py','--smoke','UART-VIP'],
                         env=env, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
    timed_out = False
    try:
        rc = p.wait(timeout=1800)
    except subprocess.TimeoutExpired:
        timed_out = True
        os.killpg(p.pid, signal.SIGKILL)
        rc = p.wait()
text = (root/'flow.log').read_text(errors='replace')
after = counters()
report = dict(returncode=rc, timeout=timed_out, seconds=round(time.monotonic()-begin,2),
              status='pass' if rc == 0 and 'UVM_SMOKE_PASS UART-VIP/' in text else 'review',
              memory_events_before=before, memory_events_after=after,
              oom_kill_delta=int(after['oom_kill'])-int(before['oom_kill']),
              memory_peak_bytes=Path('/sys/fs/cgroup/memory.peak').read_text().strip(),
              scope='UART upstream smoke only; not full regression or coverage closure',
              version=subprocess.check_output(['/usr/local/bin/verilator','--version'],text=True).strip())
(root/'report.json').write_text(json.dumps(report,indent=2))
print(json.dumps(report,indent=2),flush=True)
