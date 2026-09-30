"""Cached local neural inference. Runtime never downloads models or calls a paid API."""

from __future__ import annotations

import math
from collections import OrderedDict
from functools import lru_cache
from pathlib import Path
from threading import RLock

from app.core.config import settings

DEFAULT_MODEL_ROOT = Path(__file__).resolve().parents[2] / "data" / "models"
MODEL_IDS = {"embedding": "BAAI/bge-small-zh-v1.5", "reranker": "BAAI/bge-reranker-base"}


class SemanticUnavailable(RuntimeError):
    """Missing dependencies, missing weights, or an inference failure."""


class LocalSemanticModels:
    def __init__(self, root: Path, device: str = "cpu"):
        self.root, self.device = root, device
        self._encoder = self._reranker = None
        self._tokenizer = None
        self._vectors: OrderedDict = OrderedDict()
        self._scores: OrderedDict = OrderedDict()
        self._lock = RLock()
        self.calls = {"embedding_batches": 0, "rerank_batches": 0}

    def _load(self, kind):
        path = self.root / kind
        if not (path / "config.json").exists():
            raise SemanticUnavailable(f"{kind}_model_not_prepared")
        try:
            import torch
            from sentence_transformers import SentenceTransformer

            torch.set_num_threads(settings.semantic_cpu_threads)
            if kind == "embedding" and self._encoder is None:
                self._encoder = SentenceTransformer(
                    str(path), device=self.device, local_files_only=True, trust_remote_code=False
                )
                self._encoder.max_seq_length = 512
            if kind == "reranker" and self._reranker is None:
                import onnxruntime as ort
                from transformers import AutoTokenizer

                self._tokenizer = AutoTokenizer.from_pretrained(
                    str(path), local_files_only=True, trust_remote_code=False, use_fast=False
                )
                options = ort.SessionOptions()
                options.intra_op_num_threads = settings.semantic_cpu_threads
                options.inter_op_num_threads = 1
                self._reranker = ort.InferenceSession(
                    str(path / "onnx" / "model_quantized.onnx"),
                    sess_options=options,
                    providers=["CPUExecutionProvider"],
                )
        except Exception as exc:  # noqa: BLE001 - native runtime exceptions vary by provider.
            raise SemanticUnavailable(f"{kind}_load_failed:{type(exc).__name__}") from exc

    def encode(self, texts: list[str]):
        with self._lock:
            self._load("embedding")
            missing = list(dict.fromkeys(text for text in texts if text not in self._vectors))
            try:
                if missing:
                    vectors = self._encoder.encode(
                        missing, normalize_embeddings=True, batch_size=16, show_progress_bar=False
                    )
                    self.calls["embedding_batches"] += math.ceil(len(missing) / 16)
                    self._vectors.update(zip(missing, vectors, strict=True))
                result = [self._vectors[text] for text in texts]
                self._trim(self._vectors)
                return result
            except Exception as exc:  # noqa: BLE001 - explicit, observable model degradation.
                raise SemanticUnavailable(
                    f"embedding_inference_failed:{type(exc).__name__}"
                ) from exc

    def rerank(self, pairs: list[tuple[str, str]]) -> list[float]:
        with self._lock:
            self._load("reranker")
            missing = list(dict.fromkeys(pair for pair in pairs if pair not in self._scores))
            try:
                if missing:
                    scores = []
                    input_names = {entry.name for entry in self._reranker.get_inputs()}
                    for offset in range(0, len(missing), 8):
                        batch = missing[offset : offset + 8]
                        tokens = self._tokenizer(
                            [pair[0] for pair in batch],
                            [pair[1] for pair in batch],
                            padding=True,
                            truncation=True,
                            max_length=512,
                            return_tensors="np",
                        )
                        logits = self._reranker.run(
                            None, {key: val for key, val in tokens.items() if key in input_names}
                        )[0].reshape(-1)
                        scores.extend(
                            1 / (1 + math.exp(-max(-80, min(80, float(logit))))) for logit in logits
                        )
                    self.calls["rerank_batches"] += math.ceil(len(missing) / 8)
                    self._scores.update(zip(missing, map(float, scores), strict=True))
                result = [self._scores[pair] for pair in pairs]
                self._trim(self._scores)
                return result
            except Exception as exc:  # noqa: BLE001 - ONNX exceptions need not be RuntimeError.
                raise SemanticUnavailable(
                    f"reranker_inference_failed:{type(exc).__name__}"
                ) from exc

    @staticmethod
    def _trim(cache):
        while len(cache) > 4096:
            cache.popitem(last=False)


@lru_cache(maxsize=4)
def _runtime(root: str, device: str):
    return LocalSemanticModels(Path(root), device)


def get_semantic_models():
    return _runtime(
        settings.semantic_model_dir or str(DEFAULT_MODEL_ROOT), settings.semantic_device
    )
