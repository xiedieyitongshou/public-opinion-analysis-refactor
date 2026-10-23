"""Read-only matching audit; no collection, delivery, DB writes or production cache writes."""
import json
from pathlib import Path

from sqlalchemy import select

from app.core.config import settings
settings.semantic_cache_path = ""
settings.semantic_cpu_threads = 1

from app.db.session import SessionLocal
from app.models import AgentTask, Event, HumanReviewTask, Item
from app.schemas import EventSignal, EventMatchConfig
from app.agents.event_matcher import EventMatcher
from app.services.analysis_interfaces import _candidate
from app.services.match_documents import event_document
from app.services.matching_engine import compare_many, retrieve
from app.services.topic_content import topic_context, topic_excerpts

PREFIX = "overnight-4dd38676c8694086-"
OUTPUT = Path('/tmp/oct23-cli-refresh-audit.json')


def main():
    matcher = EventMatcher()
    output = {'reviews': [], 'discovery': []}
    with SessionLocal() as db:
        tasks = [t for t in db.scalars(select(AgentTask).where(
            AgentTask.task_type == 'match_and_resolve_events').order_by(AgentTask.id))
            if (t.input_json or {}).get('run_id', '').startswith(PREFIX)]
        config = EventMatchConfig.model_validate(tasks[-1].input_json['match_config'])
        latest, raw_by_title = {}, {}
        hashes = set()
        for t in tasks:
            sources = {s['source_signal_id']: s for s in t.input_json['source_signals']}
            for raw in t.input_json['normalized_items']:
                if raw.get('platform') in ('zhihu', 'weibo') and raw.get('signal_role') != 'search_enrichment_signal':
                    raw_by_title[raw['title']] = raw
                    hashes.add(raw['content_hash'])
            for sig in t.input_json['event_signals']:
                src = [sources[s] for s in sig['source_signal_ids'] if s in sources]
                if any(s['platform'] in ('zhihu','weibo') and s['signal_role'] != 'search_enrichment_signal' for s in src):
                    latest[sig['title']] = sig
        events = {e.event_id:e for e in db.scalars(select(Event))}
        current_ids, owners = set(), {}
        for item in db.scalars(select(Item)):
            keys = {item.content_hash,(item.normalized_json or {}).get('collector_content_hash')}
            if keys & hashes and item.event:
                current_ids.add(item.event.event_id)
                owners.setdefault(item.title,set()).add(item.event.event_id)
        candidates = {eid:_candidate(e) for eid,e in events.items()}

        def enrich(sig):
            signal = EventSignal.model_validate(sig)
            raw = raw_by_title.get(signal.title)
            if raw and raw.get('platform') == 'weibo':
                signal = signal.model_copy(update={'summary':topic_context(raw),
                    'has_accepted_cli_context':bool(topic_excerpts(raw))})
            return signal

        def evaluate(signal, cs):
            comparisons = compare_many(event_document(signal),[event_document(c) for c in cs],
                use_embedding=True,use_reranker=True)
            rows=[]
            for c,comparison in zip(cs,comparisons):
                ranked=matcher.rerank_candidate(signal=signal,candidate=c,config=config,comparison=comparison)
                action,reason,_=matcher._decide(ranked,signal=signal,config=config)
                rows.append({'target_id':c.event_id,'target_title':c.title,'target_body':c.summary,
                    'target_cli':c.has_accepted_cli_context,'action_pairwise':action,'reason':reason,
                    'confidence':ranked.confidence,'features':ranked.features.model_dump(mode='json')})
            return sorted(rows,key=lambda r:(r['confidence'],r['features']['rerank_score'] or 0),reverse=True)

        reviews=[r for r in db.scalars(select(HumanReviewTask))
            if (r.payload_json or {}).get('run_id','').startswith(PREFIX)
            and (r.payload_json or {}).get('candidate_event_id') in candidates]
        for r in reviews:
            p=r.payload_json
            signal=enrich(p['event_signal'])
            row={'review':r.id,'status':r.status,'title':signal.title,'body':signal.summary,
                'cli':signal.has_accepted_cli_context,'old':p.get('match_features_json'),
                'results':evaluate(signal,[candidates[p['candidate_event_id']]])}
            output['reviews'].append(row)
            OUTPUT.write_text(json.dumps(output,ensure_ascii=False),encoding='utf-8')
            print('review',r.id,row['results'][0]['action_pairwise'],flush=True)
        pool=[candidates[eid] for eid in sorted(current_ids)]
        for n,(title,sig) in enumerate(latest.items(),1):
            signal=enrich(sig)
            cs=[c for c in pool if c.event_id not in owners.get(title,set()) and c.title != title]
            selected,_=retrieve(event_document(signal),[event_document(c) for c in cs],limit=20,use_embedding=True)
            rows=evaluate(signal,[cs[i] for i in selected])
            output['discovery'].append({'title':title,'body':signal.summary,
                'cli':signal.has_accepted_cli_context,'platforms':signal.platforms,
                'owners':sorted(owners.get(title,set())),'results':rows})
            OUTPUT.write_text(json.dumps(output,ensure_ascii=False),encoding='utf-8')
            print('topic',n,'/',len(latest),title,flush=True)
        output['counts']={'reviews':len(reviews),'topics':len(latest),'candidate_events':len(pool)}
        OUTPUT.write_text(json.dumps(output,ensure_ascii=False),encoding='utf-8')


if __name__ == '__main__':
    main()
