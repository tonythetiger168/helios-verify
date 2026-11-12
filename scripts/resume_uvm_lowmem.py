"""Resume generated UART C++ on container storage with live logs/checkpoints."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import time

root = Path('/diagnostic')
source = root/'build/uvm/UART-VIP/obj'
cache = Path('/tmp/uart-lowmem-obj')
report = root/'resume-report.json'
segment = str(time.time_ns())
if report.exists():
    previous = json.loads(report.read_text())
    (root/f'resume-report-before-{segment}.json').write_text(json.dumps(previous,indent=2))
manifest = {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
            for p in source.iterdir() if p.suffix in ('.cpp','.h','.mk')}
if not manifest or not (source/'Vtop_tb.mk').exists():
    raise RuntimeError('Missing generated source; refusing to restart RTL generation')
fingerprint = hashlib.sha256(json.dumps(manifest,sort_keys=True).encode()).hexdigest()
if cache.exists():
    if (cache/'source-fingerprint').read_text() != fingerprint:
        raise RuntimeError('Generated source changed; refusing to reuse object cache')
else:
    shutil.copytree(source,cache)
    (cache/'source-fingerprint').write_text(fingerprint)
def memory():
    return dict(line.split() for line in Path('/sys/fs/cgroup/memory.events').read_text().splitlines())
state = dict(status='building',source_fingerprint=fingerprint,objects_before=len(list(cache.glob('*.o'))),
             segment=segment, memory_before=memory(),storage='container native /tmp; survives stop/start of this container')
def save():
    state['objects_now']=len(list(cache.glob('*.o')))
    state['memory_now']=memory()
    temporary=report.with_suffix('.tmp')
    temporary.write_text(json.dumps(state,indent=2))
    temporary.replace(report)
save()
begin=time.monotonic()
with (root/'resume-build.log').open('a') as log:
    cmd=['make','-C',str(cache),'-f','Vtop_tb.mk','-j','1',
         'OPT_FAST=-O0','OPT_SLOW=-O0','OPT_GLOBAL=-O0']
    p=subprocess.Popen(cmd,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
    while p.poll() is None:
        if time.monotonic()-begin>1800:
            os.killpg(p.pid,signal.SIGKILL)
            state['status']='build_timeout_resumable'
            break
        save()
        time.sleep(5)
    rc=p.wait()
state['build_returncode']=rc
state['build_seconds']=round(time.monotonic()-begin,2)
if rc == 0:
    import sys
    sys.path.insert(0,str(root/'tools'))
    import run_uvm_tests as upstream
    with (root/'resume-run.log').open('w') as log:
        try:
            sim=subprocess.run([str(cache/'Vtop_tb'),'+UVM_TESTNAME=uart_random_test'],
                               cwd=cache,stdout=log,stderr=subprocess.STDOUT,timeout=120)
            text=(root/'resume-run.log').read_text(errors='replace')
            status,_,detail=upstream.classify_smoke(sim.returncode,text)
            state.update(status='pass' if status=='PASS' else 'simulation_review',
                         simulation_status=status,simulation_detail=detail,simulation_returncode=sim.returncode)
        except subprocess.TimeoutExpired:
            state['status']='simulation_timeout'
elif state['status']=='building':
    state['status']='build_failed'
save()
state['oom_kill_delta']=int(state['memory_now']['oom_kill'])-int(state['memory_before']['oom_kill'])
state['memory_peak_bytes']=Path('/sys/fs/cgroup/memory.peak').read_text().strip()
save()
print(json.dumps(state,indent=2),flush=True)
