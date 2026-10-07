"""Read-only browser acceptance against an already running local MVP.

Optional dependency: pip install -e .[ui]. Screenshots remain in ignored local data.
"""

import argparse
import json
from pathlib import Path

from playwright.sync_api import sync_playwright

from app.core.config import settings


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8000")
    parser.add_argument("--channel", default="msedge")
    parser.add_argument("--output", type=Path, default=Path("data/briefing-mvp/ui"))
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    if not settings.admin_token:
        raise ValueError("ADMIN_TOKEN must be configured locally")
    errors, failures = [], []
    with sync_playwright() as p:
        browser = p.chromium.launch(channel=args.channel, headless=True, timeout=30000)
        try:
            page = browser.new_page(viewport={"width": 1440, "height": 1000})
            page.set_default_timeout(15000)
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(args.url, wait_until="networkidle")
            page.locator("#token").fill(settings.admin_token)
            page.locator("#login-form button").click()
            page.locator("#workspace").wait_for(state="visible")
            page.screenshot(path=str(args.output / "dashboard-desktop.png"), full_page=True)
            if page.locator(".report-card").count():
                page.locator(".report-card button").first.click()
                frame = page.frame_locator("#report-preview")
                frame.locator("h1").wait_for()
                page.screenshot(path=str(args.output / "report-desktop.png"))
                links = frame.locator("a[href^='http']").count()
                assert links > 0, "Report must expose original source links"
            for tab in ("operations", "reviews", "deliveries"):
                page.locator(f"[data-tab='{tab}']").click()
                page.screenshot(path=str(args.output / f"{tab}-desktop.png"), full_page=True)
            page.set_viewport_size({"width": 390, "height": 844})
            page.locator("[data-tab='briefings']").click()
            page.screenshot(path=str(args.output / "dashboard-mobile.png"))
            outer = page.evaluate(
                "({width:innerWidth,scroll:document.documentElement.scrollWidth})"
            )
            if outer["scroll"] > outer["width"]:
                failures.append("mobile_outer_horizontal_overflow")
            if page.locator("#preview-panel").is_visible():
                page.locator("#preview-panel").evaluate("el => el.scrollIntoView({block:'start'})")
                page.screenshot(path=str(args.output / "report-mobile.png"))
                inner = page.frame_locator("#report-preview").locator("body").evaluate(
                    "el => ({width:innerWidth,scroll:document.documentElement.scrollWidth})"
                )
                if inner["scroll"] > inner["width"]:
                    failures.append("mobile_report_horizontal_overflow")
            result = {"page_errors": errors, "failures": failures, "mobile": outer}
            (args.output / "result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
            print(json.dumps(result))
            assert not errors and not failures, result
        finally:
            browser.close()


if __name__ == "__main__":
    main()
