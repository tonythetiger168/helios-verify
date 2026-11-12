import csv,json,tempfile,unittest
from pathlib import Path
from plan_vip_coverage_resume import plan
class PlanTests(unittest.TestCase):
    def exercise(self,mode):
        with tempfile.TemporaryDirectory() as d:
            folder=Path(d);(folder/'UART-VIP.zip').touch()
            partial=folder/'partial.json';partial.write_text(json.dumps({'results':[]}))
            row={'vip':'UART-VIP','tests':'1','covered_tests':'1','line_missed':'dut:21,49','detail':''}
            for name in ('line','toggle','expr','assert','func'):
                row[name+'_hit']='0';row[name+'_total']='0'
            row.update(line_hit='18',line_total='20',toggle_hit='9',toggle_total='10',expr_hit='4',expr_total='4')
            if mode=='invalid':row['line_hit']='21'
            if mode=='uvm_failure':row['detail']='UVM build: make exit2'
            if mode=='functional_failure':
                row['detail']='func_tb.sv: FAIL fails under coverage: [FAIL] scoreboard mismatch'
            if mode=='compile_failure':
                row['detail']='func_tb.sv: ERROR plain compile: %Error: unresolved package'
                row['covered_tests']='0'
                for metric in ('line','toggle','expr'):
                    row[metric+'_hit']=row[metric+'_total']='0'
            checkpoint=folder/'checkpoint.csv'
            with checkpoint.open('w',newline='') as f:
                writer=csv.DictWriter(f,fieldnames=list(row));writer.writeheader()
                if mode!='empty':writer.writerow(row)
                if mode=='duplicate':writer.writerow(row)
            if mode not in ('valid','uvm_failure','functional_failure','compile_failure'):
                with self.assertRaises(ValueError):plan(folder,partial,checkpoint)
            else:
                result=plan(folder,partial,checkpoint)
                self.assertEqual(result['fully_completed'],0);self.assertEqual(result['pending_full_acceptance'],['UART-VIP'])
                if mode=='uvm_failure':
                    self.assertEqual(result['uvm_retry_required'],['UART-VIP']);self.assertEqual(result['checkpoint_review_required'],['UART-VIP'])
                    self.assertIn('uvm_build_failed',result['checkpoints']['UART-VIP']['issues'])
                if mode=='functional_failure':
                    self.assertEqual(result['measured_checkpoints'],['UART-VIP'])
                    self.assertEqual(result['checkpoint_review_required'],['UART-VIP'])
                    self.assertEqual(result['uvm_retry_required'],[])
                    self.assertIn('functional_failed_under_coverage',result['checkpoints']['UART-VIP']['issues'])
                if mode=='compile_failure':
                    self.assertEqual(result['attempted_unmeasured'],['UART-VIP'])
                    self.assertEqual(result['checkpoint_review_required'],['UART-VIP'])
                    self.assertEqual(result['uvm_retry_required'],[])
                    issues=result['checkpoints']['UART-VIP']['issues']
                    self.assertIn('functional_compile_failed',issues)
                    self.assertNotIn('functional_failed_under_coverage',issues)
                item=result['checkpoints']['UART-VIP'];self.assertFalse(item['metrics']['assert']['measured']);self.assertFalse(item['metrics']['func']['measured'])
                self.assertEqual(item['line_missed'],'dut:21,49');self.assertEqual(result['coverage_not_recorded'],[])
    def test_pending_and_unmeasured(self):self.exercise('valid')
    def test_uvm_failure_preserves_coverage_and_retry(self):self.exercise('uvm_failure')
    def test_functional_failure_preserves_coverage_and_requires_review(self):
        self.exercise('functional_failure')
    def test_compile_failure_requires_review_without_coverage(self):
        self.exercise('compile_failure')
    def test_invalid_counts(self):self.exercise('invalid')
    def test_duplicate(self):self.exercise('duplicate')
    def test_empty(self):self.exercise('empty')
    def test_multiple_sources_and_conflict(self):
        with tempfile.TemporaryDirectory() as d:
            folder=Path(d)
            for name in ('UART-VIP','SPI-VIP'):(folder/(name+'.zip')).touch()
            partial=folder/'partial.json';partial.write_text(json.dumps({'results':[]}))
            paths=[]
            for name in ('UART-VIP','SPI-VIP'):
                row={'vip':name,'tests':'1','covered_tests':'1','line_missed':'dut:21','detail':''}
                for metric in ('line','toggle','expr','assert','func'):
                    row[metric+'_hit']='0';row[metric+'_total']='0'
                row.update(line_hit='1',line_total='2')
                path=folder/(name+'.csv');paths.append(path)
                with path.open('w',newline='') as stream:
                    writer=csv.DictWriter(stream,fieldnames=list(row));writer.writeheader();writer.writerow(row)
            result=plan(folder,partial,paths)
            self.assertEqual(set(result['checkpoints']),{'UART-VIP','SPI-VIP'})
            self.assertEqual(len(result['checkpoint_sources']),2);self.assertEqual(result['fully_completed'],0)
            with self.assertRaises(ValueError):plan(folder,partial,[paths[0],paths[0]])
            with self.assertRaises(ValueError):plan(folder,partial,[])
    def test_unmeasured_and_no_test_are_records_not_passes(self):
        for tests,covered,contradictory in [(1,0,False),(0,0,False),(1,0,True)]:
            with self.subTest(tests=tests,contradictory=contradictory),tempfile.TemporaryDirectory() as d:
                folder=Path(d);(folder/'Bluetooth5-VIP.zip').touch();partial=folder/'partial.json';partial.write_text(json.dumps({'results':[]}))
                row={'vip':'Bluetooth5-VIP','tests':str(tests),'covered_tests':str(covered),'line_missed':'','detail':'build failed' if tests else 'no functional test'}
                for m in ('line','toggle','expr','assert','func'):row[m+'_hit']='0';row[m+'_total']='0'
                if contradictory:row['line_total']='1'
                checkpoint=folder/'checkpoint.csv'
                with checkpoint.open('w',newline='') as f:
                    w=csv.DictWriter(f,fieldnames=list(row));w.writeheader();w.writerow(row)
                if contradictory:
                    with self.assertRaises(ValueError):plan(folder,partial,checkpoint)
                else:
                    result=plan(folder,partial,checkpoint)
                    self.assertEqual(result['fully_completed'],0);self.assertEqual(result['measured_checkpoints'],[])
                    self.assertEqual(result['attempted_unmeasured'],['Bluetooth5-VIP']);self.assertEqual(result['coverage_not_recorded'],[])
                    self.assertEqual(result['pending_full_acceptance'],['Bluetooth5-VIP'])
                    self.assertEqual(result['checkpoints']['Bluetooth5-VIP']['status'],'attempted_coverage_unmeasured' if tests else 'no_functional_test_unmeasured')
if __name__=='__main__':unittest.main()
