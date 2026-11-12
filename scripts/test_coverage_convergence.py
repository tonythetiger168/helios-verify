import tempfile,unittest
from pathlib import Path
from coverage_convergence import audit
class Tests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
 def point(self,hit,name='dut.sv',obj='x'):
  return "C '\x01f\x02/a/"+name+"\x01page\x02v_toggle/top\x01o\x02"+obj+"' "+str(hit)+"\n"
 def file(self,name,text):
  p=self.root/name;p.write_text(text);return p
 def test_gain_plateau_and_tb_exclusion(self):
  baseline = self.file('b', self.point(0) + self.point(0, 'tb.sv'))
  run = self.file('r', self.point(1) + self.point(1, 'tb.sv'))
  result = audit(baseline, [run, run], {'dut.sv'})
  first, second = result['rounds']
  self.assertEqual([first['new_points'], second['new_points']], [1, 0])
  self.assertEqual(first['new_point_keys'], [{'f': 'dut.sv', 'o': 'x', 'page': 'v_toggle/top'}])
  self.assertEqual(second['new_point_keys'], [])
  self.assertEqual(result['remaining'], [])
  self.assertFalse(result['full_rtl_closure'])
 def test_changed_denominator_rejected(self):
  with self.assertRaises(ValueError):audit(self.file('b',self.point(0)),[self.file('r',self.point(1,obj='y'))],{'dut.sv'})
 def test_empty_dut_rejected(self):
  with self.assertRaises(ValueError):audit(self.file('b',self.point(0,'tb.sv')),[self.file('r',self.point(1))],{'dut.sv'})
 def test_empty_runs_rejected(self):
  with self.assertRaises(ValueError):audit(self.file('b',self.point(0)),[],{'dut.sv'})
 def test_remaining_not_waived(self):
  d=audit(self.file('b',self.point(0)),[self.file('r',self.point(0))],{'dut.sv'});self.assertEqual(len(d['remaining']),1)
 def test_basename_collision_rejected(self):
  b=self.file('b',self.point(0)+self.point(1).replace('/a/dut.sv','/other/dut.sv'))
  with self.assertRaises(ValueError):audit(b,[self.file('r',self.point(1))],{'dut.sv'})
 def test_missing_requested_source_rejected(self):
  with self.assertRaises(ValueError):audit(self.file('b',self.point(0)),[self.file('r',self.point(1))],{'dut.sv','missing.sv'})
 def test_recorded_source_change_rejected(self):
  with self.assertRaises(ValueError):audit(self.file('b',self.point(0)),[self.file('r',self.point(1))],{'dut.sv'},[{'dut.sv':'a'*64},{'dut.sv':'b'*64}])
 def test_matching_records_are_not_hermetic_proof(self):
  d=audit(self.file('b',self.point(0)),[self.file('r',self.point(1))],{'dut.sv'},[{'dut.sv':'a'*64}]*2);self.assertTrue(d['recorded_source_sha256_agreement']);self.assertFalse(d['source_equivalence_verified'])
 def test_missing_or_malformed_source_records(self):
  b=self.file('b',self.point(0));r=self.file('r',self.point(1))
  for records in [[],[{'dut.sv':'bad'}]*2,[{'other.sv':'a'*64}]*2]:
   with self.subTest(records=records),self.assertRaises(ValueError):audit(b,[r],{'dut.sv'},records)
 def test_corrupt_counts_cannot_shrink_both_denominators(self):
  # A valid hit must not hide a second point dropped from both input files.
  for count in ['-1','nan','1.5','']:
   bad=self.point(count,obj='lost')
   with self.subTest(count=count),self.assertRaisesRegex(ValueError,'malformed'):
    audit(self.file('b',self.point(0)+bad),[self.file('r',self.point(1)+bad)],{'dut.sv'})
 def test_truncated_record_rejected(self):
  with self.assertRaisesRegex(ValueError,'malformed'):
   audit(self.file('b',self.point(0)),[self.file('r',self.point(1)+"C 'truncated")],{'dut.sv'})
 def test_malformed_metadata_cannot_collapse_point_identity(self):
  for field in ('o\x02x', 'field-without-value', '\x02value'):
   corrupt = self.point(1, obj='other').replace("' 1", "\x01" + field + "' 1")
   with self.subTest(field=field), self.assertRaisesRegex(ValueError, 'coverage field'):
    audit(self.file('b', self.point(0)), [self.file('r', self.point(1) + corrupt)], {'dut.sv'})
if __name__=='__main__':unittest.main()
