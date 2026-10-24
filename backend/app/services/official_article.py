"""Bounded official article extraction and source-grounded sentence summaries."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from html.parser import HTMLParser
from pathlib import Path
from tempfile import NamedTemporaryFile
from time import monotonic, time
from urllib.parse import urljoin, urlsplit, urlunsplit

import httpx
from sqlalchemy.engine import make_url

from app.core.config import settings
from app.services.request_usage import RequestBudgetExceeded, measured_call

VERSION = "official-extract-v1"
DOMAINS = {
    "chinanews": ("chinanews.com.cn", "chinanews.com", "chinanews.cn"),
    "people": ("people.com.cn", "people.cn"),
    "xinhua": ("news.cn", "xinhuanet.com"),
}
CONTAINERS = {
    "left_zw",
    "leftzw",
    "rm_txt_con",
    "article-content",
    "article_content",
    "articlecontent",
    "content_area",
    "main-aticle",
    "xlcontent",
    "main-content",
    "rich_media_content",
    "article-body",
    "articlebody",
    "detail",
}
NOISE = re.compile(
    r"^(?:责任编辑|相关阅读|推荐阅读|版权声明|扫码|分享到|广告|返回顶部|"
    r"总监制|监制[丨：:]|主编[丨：:]|编辑[丨：:&]|制图[丨：:]|策划[丨：:])"
)
MAX_BYTES = 2_000_000


def clean(text):
    return re.sub(r"\s+", " ", text or "").strip()


def safe_url(url, platform):
    try:
        p = urlsplit(url or "")
        host = (p.hostname or "").lower()
        if (
            p.scheme not in {"http", "https"}
            or p.username
            or p.password
            or p.port not in {None, 80, 443}
            or not any(host == d or host.endswith("." + d) for d in DOMAINS.get(platform, ()))
        ):
            return None
        return urlunsplit((p.scheme, p.netloc, p.path, p.query, ""))
    except ValueError:
        return None


@dataclass
class Node:
    tag: str
    attrs: dict = field(default_factory=dict)
    children: list = field(default_factory=list)

    def text(self):
        return clean(" ".join(c.text() if isinstance(c, Node) else c for c in self.children))

    def walk(self):
        yield self
        for child in self.children:
            if isinstance(child, Node):
                yield from child.walk()


class ArticleParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = Node("root")
        self.stack = [self.root]

    def handle_starttag(self, tag, attrs):
        node = Node(tag, dict(attrs))
        self.stack[-1].children.append(node)
        if tag not in {"meta", "link", "img", "br", "hr", "input", "source", "wbr", "area"}:
            self.stack.append(node)

    def handle_endtag(self, tag):
        for i in range(len(self.stack) - 1, 0, -1):
            if self.stack[i].tag == tag:
                del self.stack[i:]
                break

    def handle_data(self, data):
        if not any(
            n.tag in {"script", "style", "nav", "footer", "aside", "noscript"} for n in self.stack
        ):
            self.stack[-1].children.append(data)


def grams(text):
    text = re.sub(r"\W", "", text).lower()
    return {text[i : i + 2] for i in range(len(text) - 1)}


def extract_article(html, expected_title):
    parser = ArticleParser()
    parser.feed(html)
    nodes = list(parser.root.walk())
    meta = {
        (n.attrs.get("property") or n.attrs.get("name") or "").lower(): n.attrs.get("content", "")
        for n in nodes
        if n.tag == "meta"
    }
    title = clean(
        meta.get("og:title")
        or next((n.text() for n in nodes if n.tag == "h1" and n.text()), "")
        or next((n.text() for n in nodes if n.tag == "title" and n.text()), "")
    )
    if not title or len(grams(title) & grams(expected_title)) < min(2, len(grams(expected_title))):
        raise ValueError("article_title_mismatch")
    containers = [
        n
        for n in nodes
        if n.tag == "article"
        or ({n.attrs.get("id", "").lower(), *n.attrs.get("class", "").lower().split()} & CONTAINERS)
    ]
    candidates = []
    for node in containers:
        paragraphs = [n.text() for n in node.walk() if n.tag == "p"]
        if not paragraphs:
            paragraphs = [node.text()]
        paragraphs = list(
            dict.fromkeys(p for p in paragraphs if len(p) >= 15 and not NOISE.search(p))
        )
        if paragraphs:
            candidates.append(paragraphs)
    if not candidates:
        raise ValueError("article_body_missing")
    paragraphs = max(candidates, key=lambda ps: sum(map(len, ps)))
    body = "\n".join(paragraphs)
    if len(body) < 80:
        raise ValueError("article_body_too_short")
    return {
        "title": title,
        "content": body[:100_000],
        "content_truncated": len(body) > 100_000,
        "page_published_at": meta.get("article:published_time")
        or meta.get("publishdate")
        or meta.get("pubdate"),
    }


def summarize(title, body):
    """Extract original sentences, ranked by title relevance; never invent missing facts."""
    title_grams = grams(title)
    sentences, seen = [], set()
    for paragraph in body.splitlines():
        for sentence in re.split(r"(?<=[。！？；])", paragraph):
            sentence = clean(sentence)
            if len(sentence) < 12 or sentence in seen or NOISE.search(sentence):
                continue
            seen.add(sentence)
            overlap = len(title_grams & grams(sentence))
            if overlap >= 2:
                factual = bool(
                    re.search(r"\d|发生|发布|通报|宣布|救援|调查|发运|获悉|启动", sentence)
                )
                score = overlap / max(1, len(title_grams)) + (0.2 if factual else 0)
                sentences.append((len(sentences), score, sentence))
    selected, length = [], 0
    for index, _score, text in sorted(sentences, key=lambda s: (-s[1], s[0])):
        if len(text) > 500 or length + len(text) > 600:
            continue
        selected.append((index, text))
        length += len(text)
        if len(selected) == 3:
            break
    selected.sort()
    return [text for _, text in selected]


class OfficialArticleClient:
    def __init__(self, client=None, cache_dir=None):
        self.client = client
        database = make_url(settings.database_url).database
        self.cache_dir = Path(
            cache_dir
            or settings.official_article_cache_dir
            or (
                Path(database).parent / "official-articles"
                if database and database != ":memory:"
                else Path("data/official-articles")
            )
        )

    def _path(self, url):
        return self.cache_dir / (hashlib.sha256((VERSION + url).encode()).hexdigest() + ".json")

    def cached(self, url):
        try:
            data = json.loads(self._path(url).read_text(encoding="utf-8"))
            ttl = (
                settings.official_article_cache_hours * 3600
                if data["status"] == "succeeded"
                else 600
            )
            if 0 <= time() - data["cached_at"] < ttl:
                return {**data, "cache_hit": True}
        except (OSError, ValueError, KeyError, TypeError):
            pass
        return None

    def save(self, url, data):
        temporary = None
        try:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            with NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=self.cache_dir, suffix=".tmp", delete=False
            ) as file:
                temporary = Path(file.name)
                json.dump({**data, "cached_at": time()}, file, ensure_ascii=False)
            temporary.replace(self._path(url))
        except OSError:
            data["cache_write_failed"] = True
        finally:
            if temporary:
                temporary.unlink(missing_ok=True)

    def fetch(self, item, remaining_seconds):
        url = safe_url(item.url, item.platform)
        if not url:
            return {"status": "fallback", "reason": "unsupported_article_url"}
        cached = self.cached(url)
        if cached:
            return cached
        client = self.client or httpx.Client()
        deadline = monotonic() + remaining_seconds
        current = url
        try:
            for _ in range(4):
                remaining = deadline - monotonic()
                if remaining <= 0:
                    raise ValueError("article_deadline_exceeded")

                def download(current=current, remaining=remaining):
                    with client.stream(
                        "GET",
                        current,
                        follow_redirects=False,
                        timeout=min(settings.official_article_timeout_seconds, remaining),
                        headers={
                            "User-Agent": "public-opinion-analysis-article/0.1",
                            "Accept": "text/html",
                        },
                    ) as response:
                        if not response.is_redirect:
                            response.raise_for_status()
                            if "html" not in response.headers.get("content-type", "").lower():
                                raise ValueError("article_not_html")
                            parts, size = [], 0
                            for chunk in response.iter_bytes():
                                size += len(chunk)
                                if size > MAX_BYTES or monotonic() > deadline:
                                    raise ValueError("article_size_or_time_limit")
                                parts.append(chunk)
                            response._content = b"".join(parts)
                        return response

                response = measured_call(
                    "official_article_" + item.platform, "GET", "http_request", download
                )
                if response.is_redirect:
                    current = safe_url(
                        urljoin(current, response.headers.get("location", "")), item.platform
                    )
                    if not current:
                        raise ValueError("article_redirect_not_allowed")
                    continue
                # Respect HTML-declared legacy encodings when the HTTP header omits one.
                if "charset=" not in response.headers.get("content-type", "").lower():
                    match = re.search(
                        rb'charset\s*=\s*["\x27]?([\w-]+)', response.content[:4096], re.I
                    )
                    if match:
                        response.encoding = match[1].decode("ascii")
                data = {
                    **extract_article(response.text, item.title or ""),
                    "status": "succeeded",
                    "url": current,
                    "fetched_at": datetime.now(UTC).isoformat(),
                    "cache_hit": False,
                }
                self.save(url, data)
                return data
            raise ValueError("article_redirect_limit")
        except RequestBudgetExceeded:
            return {"status": "fallback", "reason": "article_request_budget"}
        except (httpx.HTTPError, ValueError, LookupError) as exc:
            reason = str(exc) if isinstance(exc, ValueError) else type(exc).__name__
            data = {"status": "fallback", "reason": reason, "cache_hit": False}
            self.save(url, data)
            return data
        finally:
            if self.client is None:
                client.close()


def enrich_official_items(items, *, client=None, cache_dir=None):
    if not settings.official_article_enabled:
        return items
    extractor = OfficialArticleClient(client, cache_dir)
    deadline = monotonic() + settings.official_article_batch_seconds
    fetched = 0
    output = []
    for item in items:
        url = safe_url(item.url, item.platform)
        data = extractor.cached(url) if url else None
        if data is None:
            if fetched < settings.official_article_max_fetches and monotonic() < deadline:
                data = extractor.fetch(item, deadline - monotonic())
                fetched += 1
            else:
                data = {"status": "fallback", "reason": "article_batch_limit"}
        full = data["status"] == "succeeded"
        fallback = re.split(
            r"一起学习[↓]+|总监制|责任编辑|主编[丨：:]|编辑[丨：:&]|制图[丨：:]",
            clean(item.summary or ""),
        )[0].strip()
        body = data.get("content", "") if full else fallback
        excerpts = summarize(item.title or "", body)
        summary = "\n".join(excerpts) if excerpts else (fallback or item.summary)
        audit = {k: v for k, v in data.items() if k != "content"}
        audit.update(
            version=VERSION,
            original_summary=item.summary,
            summary_method="extractive" if excerpts else "source_description",
            summary_origin="article" if full and excerpts else "source_description",
            excerpts=[{"text": text, "url": data.get("url") or item.url} for text in excerpts],
        )
        output.append(
            item.model_copy(
                update={
                    "summary": summary,
                    "content": body if full else item.content,
                    "content_text": body if full else item.content_text,
                    "normalized": {**item.normalized, "official_article": audit},
                    "event_text_for_match": " ".join(filter(None, [item.title, summary])),
                    "quality_flags": list(
                        dict.fromkeys(
                            [*item.quality_flags, *([] if full else ["official_article_fallback"])]
                        )
                    ),
                }
            )
        )
    return output
