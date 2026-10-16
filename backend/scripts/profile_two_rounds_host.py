"""Windows host controller for the isolated resource-test Compose project."""
import argparse
import ctypes
import json
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / 'data' / 'resource-test'
UTC = timezone.utc
BACKEND = 'opinion-resource-test-backend-1'
RSSHUB = 'opinion-resource-test-rsshub-1'


class MemoryStatus(ctypes.Structure):
    _fields_ = [('length', ctypes.c_ulong), ('load', ctypes.c_ulong)] + [
        (name, ctypes.c_ulonglong) for name in
        ['total', 'available', 'page_total', 'page_available', 'virtual_total',
         'virtual_available', 'extended_available']]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--session', default=datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ'))
    parser.add_argument('--local-report', action='store_true')
    args = parser.parse_args()
    if not args.session.replace('-', '').replace('_', '').isalnum():
        parser.error('session must contain only letters, digits, hyphens or underscores')
    ROOT.mkdir(parents=True, exist_ok=True)
    with (ROOT / f'{args.session}-host-containers.jsonl').open('x', encoding='utf-8') as metrics:
        def sample(round_name):
            memory = MemoryStatus()
            memory.length = ctypes.sizeof(memory)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(memory))
            row = {'at': datetime.now(UTC).isoformat(), 'round': round_name,
                   'windows_available_bytes': memory.available,
                   'windows_total_bytes': memory.total}
            try:
                r = subprocess.run(['docker', 'stats', '--no-stream', '--format', '{{json .}}',
                                    BACKEND, RSSHUB], capture_output=True, text=True,
                                   timeout=12, check=False)
                row['containers'] = [json.loads(x) for x in r.stdout.splitlines() if x]
                if r.returncode:
                    row['error'] = r.stderr[:500]
            except subprocess.TimeoutExpired:
                row['error'] = 'docker_stats_timeout'
            metrics.write(json.dumps(row) + '\n')
            metrics.flush()
            return memory.available

        if sample('baseline') < 2.5 * 1024**3:
            raise RuntimeError('Preflight: free at least 2.5 GiB Windows memory before testing')
        for number in [1, 2]:
            run_id = f'local-resource-{args.session}-r{number}'
            started = time.monotonic()
            print('Starting', run_id, flush=True)
            with (ROOT / f'{run_id}-console.log').open('w', encoding='utf-8') as log:
                worker = subprocess.Popen([
                    'docker', 'exec', '-e', 'PYTHONPATH=/app',
                    '-e', f'DATABASE_URL=sqlite:////app/data/{args.session}.db',
                    '-e', f'SEMANTIC_CACHE_PATH=/app/data/{args.session}-cache.db', BACKEND,
                    'python', '/app/profile-scripts/profile_live_collection.py',
                    '--run-id', run_id, '--output', '/app/profiles',
                    *(['--local-report'] if args.local_report and number == 2 else [])],
                    stdout=log, stderr=subprocess.STDOUT)
                while worker.poll() is None:
                    available = sample(run_id)
                    if available < 600 * 1024**2 or time.monotonic() - started > 1800:
                        subprocess.run(['docker', 'stop', '-t', '10', BACKEND],
                                       timeout=25, check=False)
                        worker.wait(timeout=20)
                        raise RuntimeError('Stopped: host low memory or 30-minute round deadline')
                    time.sleep(2)
            print('Finished', run_id, 'exit', worker.returncode,
                  'seconds', round(time.monotonic() - started, 2), flush=True)
            if worker.returncode:
                raise RuntimeError('Round failed; inspect console log before retrying')
            sample('between_rounds' if number == 1 else 'finished')
            time.sleep(5)


if __name__ == '__main__':
    main()
