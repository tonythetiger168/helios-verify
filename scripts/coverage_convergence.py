"""Audit same-scope Verilator raw points; no source equivalence or RTL closure claim."""
import argparse,hashlib,json,re
from pathlib import Path

def read_points(path,dut_names):
    points={}; origins={}
    for line in path.read_text().splitlines():
        m=re.fullmatch(r"C '(.*)' (\d+)",line)
        if not m:
            if line.startswith("C "):raise ValueError("malformed coverage record/count")
            continue
        fields = {}
        for field in m[1].removeprefix("\x01").split("\x01"):
            key, separator, value = field.partition("\x02")
            if not key or not separator or key in fields:
                raise ValueError("malformed or duplicate coverage field")
            fields[key] = value
        name=Path(fields.get('f','')).name
        if name not in dut_names:continue
        origin=fields['f']
        if name in origins and origins[name]!=origin:raise ValueError('ambiguous DUT source basename')
        origins[name]=origin
        fields['f']=name
        key=json.dumps(fields,sort_keys=True)
        points[key]=points.get(key,0)+int(m[2])
    if not points:raise ValueError('empty requested DUT denominator')
    if set(origins)!=set(dut_names):raise ValueError('requested DUT source missing')
    return points

def audit(baseline,runs,dut_names,source_records=None):
    if not runs:raise ValueError('at least one new run required')
    if source_records is not None:
        if len(source_records)!=len(runs)+1:raise ValueError('source record per baseline and run required')
        for record in source_records:
            if set(record)!=set(dut_names) or not all(isinstance(v,str) and re.fullmatch('[0-9a-f]{64}',v) for v in record.values()):raise ValueError('exact DUT SHA256 records required')
        if any(record!=source_records[0] for record in source_records[1:]):raise ValueError('recorded DUT content changed')
    merged=read_points(baseline,dut_names);keys=set(merged);rounds=[]
    for path in runs:
        current=read_points(path,dut_names)
        if set(current)!=keys:raise ValueError('DUT point identity/denominator changed')
        added = [json.loads(key) for key in sorted(keys) if merged[key] == 0 and current[key] > 0]
        for key,value in current.items():merged[key]+=value
        categories={}
        for key in keys:
            category=json.loads(key).get('page','').split('/')[0]
            row=categories.setdefault(category,{'hit':0,'raw_points':0})
            row['raw_points']+=1;row['hit']+=merged[key]>0
        rounds.append({'path':str(path),'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'new_points':len(added),'new_point_keys':added,'cumulative':categories})
    return {'baseline':str(baseline),'baseline_sha256':hashlib.sha256(baseline.read_bytes()).hexdigest(),'rounds':rounds,'remaining':[json.loads(k) for k in sorted(keys) if merged[k]==0],'scope':'exact raw DUT point keys after source-basename normalization; testbench excluded; raw line points differ from expanded lines','recorded_source_sha256_agreement':source_records is not None,'source_equivalence_verified':False,'full_rtl_closure':False}

def main():
    p=argparse.ArgumentParser();p.add_argument('--baseline',type=Path,required=True);p.add_argument('--run',type=Path,action='append',required=True);p.add_argument('--dut-name',action='append',required=True);p.add_argument('--source-records',type=Path);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    result=audit(a.baseline,a.run,set(a.dut_name),json.loads(a.source_records.read_text()) if a.source_records else None)
    with a.output.open('x') as f:json.dump(result,f,indent=2)
if __name__=='__main__':main()
