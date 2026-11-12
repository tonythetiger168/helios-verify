"""Patch a private upstream snapshot, preserving each completed CSV row."""
from pathlib import Path
import argparse
MARKER='# AI_EDA_PER_VIP_CHECKPOINT_V2'
LEGACY='# AI_EDA_PER_VIP_CHECKPOINT_V1'
HELPER='''
# AI_EDA_PER_VIP_CHECKPOINT_V2
def checkpoint_rows(rows):
    from pathlib import Path
    report = Path(REPORT)
    merged = {}
    if report.exists():
        with report.open(newline="") as stream:
            for old in list(csv.reader(stream))[1:]:
                if old: merged[old[0]] = old
    for item in rows: merged[item[0]] = item
    temporary = report.with_name(report.name + ".checkpoint.tmp")
    with temporary.open("w", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["vip", "tests", "covered_tests"]
            + ["%s_%s" % (m, k) for m in METRICS for k in ("hit", "total", "pct")]
            + ["line_missed", "func_missed", "detail"])
        writer.writerows(merged[key] for key in sorted(merged))
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(report)

'''
def patched(text):
    if MARKER in text:return text
    if LEGACY in text:
        result=text.replace(LEGACY,MARKER)
    else:
        if text.count('def main(argv):')!=1 or text.count('            rows.append(rw)')!=1:raise ValueError('unexpected upstream coverage source')
        result=text.replace('def main(argv):',HELPER+'def main(argv):').replace('            rows.append(rw)','            rows.append(rw)\n            checkpoint_rows(rows)')
    start=result.index('    with open(REPORT, "w", newline="") as f:',result.index('def main(argv):'))
    end=result.index('    return 0 if all(',start)
    result=result[:start]+'    checkpoint_rows(rows)\n'+result[end:]
    compile(result,'coverage.py','exec');return result

def main():
    p=argparse.ArgumentParser();p.add_argument('snapshot',type=Path);a=p.parse_args()
    text=a.snapshot.read_text();updated=patched(text)
    if updated!=text:
        backup=a.snapshot.with_suffix(a.snapshot.suffix+'.before-checkpoint')
        if backup.exists():raise ValueError('backup already exists')
        backup.write_bytes(a.snapshot.read_bytes());a.snapshot.write_text(updated)
if __name__=='__main__':main()
