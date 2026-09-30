"""Explicit one-time download of pinned, local-only runtime models."""

import argparse
import json
from pathlib import Path

from huggingface_hub import HfApi, snapshot_download

MODELS = {"embedding": "BAAI/bge-small-zh-v1.5", "reranker": "Xenova/bge-reranker-base"}
REVISIONS = {
    "embedding": "7999e1d3359715c523056ef9478215996d62a620",
    "reranker": "280bcc27a84e0b898c251e06fddb25171bd9b101",
}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-dir", type=Path, default=Path(__file__).resolve().parents[1] / "data" / "models"
    )
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    manifest = {}
    for kind, model_id in MODELS.items():
        info = HfApi().model_info(model_id, revision=REVISIONS[kind])
        files = [entry.rfilename for entry in info.siblings]
        weights = "model.safetensors" if "model.safetensors" in files else "pytorch_model.bin"
        if kind == "reranker":
            weights = "onnx/model_quantized.onnx"
        snapshot_download(
            model_id,
            revision=info.sha,
            local_dir=args.output_dir / kind,
            allow_patterns=[weights, "*.json", "*.txt", "*.model", "1_Pooling/*"],
            ignore_patterns=["openvino/*"],
            max_workers=2,
        )
        manifest[kind] = {
            "model_id": model_id,
            "revision": info.sha,
            "weights": weights,
            "base_model": "BAAI/bge-reranker-base" if kind == "reranker" else model_id,
        }
        print(f"Prepared {kind}: {model_id}@{info.sha}", flush=True)
    (args.output_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()
