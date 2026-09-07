"""Low-frequency smoke test for the Zhihu Data Open Platform hot_list API.

Required environment:
    ZHIHU_ACCESS_SECRET=<your access secret>

Optional environment:
    ZHIHU_API_BASE_URL=https://developer.zhihu.com
    ZHIHU_FETCH_LIMIT=10
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.core.config import settings
from app.services.zhihu_client import ZhihuClient, normalize_hot_list_items


def main() -> None:
    parser = argparse.ArgumentParser(description="Probe Zhihu official hot_list API.")
    parser.add_argument("--limit", type=int, default=settings.zhihu_fetch_limit)
    parser.add_argument(
        "--raw-output",
        default="../notes/source-probe-raw/zhihu-hot-list-smoke.json",
    )
    parser.add_argument(
        "--search-query",
        default=None,
        help="Optional low-frequency zhihu_search smoke query.",
    )
    args = parser.parse_args()

    client = ZhihuClient()
    result = client.fetch_hot_list(limit=args.limit)
    quota = client.fetch_quota()
    search_payload = None
    if args.search_query:
        search_payload = client.search(args.search_query, count=1).raw_payload

    normalized = normalize_hot_list_items(result)

    output_path = Path(args.raw_output).resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(
            {
                "fetched_at": result.fetched_at.isoformat(),
                "total": result.total,
                "item_count": len(result.items),
                "quota": quota.raw_payload,
                "search_probe": search_payload,
                "normalized_samples": normalized,
                "raw_payload": result.raw_payload,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    print(f"Zhihu hot_list items: {len(result.items)}")
    print(f"Zhihu quota: {quota.raw_payload}")
    if search_payload:
        print("Zhihu search probe: ok")
    print(f"Raw local sample: {output_path}")
    for item in normalized[:5]:
        print(f"{item['raw_metrics']['rank']}. {item['title']} - {item['url']}")


if __name__ == "__main__":
    main()
