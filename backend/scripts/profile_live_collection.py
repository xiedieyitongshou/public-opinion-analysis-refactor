"""Opt-in live resource profiling, optional local report, never email dispatch.

Run each round in a new process, sharing the isolated DB and semantic disk cache.
Stage/model peaks include resident shared dependencies; they are not additive.
"""
import argparse
import json
import os
import resource
import threading
import time
from datetime import UTC, datetime
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-id', required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--local-report', action='store_true')
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    state = {'stage': 'imports', 'model': None}
    stop = threading.Event()
    lock = threading.Lock()
    stream = (args.output / f'{args.run_id}-samples.jsonl').open('w', encoding='utf-8')

    def snapshot():
        usage = resource.getrusage(resource.RUSAGE_SELF)
        status = {}
        for line in Path('/proc/self/status').read_text().splitlines():
            if line.startswith(('VmRSS:', 'VmHWM:')):
                key, value = line.split(':', 1)
                status[key] = int(value.split()[0]) * 1024
        row = {'at': datetime.now(UTC).isoformat(), 'monotonic': time.monotonic(),
               'stage': state['stage'], 'model': state['model'], 'pid': os.getpid(),
               'cpu_seconds': usage.ru_utime + usage.ru_stime,
               'major_faults': usage.ru_majflt, **status}
        for name in ['memory.current', 'memory.peak', 'memory.swap.current', 'memory.stat',
                     'memory.events', 'cpu.stat', 'io.stat', 'memory.pressure']:
            p = Path('/sys/fs/cgroup') / name
            if p.exists():
                row[name] = p.read_text().strip()
        return row

    def emit(kind='sample', **extra):
        with lock:
            row = snapshot()
            row.update(kind=kind, **extra)
            stream.write(json.dumps(row, ensure_ascii=False) + '\n')
            stream.flush()
            return row

    def monitor():
        while not stop.wait(0.25):
            emit()

    thread = threading.Thread(target=monitor, daemon=True)
    emit('start')
    thread.start()
    try:
        from app.agents.analysis import HotspotAnalysisAgent
        from app.core.config import settings
        from app.db.init_db import init_db
        from app.db.session import SessionLocal
        from app.services.briefing_jobs import default_analysis_input
        from app.services.request_usage import request_scope
        from app.services.runtime_logging import configure_logging
        from app.services.semantic_models import LocalSemanticModels
        from app.tools import ToolRegistry

        assert not settings.scheduler_enabled and not settings.daily_briefing_enabled
        assert not settings.email_schedule_enabled
        configure_logging(args.run_id)
        init_db()
        original_call = ToolRegistry.call

        def measured_call(self, name, *a, **kw):
            previous = state['stage']
            state['stage'] = name
            emit('stage_start')
            try:
                return original_call(self, name, *a, **kw)
            finally:
                emit('stage_end')
                state['stage'] = previous

        ToolRegistry.call = measured_call
        for method in ['_load', 'encode', 'rerank']:
            original = getattr(LocalSemanticModels, method)

            def wrapped(self, *a, _method=method, _original=original, **kw):
                if _method == '_load':
                    attr = '_encoder' if a[0] == 'embedding' else '_reranker'
                    if getattr(self, attr) is not None:
                        return _original(self, *a, **kw)
                previous = state['model']
                state['model'] = _method + (':' + a[0] if _method == '_load' else '')
                emit('model_start')
                try:
                    return _original(self, *a, **kw)
                finally:
                    emit('model_end')
                    state['model'] = previous

            setattr(LocalSemanticModels, method, wrapped)
        data = default_analysis_input(args.run_id)
        state['stage'] = 'analysis'
        started_at = datetime.now(UTC)
        with SessionLocal() as db:
            with request_scope(data.run_id, settings.request_limits,
                               settings.job_timeout_seconds) as scope:
                try:
                    result = HotspotAnalysisAgent().run(db, data)
                    (args.output / f'{args.run_id}-result.json').write_text(
                        result.model_dump_json(indent=2), encoding='utf-8')
                    print(json.dumps({'run_id': data.run_id, 'status': result.status,
                                      'events': len(result.event_ids),
                                      'sources': result.source_statuses}), flush=True)
                    from app.models.briefing import AnalysisRun
                    from app.services.briefing_jobs import capture_collection
                    row = AnalysisRun(
                        run_id=data.run_id, status=result.status, started_at=started_at,
                        finished_at=datetime.now(UTC), window_end=result.window_end,
                        input_json=data.model_dump(mode='json'),
                        output_json=result.model_dump(mode='json'),
                        collection_json=capture_collection(db, result),
                    )
                    db.add(row)
                    db.commit()
                    if args.local_report:
                        from app.services.briefing import generate_briefing, save_briefing
                        from app.services.briefing_render import render_report, render_text
                        from app.schemas.display import DailyBriefing
                        state['stage'] = 'local_daily_report'
                        emit('stage_start')
                        try:
                            report = save_briefing(db, generate_briefing(db, data.run_id))
                            row.report_id = report.id
                            db.commit()
                            briefing = DailyBriefing.model_validate(report.briefing_json)
                            for suffix, content in [
                                ('html', render_report(briefing, status=report.status)),
                                ('txt', render_text(briefing)),
                                ('json', briefing.model_dump_json(indent=2)),
                            ]:
                                (args.output / f'{args.run_id}-daily-report.{suffix}').write_text(
                                    content, encoding='utf-8')
                            print(json.dumps({'report_id': report.id, 'status': report.status,
                                              'email_sent': False}), flush=True)
                        finally:
                            emit('stage_end')
                            state['stage'] = 'analysis'
                finally:
                    db.rollback()
                    scope.flush(db)
        emit('complete')
    finally:
        stop.set()
        thread.join(timeout=2)
        stream.close()


if __name__ == '__main__':
    main()
