"""Explicit single-VIP execution in the existing private container snapshot."""
import argparse,hashlib,json,re,sys,os
from pathlib import Path
from run_portfolio_extended import run
from verilator_profile import render_wrapper

def validate(request,root):
    if set(request) not in ({'vip','timeout_seconds'},{'vip','timeout_seconds','attempt_id'}):raise ValueError('exact request fields required')
    if 'attempt_id' in request and (not isinstance(request['attempt_id'],str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,63}',request['attempt_id'])):raise ValueError('safe attempt_id required')
    vip=request['vip'];seconds=request['timeout_seconds']
    if not isinstance(vip,str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]*',vip):raise ValueError('single VIP name required')
    if not isinstance(seconds,int) or isinstance(seconds,bool) or not 1<=seconds<=3600:raise ValueError('timeout 1..3600 required')
    source=root/'tools/coverage.py'
    if 'AI_EDA_PER_VIP_CHECKPOINT_V2' not in source.read_text():raise ValueError('checkpoint V2 required')
    return vip,seconds,source

def execute(request,root,output):
    vip,seconds,source=validate(request,root)
    # An exclusive new output directory prevents accidental stale-result reuse.
    output.mkdir(parents=True,exist_ok=False)
    (output/'request.json').write_text(json.dumps(request,indent=2))
    runtime=output/'runtime-bin';runtime.mkdir()
    wrapper=runtime/'verilator'
    wrapper.write_text(render_wrapper('/usr/local/bin/verilator',output/'tool-logs'))
    wrapper.chmod(0o755)
    env=os.environ.copy();env.update(UVM_JOBS='1',VERILATOR=str(wrapper),PATH=str(runtime)+os.pathsep+env['PATH'])
    (output/'compile-profile.json').write_text(json.dumps({'profile':'single-job-normalized-logged-split-O0-v2','UVM_JOBS':1,'wrapper_sha256':hashlib.sha256(wrapper.read_bytes()).hexdigest()},indent=2))
    # A separate child process preserves timeout/process-group termination.
    child="import importlib.util,sys,os;spec=importlib.util.spec_from_file_location('single_coverage',sys.argv[1]);m=importlib.util.module_from_spec(spec);sys.modules[spec.name]=m;spec.loader.exec_module(m);assert sys.argv[3] in os.listdir(m.rf().VIPS);m.REPORT=sys.argv[2];sys.exit(m.main(['-j','1',sys.argv[3]]))"
    info,_=run([sys.executable,'-c',child,str(source),str(output/'coverage_report.csv'),vip],root,output/'flow.log',seconds,env=env)
    info.update(vip=vip,coverage_source_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),scope='single VIP only; return code alone is not closure',csv_exists=(output/'coverage_report.csv').exists())
    (output/'execution.json').write_text(json.dumps(info,indent=2))
    return info

def dispatch(request_path,root,results):
    request=json.loads(request_path.read_text())
    validate(request,root)
    # Persistent claim prevents a container restart from replaying this request.
    namespace=results
    if 'attempt_id' in request:
        namespace=results/'single-vip-attempts'/request['attempt_id']
        namespace.mkdir(parents=True,exist_ok=True)
    claim=namespace/'single-vip-request.claimed'
    with claim.open('x') as stream:
        stream.write(hashlib.sha256(request_path.read_bytes()).hexdigest())
    result=execute(request,root,namespace/'single-vip-coverage')
    return 124 if result['timeout'] else (0 if result['returncode']==0 else 1)

def main():
    p=argparse.ArgumentParser();p.add_argument('--request',type=Path,required=True);p.add_argument('--output',type=Path,required=True);p.add_argument('--root',type=Path,default=Path('/work/open-vip-portfolio'));a=p.parse_args()
    result=execute(json.loads(a.request.read_text()),a.root.resolve(),a.output.resolve())
    print(json.dumps(result))
    return 124 if result['timeout'] else (0 if result['returncode']==0 else 1)
if __name__=='__main__':sys.exit(main())
