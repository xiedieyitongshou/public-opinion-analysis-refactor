"""Explicit live smoke test; saves a local capture separately from offline reports."""

import argparse
import json
import sys
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.official_search import ENDPOINTS, OfficialSearchClient  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--queries",
        nargs="+",
        default=["常德 养老金 拾荒", "张雪 机车 被盗", "王楚钦 阿拉米扬", "王曼昱 蒯曼 11比0"],
    )
    parser.add_argument("--sources", nargs="+", choices=list(ENDPOINTS), default=list(ENDPOINTS))
    parser.add_argument("--name", default="official-search-upgrade-20260929")
    args = parser.parse_args()
    output = Path(__file__).resolve().parents[2] / "notes" / "source-probe-raw"
    output.mkdir(parents=True, exist_ok=True)
    client = OfficialSearchClient()
    captures = []
    for query in args.queries:
        for source in args.sources:
            result = client.search(query, source=source, limit=5)
            value = asdict(result)
            value["items"] = [item.model_dump(mode="json") for item in result.items]
            captures.append(value)
            print(json.dumps(result.audit(), ensure_ascii=True), flush=True)
    path = output / f"{args.name}.json"
    path.write_text(
        json.dumps(
            {"captured_at": datetime.now(UTC).isoformat(), "results": captures},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(path)


if __name__ == "__main__":
    main()
