"""Record host and project container resource samples until an ISO deadline."""
import argparse
import datetime as dt
import json
import pathlib
import shutil
import subprocess
import time


def command(args):
    result = subprocess.run(args, capture_output=True, text=True, timeout=30, check=False)
    if result.returncode:
        return {"error": result.stderr.strip()[:500]}
    return result.stdout.strip()


def sample():
    memory = {}
    for line in pathlib.Path('/proc/meminfo').read_text().splitlines():
        key, value = line.split(':', 1)
        if key in {'MemTotal', 'MemAvailable', 'SwapTotal', 'SwapFree'}:
            memory[key + '_KiB'] = int(value.split()[0])
    disk = shutil.disk_usage('/')
    stats = command(['docker', 'stats', '--no-stream', '--format', '{{json .}}'])
    containers = ([json.loads(line) for line in stats.splitlines() if line]
                  if isinstance(stats, str) else stats)
    if isinstance(containers, list):
        containers = [row for row in containers if row.get('Name', '').startswith('opinion-')]
    return {'at': dt.datetime.now(dt.timezone.utc).isoformat(),
            'memory': memory, 'loadavg': pathlib.Path('/proc/loadavg').read_text().strip(),
            'cpu_ticks': pathlib.Path('/proc/stat').read_text().splitlines()[0],
            'disk_used_bytes': disk.used, 'disk_free_bytes': disk.free,
            'containers': containers,
            'states': command(['docker', 'ps', '-a', '--filter', 'name=opinion-',
                               '--format', '{{json .}}'])}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--until', required=True)
    parser.add_argument('--output', type=pathlib.Path, required=True)
    args = parser.parse_args()
    deadline = dt.datetime.fromisoformat(args.until)
    if deadline.tzinfo is None:
        parser.error('--until must include a timezone')
    args.output.mkdir(parents=True, exist_ok=True)
    while dt.datetime.now(dt.timezone.utc) <= deadline:
        try:
            row = sample()
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            row = {'at': dt.datetime.now(dt.timezone.utc).isoformat(), 'error': str(exc)}
        day = dt.datetime.now(dt.timezone(dt.timedelta(hours=8))).date().isoformat()
        with (args.output / f'resources-{day}.jsonl').open('a', encoding='utf-8') as stream:
            stream.write(json.dumps(row, ensure_ascii=False) + '\n')
        remaining = (deadline - dt.datetime.now(dt.timezone.utc)).total_seconds()
        if remaining <= 0:
            break
        time.sleep(min(60, remaining))


if __name__ == '__main__':
    main()
