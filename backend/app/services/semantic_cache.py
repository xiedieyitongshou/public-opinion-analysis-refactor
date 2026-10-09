"""Exact neural score cache across bounded workers; never cache business decisions."""

import hashlib
import json
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path


class RerankCache:
    def __init__(self, path, model_root):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        root = Path(model_root)
        identity = {"algorithm": "onnx-cpu-sigmoid-512-v1"}
        for name in ("manifest.json", "reranker/config.json", "reranker/tokenizer_config.json",
                     "reranker/tokenizer.json", "reranker/vocab.txt",
                     "reranker/special_tokens_map.json",
                     "reranker/sentencepiece.bpe.model", "reranker/onnx/model_quantized.onnx"):
            file = root / name
            if file.exists():
                stat = file.stat()
                identity[name] = [stat.st_size, stat.st_mtime_ns]
                if file.suffix == ".json":
                    identity[name].append(hashlib.sha256(file.read_bytes()).hexdigest())
        self.namespace = json.dumps(identity, sort_keys=True)
        with self.connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS scores "
                       "(key TEXT PRIMARY KEY, score REAL NOT NULL, created REAL NOT NULL)")
            db.execute("DELETE FROM scores WHERE created < ?", (time.time() - 14 * 86400,))
            db.execute("DELETE FROM scores WHERE key IN "
                       "(SELECT key FROM scores ORDER BY created DESC LIMIT -1 OFFSET 100000)")

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=30)
        try:
            with db:
                yield db
        finally:
            db.close()

    def key(self, pair):
        return hashlib.sha256(json.dumps(
            [self.namespace, *pair], ensure_ascii=False, separators=(",", ":"),
        ).encode()).hexdigest()

    def get_many(self, pairs):
        keys = {self.key(pair): pair for pair in pairs}
        found = {}
        with self.connect() as db:
            for start in range(0, len(keys), 500):
                batch = list(keys)[start:start + 500]
                placeholders = ",".join("?" for _ in batch)
                for key, score in db.execute(
                    f"SELECT key, score FROM scores WHERE key IN ({placeholders})", batch
                ):
                    found[keys[key]] = score
        return found

    def put_many(self, pairs, scores):
        with self.connect() as db:
            db.executemany("INSERT OR REPLACE INTO scores VALUES (?, ?, ?)",
                           [(self.key(pair), score, time.time())
                            for pair, score in zip(pairs, scores, strict=True)])
