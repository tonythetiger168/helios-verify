"""Read-only scheduling plan; partial coverage never implies complete UVM PASS."""
import argparse,hashlib,json,csv,re
from pathlib import Path
def plan(archives,partial,checkpoint=None):
    names=sorted(p.stem for p in Path(archives).glob('*.zip'))
    if not names or len(names)!=len(set(names)):raise ValueError('empty/duplicate VIP inventory')
    rows=json.loads(Path(partial).read_text())['results'];seen=set()
    for row in rows:
        name=row['vip']
        if name not in names or name in seen:raise ValueError('unknown/duplicate partial VIP')
        if row['status']!='partial_coverage_uvm_build_timeout':raise ValueError('unrecognized partial status')
        for metric in row['metrics'].values():
            hit,total=metric['hit'],metric['total']
            if not isinstance(hit,int) or not isinstance(total,int) or not 0<=hit<=total:raise ValueError('invalid coverage denominator')
        if row['metrics']['line']['total']==0:raise ValueError('empty partial line denominator')
        seen.add(name)
    result={'schema':'vip-coverage-resume-plan-v1','inventory_count':len(names),'fully_completed':0,'partial_available':sorted(seen),'coverage_not_recorded':[n for n in names if n not in seen],'uvm_retry_required':sorted(seen),'pending_full_acceptance':names,'auto_start':False,'partial_sha256':hashlib.sha256(Path(partial).read_bytes()).hexdigest(),'scope':'planning only; recovered records are not accepted complete checkpoints'}
    if checkpoint is not None:
        paths=[Path(p) for p in checkpoint] if isinstance(checkpoint,(list,tuple)) else [Path(checkpoint)]
        if not paths:raise ValueError('empty checkpoint list')
        records=[]
        for path in paths:
            with path.open(newline='') as stream:part=list(csv.DictReader(stream))
            if not part:raise ValueError('empty checkpoint')
            records.extend(part)
        observed={}
        if not records:raise ValueError('empty checkpoint')
        for row in records:
            name=row['vip']
            if name not in names or name in observed:raise ValueError('unknown/duplicate checkpoint VIP')
            tests,covered=int(row['tests']),int(row['covered_tests'])
            if not 0<=covered<=tests:raise ValueError('invalid test denominator')
            metrics={}
            for metric in ('line','toggle','expr','assert','func'):
                hit,total=int(row[metric+'_hit']),int(row[metric+'_total'])
                if not 0<=hit<=total:raise ValueError('invalid checkpoint denominator')
                metrics[metric]={'hit':hit,'total':total,'measured':total>0}
            if covered==0 and any(m['total'] for m in metrics.values()):raise ValueError('coverage points without covered tests')
            issues=[]
            status='checkpoint_recorded_pending_full_acceptance'
            if tests==0:
                issues.append('no_functional_test');status='no_functional_test_unmeasured'
            elif covered==0:
                issues.append('coverage_unavailable');status='attempted_coverage_unmeasured'
            elif metrics['line']['total']==0:
                issues.append('no_dut_line_measurement');status='checkpoint_without_dut_line_measurement'
            if covered<tests:issues.append('functional_tests_not_all_covered')
            if row['detail']:issues.append('reported_detail_requires_review')
            if re.search(r'(?:^| \| )[^|:]+\.sv: ERROR [\w-]+ compile:', row['detail']):
                issues.append('functional_compile_failed')
            if 'FAIL fails under coverage:' in row['detail']:
                issues.append('functional_failed_under_coverage')
            if 'UVM build:' in row['detail']:issues.append('uvm_build_failed')
            observed[name]={'issues':issues,'tests':tests,'covered_tests':covered,'metrics':metrics,'line_missed':row['line_missed'],'detail':row['detail'],'status':status}
        result['schema']='vip-coverage-resume-plan-v2'
        result['checkpoints']=observed
        result['measured_checkpoints']=sorted(n for n,r in observed.items() if r['metrics']['line']['measured'])
        result['attempted_unmeasured']=sorted(n for n,r in observed.items() if not r['metrics']['line']['measured'])
        result['checkpoint_review_required']=sorted(n for n,r in observed.items() if r['issues'])
        result['uvm_retry_required']=sorted(set(result['uvm_retry_required'])|{n for n,r in observed.items() if 'uvm_build_failed' in r['issues']})
        result['checkpoint_sources']=[{'path':str(p),'sha256':hashlib.sha256(p.read_bytes()).hexdigest()} for p in paths]
        if len(paths)==1:result['checkpoint_sha256']=result['checkpoint_sources'][0]['sha256']
        result['coverage_not_recorded']=[n for n in names if n not in seen and n not in observed]
        result['scope']='planning only; checkpoints and recovered records do not establish full acceptance'
    return result

def main():
    p=argparse.ArgumentParser();p.add_argument('--archives',type=Path,required=True);p.add_argument('--partial',type=Path,required=True);p.add_argument('--output',type=Path,required=True);p.add_argument('--checkpoint',type=Path,action='append');a=p.parse_args()
    d=plan(a.archives,a.partial,a.checkpoint);a.output.write_text(json.dumps(d,indent=2)+'\n');print(json.dumps({'inventory':d['inventory_count'],'partial':len(d['partial_available']),'coverage_not_recorded':len(d['coverage_not_recorded']),'fully_completed':d['fully_completed']}))
if __name__=='__main__':main()
