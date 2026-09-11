"""Fetch minimal Weibo heat data from RSSHub and optional Weibo CLI enrichment."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from app.core.config import settings
from app.services.weibo_heat_client import WeiboHeatClient, WeiboHeatClientConfig, WeiboHeatError


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Fetch minimal Weibo heat data.")
    parser.add_argument("--base-url", default=settings.weibo_rsshub_base_url)
    parser.add_argument("--route", default=settings.weibo_rsshub_route)
    parser.add_argument("--limit", type=int, default=settings.weibo_rsshub_fetch_limit)
    parser.add_argument("--skip-top", type=int, default=settings.weibo_rsshub_skip_top)
    parser.add_argument("--with-cli", action="store_true", default=settings.weibo_cli_enabled)
    parser.add_argument("--cli-command", default=settings.weibo_cli_command)
    parser.add_argument("--cli-topic-limit", type=int, default=settings.weibo_cli_topic_limit)
    parser.add_argument("--cli-search-count", type=int, default=settings.weibo_cli_search_count)
    parser.add_argument("--include-cli-raw", action="store_true")
    parser.add_argument("--json", action="store_true", help="Print the full JSON result.")
    parser.add_argument("--output", help="Optional path to write the full JSON result.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    client = WeiboHeatClient(
        config=WeiboHeatClientConfig(
            rsshub_base_url=args.base_url,
            rsshub_route=args.route,
            rsshub_fetch_limit=args.limit,
            rsshub_skip_top=args.skip_top,
            rsshub_timeout_seconds=settings.weibo_rsshub_timeout_seconds,
            rsshub_max_retries=settings.weibo_rsshub_max_retries,
            cli_enabled=args.with_cli,
            cli_command=args.cli_command,
            cli_topic_limit=args.cli_topic_limit,
            cli_search_count=args.cli_search_count,
            cli_timeout_seconds=settings.weibo_cli_timeout_seconds,
        )
    )

    try:
        result = client.fetch_heat(with_cli=args.with_cli, include_cli_raw=args.include_cli_raw)
    except WeiboHeatError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    payload = result.model_dump(mode="json")
    if args.output:
        output_path = Path(args.output).resolve()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(f"RSSHub URL: {result.rsshub['url']}")
        print(f"Selected topics: {len(result.items)}")
        print(f"CLI enabled: {result.cli['enabled']}")
        for item in result.items[:10]:
            status = item.normalized["weibo_signal_status"]
            position = item.raw_metrics["list_position"]
            matched = item.raw_metrics["matched_status_count"]
            matched_text = "" if matched is None else f", cli_matches={matched}"
            print(f"{position}. {item.topic} [{status}{matched_text}]")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
