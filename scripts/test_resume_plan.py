from pathlib import Path
import tempfile,json
from plan_vip_coverage_resume import plan
with tempfile.TemporaryDirectory() as d:
    r=Path(d);(r/'A.zip').touch();(r/'B.zip').touch();p=r/'partial.json'
    row={'vip':'A','status':'partial_coverage_uvm_build_timeout','metrics':{'line':{'hit':1,'total':2}}}
    p.write_text(json.dumps({'results':[row]}));a=plan(r,p)
    assert a['fully_completed']==0 and a['coverage_not_recorded']==['B'] and a['uvm_retry_required']==['A']
    for bad in ([row,row],[{**row,'vip':'unknown'}],[{**row,'metrics':{'line':{'hit':2,'total':1}}}]):
        p.write_text(json.dumps({'results':bad}))
        try:plan(r,p)
        except ValueError:pass
        else:raise AssertionError('invalid partial report accepted')
print('4 planning checks passed: partial pending, duplicate, unknown and bad denominator')
