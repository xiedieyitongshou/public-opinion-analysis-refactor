"""Summarize recorded resource samples without contacting any external service."""
import argparse
import json
import re
from pathlib import Path


def size_bytes(text):
    match = re.fullmatch(r'([\d.]+)\s*([kMGT]?i?B)', text.strip())
    if not match:
        raise ValueError(text)
    units = {'B': 1, 'kB': 1000, 'MB': 1000**2, 'GB': 1000**3, 'TB': 1000**4,
             'KiB': 1024, 'MiB': 1024**2, 'GiB': 1024**3, 'TiB': 1024**4}
    return float(match[1]) * units[match[2]]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', type=Path, required=True)
    parser.add_argument('--session', required=True)
    args = parser.parse_args()
    summary = {'session': args.session, 'rounds': [], 'notes': [
        'Stage peaks include retained models and shared libraries; do not add them.',
        'cgroup bytes include file cache; Docker displayed memory subtracts inactive file cache.',
        'Model load RSS deltas include lazy imports/allocators, not isolated model weight size.',
        'Rounds use new processes and shared disk cache; rapid rounds are not trend validation.',
        'Container CPU percent uses 100% per core; Windows available includes other applications.',
    ]}
    host = [json.loads(x) for x in (
        args.directory / f'{args.session}-host-containers.jsonl').read_text(
            encoding='utf-8').splitlines()]
    summary['windows_min_available_GiB'] = min(
        x['windows_available_bytes'] for x in host) / 1024**3
    summary['simultaneous_container_peak_MiB'] = max(sum(
        size_bytes(c['MemUsage'].split('/')[0]) for c in x.get('containers', []))
        for x in host) / 1024**2
    summary['containers'] = {}
    for row in host:
        for c in row.get('containers', []):
            entry = summary['containers'].setdefault(c['Name'],
                {'peak_MiB': 0, 'peak_CPU_percent': 0})
            entry['peak_MiB'] = max(entry['peak_MiB'],
                                   size_bytes(c['MemUsage'].split('/')[0]) / 1024**2)
            entry['peak_CPU_percent'] = max(entry['peak_CPU_percent'],
                                           float(c['CPUPerc'].rstrip('%')))
    for name, entry in summary['containers'].items():
        samples = [c for row in host for c in row.get('containers', []) if c['Name'] == name]
        for field in ['NetIO', 'BlockIO']:
            first = [size_bytes(v) for v in samples[0][field].split('/')]
            last = [size_bytes(v) for v in samples[-1][field].split('/')]
            entry[field + '_delta_MiB'] = [(b - a) / 1024**2 for a, b in zip(first, last)]
    for number in [1, 2]:
        run_id = f'local-resource-{args.session}-r{number}'
        p = args.directory / f'{run_id}-samples.jsonl'
        if not p.exists():
            continue
        rows = [json.loads(x) for x in p.read_text(encoding='utf-8').splitlines()]
        result_path = args.directory / f'{run_id}-result.json'
        result = (json.loads(result_path.read_text(encoding='utf-8'))
                  if result_path.exists() else {})
        item = {'run_id': run_id, 'status': result.get('status', 'incomplete'),
                'seconds': rows[-1]['monotonic'] - rows[0]['monotonic'],
                'cpu_seconds': rows[-1]['cpu_seconds'] - rows[0]['cpu_seconds'],
                'process_peak_MiB': max(x.get('VmHWM', 0) for x in rows) / 1024**2,
                'cgroup_sample_peak_MiB': max(int(x.get('memory.current', 0))
                                              for x in rows) / 1024**2,
                'events': len(result.get('event_ids', [])),
                'complete': rows[-1]['kind'] == 'complete',
                'major_faults_delta': rows[-1]['major_faults'] - rows[0]['major_faults'],
                'swap_peak_MiB': max(int(x.get('memory.swap.current', 0)) for x in rows) / 1024**2,
                'sources': result.get('source_statuses', {}), 'stages': [], 'model_loads': []}
        active = {}
        for x in rows:
            if x['kind'] == 'stage_start':
                active[x['stage']] = x
            elif x['kind'] == 'stage_end':
                start = active.pop(x['stage'])
                group = [r for r in rows if start['monotonic'] <= r['monotonic'] <= x['monotonic']]
                item['stages'].append({'stage': x['stage'],
                    'seconds': x['monotonic'] - start['monotonic'],
                    'cpu_seconds': x['cpu_seconds'] - start['cpu_seconds'],
                    'process_peak_MiB': max(r.get('VmRSS', 0) for r in group) / 1024**2,
                    'cgroup_peak_MiB': max(int(r.get('memory.current', 0))
                                           for r in group) / 1024**2})
            if x['kind'] == 'model_start' and str(x['model']).startswith('_load:'):
                active[x['model']] = x
            elif x['kind'] == 'model_end' and str(x['model']).startswith('_load:'):
                start = active.pop(x['model'])
                item['model_loads'].append({'model': x['model'],
                    'seconds': x['monotonic'] - start['monotonic'],
                    'before_MiB': start['VmRSS'] / 1024**2,
                    'after_MiB': x['VmRSS'] / 1024**2,
                    'delta_MiB': (x['VmRSS'] - start['VmRSS']) / 1024**2})
        summary['rounds'].append(item)
    out = args.directory / f'{args.session}-summary.json'
    out.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
