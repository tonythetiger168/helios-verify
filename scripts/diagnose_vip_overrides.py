"""Container diagnostic: test a minimal UVM collision fix in private copies."""
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

root = Path('/work/open-vip-portfolio')
out = Path('/results/override-diagnostic')
out.mkdir(exist_ok=True)
results = []
for name, relative in [('HyperBus-UVM-VIP', 'hyperbus/hyperbus_monitor.svh'),
                       ('xSPI-UVM-VIP', 'xspi/xspi_monitor.svh')]:
    with tempfile.TemporaryDirectory(prefix='vip-override-') as temp:
        copy = Path(temp) / name
        shutil.copytree(root / 'build/vips' / name, copy)
        source = copy / relative
        original = source.read_text()
        patched, count = re.subn(r'\bbuild\s*\(', 'assemble_transaction(', original)
        if count < 2:
            raise RuntimeError('Expected helper declaration and calls')
        logs = {}
        for stage, text in [('before', original), ('after', patched)]:
            source.write_text(text)
            p = subprocess.run(['/opt/eda-python/bin/python', 'tools/elab_scan.py', str(copy)],
                               cwd=root, env={**os.environ, 'ELAB_STRICT':'1',
                                              'UVM_CORE':'/opt/uvm-core/src'},
                               text=True, capture_output=True, timeout=90)
            (out / f'{name}-{stage}.log').write_text(p.stdout + p.stderr)
            match = re.search(r'^ELAB_ERRORS=(\d+)$', p.stdout, re.M)
            if p.returncode or not match:
                raise RuntimeError('Diagnostic process failed')
            logs[stage] = int(match[1])
        if logs['before'] < 1 or logs['after'] != 0:
            raise RuntimeError(f'Expected fail-to-clean elaboration: {logs}')
        (out / f'{name}-monitor.svh').write_text(patched)
        results.append(dict(name=name, replacements=count, errors=logs,
                            original_sha256=hashlib.sha256(original.encode()).hexdigest(),
                            scope='strict elaboration only; not dynamic UVM validation'))
(out / 'report.json').write_text(json.dumps(results, indent=2))
print(json.dumps(results, indent=2))
