"""Read-only local inference ablation; never reads or writes production score cache."""

import json
import math
import re
from pathlib import Path

import onnxruntime as ort
from sqlalchemy import select
from transformers import AutoTokenizer

from app.core.config import settings
from app.db.session import SessionLocal
from app.models import Event, HumanReviewTask
from app.schemas import EventSignal
from app.services.analysis_interfaces import _candidate
from app.services.match_documents import event_document


def main():
    root = Path(settings.semantic_model_dir) / "reranker"
    slow = AutoTokenizer.from_pretrained(str(root), local_files_only=True, use_fast=False)
    fast = AutoTokenizer.from_pretrained(str(root), local_files_only=True, use_fast=True)
    opts = ort.SessionOptions()
    opts.intra_op_num_threads = 1
    opts.inter_op_num_threads = 1
    model = ort.InferenceSession(
        str(root / "onnx/model_quantized.onnx"),
        sess_options=opts,
        providers=["CPUExecutionProvider"],
    )
    names = {entry.name for entry in model.get_inputs()}
    print(
        json.dumps(
            {"slow": type(slow).__name__, "fast": type(fast).__name__, "inputs": list(names)},
            ensure_ascii=False,
        ),
        flush=True,
    )

    def run(label, pairs, tokenizer=slow, padding=True):
        tokens = tokenizer(
            [a for a, b in pairs],
            [b for a, b in pairs],
            padding=padding,
            truncation=True,
            max_length=512,
            return_tensors="np",
        )
        logits = model.run(None, {k: v for k, v in tokens.items() if k in names})[0].reshape(-1)
        print(
            json.dumps(
                {
                    "test": label,
                    "shape": list(tokens["input_ids"].shape),
                    "lengths": tokens["attention_mask"].sum(axis=1).tolist(),
                    "logits": logits.tolist(),
                    "scores": [1 / (1 + math.exp(-float(v))) for v in logits],
                },
                ensure_ascii=False,
            ),
            flush=True,
        )

    originals = []
    with SessionLocal() as db:
        for review_id in [48, 49]:
            payload = db.get(HumanReviewTask, review_id).payload_json
            incoming = EventSignal.model_validate(payload["event_signal"])
            event = db.scalar(select(Event).where(Event.event_id == payload["candidate_event_id"]))
            candidate = _candidate(event)
            a, b = event_document(incoming), event_document(candidate)
            pair = (a.text, b.text)
            originals.append(pair)
            print(
                json.dumps(
                    {
                        "review": review_id,
                        "saved_score": payload["match_features_json"]["rerank_score"],
                        "left": a.text,
                        "right": b.text,
                        "same_token_ids": slow(*pair)["input_ids"] == fast(*pair)["input_ids"],
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )
            run(f"{review_id}:full_single", [pair])
            run(f"{review_id}:full_fast", [pair], tokenizer=fast)
            run(f"{review_id}:titles", [(a.title, b.title)])
            run(f"{review_id}:descriptions", [(a.summary, b.summary)])
            run(
                f"{review_id}:no_image_markers",
                [(re.sub(r"\[图片\]", "", a.text), re.sub(r"\[图片\]", "", b.text))],
            )
            run(f"{review_id}:reversed", [(b.text, a.text)])
            run(f"{review_id}:fixed_padding", [pair], padding="max_length")
            run(f"{review_id}:identical", [(a.text, a.text)])
            run(f"{review_id}:title_to_full", [(a.title, b.text)])
            run(f"{review_id}:full_to_title", [(a.text, b.title)])
            run(f"{review_id}:description_to_full", [(a.summary, b.text)])
            unrelated = "The football team won the match yesterday."
            run(f"{review_id}:full_to_unrelated", [(a.text, unrelated)])
            run(f"{review_id}:title_to_unrelated", [(a.title, unrelated)])
            prefix = slow.decode(slow.encode(a.text, add_special_tokens=False)[:80])
            run(f"{review_id}:first80tokens_to_full", [(prefix, b.text)])
    run("full_pairs_batched", originals)
    long_pair = ("这是一段用于检查批次填充影响的文本。" * 60, "与事件判断无关的长度控制。" * 60)
    run("with_long_control", [*originals, long_pair])
    run("with_long_control_reordered", [long_pair, *reversed(originals)])


if __name__ == "__main__":
    main()
