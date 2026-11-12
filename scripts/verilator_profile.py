"""Render an isolated, logged Verilator low-memory wrapper."""
def render_wrapper(real,logs):
    return "#!/usr/bin/env python3\n"+"REAL="+repr(str(real))+"\nLOGS="+repr(str(logs))+"\n"+BODY
BODY=r'''
import json,os,re,subprocess,sys,time
from pathlib import Path
incoming=sys.argv[1:];args=[];i=0
while i<len(incoming):
    arg=incoming[i]
    if arg in ('-j','--build-jobs'):
        if i+1>=len(incoming):raise SystemExit('missing build job value')
        i+=2;continue
    if re.fullmatch(r'-j[0-9]+',arg) or arg.startswith('--build-jobs='):
        i+=1;continue
    args.append(arg);i+=1
args+=['--output-groups','0','--output-split','3000','--output-split-cfuncs','500','-CFLAGS','-O0','-MAKEFLAGS','OPT_FAST=-O0 OPT_SLOW=-O0 OPT_GLOBAL=-O0','-j','1']
folder=Path(LOGS);folder.mkdir(parents=True,exist_ok=True)
stem=str(time.time_ns())+'-'+str(os.getpid())
(folder/(stem+'.json')).write_text(json.dumps({'incoming':incoming,'effective':[REAL]+args},indent=2))
with (folder/(stem+'.log')).open('wb') as log:
    child=subprocess.Popen([REAL]+args,stdout=subprocess.PIPE,stderr=subprocess.STDOUT)
    for chunk in iter(lambda:child.stdout.read1(65536),b''):
        log.write(chunk);log.flush();sys.stdout.buffer.write(chunk);sys.stdout.buffer.flush()
    code=child.wait()
(folder/(stem+'.exit.json')).write_text(json.dumps({'returncode':code}))
sys.exit(code if code>=0 else 128-code)
'''
