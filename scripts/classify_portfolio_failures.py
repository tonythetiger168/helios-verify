"""Annotate saved failures from log evidence without overwriting original results."""
import argparse
from collections import Counter
import gzip
import json
from pathlib import Path


def classify(paths, timed_out=False):
    evidence = []
    internal = []
    missing = []
    for path in paths:
        opener = gzip.open if path.suffix == '.gz' else open
        with opener(path, 'rt', errors='replace') as stream:
            for line in stream:
                if 'Killed signal terminated program cc1plus' in line:
                    evidence.append({'path': str(path), 'line': line.strip()[:500]})
                if '%Error' in line and ('Internal Error' in line or 'Internal: Unexpected Call' in line):
                    internal.append({'path': str(path), 'line': line.strip()[:500]})
                if line.startswith('UVM_SMOKE_NONE '):
                    missing.append({'path': str(path), 'line': line.strip()[:500]})
    if evidence:
        return 'compiler_killed_resource_suspected', evidence
    if internal:
        return 'tool_internal_error', internal
    if missing:
        return 'no_discovered_uvm_test', missing
    return ('timeout' if timed_out else 'unclassified'), evidence


def main():
    p = argparse.ArgumentParser()
    p.add_argument('root', type=Path)
    args = p.parse_args()
    state = json.loads((args.root/'progress.json').read_text())
    rows = []
    for phase in ('vip-uvm', 'ip-coverage'):
        for item in state['results'].get(phase, []):
            if item['status'] in ('pass', 'closed'):
                continue
            folder = args.root / phase / item['name']
            paths = list(folder.rglob('*.log')) + list(folder.rglob('*.log.gz'))
            reason, evidence = classify(paths, item.get('timeout', False))
            rows.append(dict(phase=phase, name=item['name'], original_status=item['status'],
                             reason=reason, evidence=evidence, logs_scanned=len(paths)))
    result = dict(note='Compiler SIGKILL is observed; OOM is suspected, not proven without OS/cgroup evidence. Original statuses unchanged.',
                  summary=dict(Counter(r['reason'] for r in rows)), results=rows)
    (args.root/'failure-classification.json').write_text(json.dumps(result, indent=2))
    print(json.dumps(result['summary']))


if __name__ == '__main__':
    main()
