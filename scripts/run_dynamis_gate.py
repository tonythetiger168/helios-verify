"""Transaction evidence gate using external Dynamis TX; never creates oracle labels."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys

def validate(path):
    records=[json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    if not records:raise ValueError('empty transaction denominator')
    for r in records:
        if not isinstance(r,dict) or not isinstance(r.get('kind'),str) or not r['kind']:
            raise ValueError('invalid transaction kind')
        if type(r.get('t')) is not int or r['t']<0 or not isinstance(r.get('payload'),str) or not r['payload']:
            raise ValueError('invalid timestamp/payload')
        keys=set()
        for field in r['payload'].split(','):
            if '=' not in field:raise ValueError('payload must contain key=value fields')
            key=field.split('=',1)[0].strip()
            if not key or key in keys:raise ValueError('empty/duplicate payload key')
            keys.add(key)
    return len(records)

def main():
    p=argparse.ArgumentParser();p.add_argument('golden',type=Path);p.add_argument('run',type=Path)
    p.add_argument('--dynamis',type=Path,default=Path(__file__).resolve().parents[2]/'dynamis')
    p.add_argument('--output',type=Path,required=True);a=p.parse_args();a.output.mkdir(parents=True,exist_ok=True)
    inputs=[a.golden.resolve(),a.run.resolve()]
    if any((a.output/f).resolve() in inputs for f in ('evidence.json','diff.json','gate.log')):
        p.error('output would overwrite input')
    evidence={'status':'error','calibration_label_created':False,
              'scope':'Dynamis per-kind ordered transaction comparison, not general design proof'}
    try:
        counts=[validate(path) for path in inputs]
        revision=subprocess.check_output(['git','-C',str(a.dynamis),'rev-parse','HEAD'],text=True).strip()
        result=subprocess.run([sys.executable,str(a.dynamis/'tx/tools/txdiff.py'),str(inputs[0]),str(inputs[1]),
                               '--with-time','--json',str(a.output/'diff.json')],capture_output=True,text=True,timeout=30)
        (a.output/'gate.log').write_text(result.stdout+result.stderr)
        if result.returncode not in (0,1):raise RuntimeError('Dynamis tool failure')
        diff=json.loads((a.output/'diff.json').read_text())['diffs']
        if (result.returncode==0)!= (len(diff)==0):raise RuntimeError('Inconsistent tool result')
        evidence.update(status='equivalent' if not diff else 'different',samples=counts,differences=len(diff),
                        dynamis_revision=revision,with_time=True,
                        tool_sha256=hashlib.sha256((a.dynamis/'tx/tools/txdiff.py').read_bytes()).hexdigest(),
                        sha256={str(x):hashlib.sha256(x.read_bytes()).hexdigest() for x in inputs})
        code=result.returncode
    except Exception as e:
        evidence['error']=str(e);code=2
    (a.output/'evidence.json').write_text(json.dumps(evidence,indent=2));print(json.dumps(evidence));return code

if __name__=='__main__':raise SystemExit(main())
