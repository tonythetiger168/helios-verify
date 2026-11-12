"""Read-only partial recovery using the original upstream parser and DUT manifest."""
import argparse,importlib.util,json,hashlib
from importlib.machinery import SourceFileLoader
from pathlib import Path
def main():
    p=argparse.ArgumentParser();p.add_argument('--parser',type=Path,required=True);p.add_argument('--artifacts',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    spec=importlib.util.spec_from_loader('upstream_coverage_recovery',SourceFileLoader('upstream_coverage_recovery',str(a.parser)));module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    rows=[]
    for vip in ('ACE-UVM-VIP','ACE_Lite-UVM-VIP','AHB-VIP','APB-VIP'):
        manifest=a.artifacts/('manifest-'+vip+'.json');dat=a.artifacts/('recovered-'+vip+'.dat')
        if not dat.exists():dat=a.artifacts/(vip+'.dat')
        sources=[x['file'] for x in json.loads(manifest.read_text()) if x.get('status')=='FLAT']
        if not sources:raise ValueError('empty DUT manifest')
        points=module.parse_dat(str(dat))
        if not points:raise ValueError('empty raw coverage')
        result=module.summarize([str(dat)],sources)
        if result['line'][1]<=0:raise ValueError('empty DUT line denominator')
        rows.append({'vip':vip,'dut_files':sources,'metrics':{k:{'hit':result[k][0],'total':result[k][1],'pct':module.pct(*result[k])} for k in module.METRICS},'line_missed':result.get('line_missed'),'status':'partial_coverage_uvm_build_timeout','sha256':{f.name:hashlib.sha256(f.read_bytes()).hexdigest() for f in (manifest,dat)}})
    report={'schema':'vip-recovered-coverage-v1','scope':'saved functional-test coverage; UVM build timeout; no full PASS or closure claim','parser_sha256':hashlib.sha256(a.parser.read_bytes()).hexdigest(),'results':rows}
    a.output.write_text(json.dumps(report,indent=2)+'\n');print(json.dumps([{k:r[k] for k in ('vip','metrics','status')} for r in rows]))
if __name__=='__main__':main()
