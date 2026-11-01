"""Live GitHub REST client with a disk cache, built on urllib only.

Why this file exists (and why it is the most interesting part of the gateway):

* MCP tools are I/O — an LLM will happily call the same tool eight times in one
  task. Without a cache you burn rate limit and money on identical queries.
* Unauthenticated GitHub allows 60 requests/hour. A naive tool that raises on
  403 turns a whole agent trajectory into a failure. We instead serve stale
  cache when the limit is hit, and tell the model so, in the tool result.
* Network calls fail. A tool that fails permanently teaches the model to retry
  forever. A tool that degrades to "cached answer + staleness flag" keeps the
  task alive.

The cache key is the URL, the validator is the ETag (GitHub supports 304), and
`meta` is returned to the caller so cache behaviour is measurable instead of
assumed.
"""

import hashlib
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASE = "https://api.github.com"
CACHE_PATH = os.path.join(ROOT, ".cache", "github.json")
DEFAULT_TTL = 3600


class ExternalApiError(Exception):
    """Raised when the remote API fails and no usable cache exists."""


def _load():
    try:
        with open(CACHE_PATH, encoding="utf-8") as handle:
            return json.load(handle)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save(store):
    os.makedirs(os.path.dirname(CACHE_PATH), exist_ok=True)
    tmp = CACHE_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(store, handle, ensure_ascii=False)
    os.replace(tmp, CACHE_PATH)


def _headers(etag=None):
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "mcp-tool-gateway/0.1",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if etag:
        headers["If-None-Match"] = etag
    return headers


def request(path, params=None, ttl=DEFAULT_TTL, timeout=8.0, retries=1):
    """Return (payload, meta). meta["cache"] is one of hit / miss / revalidated / stale."""
    url = BASE + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    key = hashlib.sha1(url.encode()).hexdigest()[:16]

    store = _load()
    entry = store.get(key)
    now = time.time()

    if entry and now - entry["fetched_at"] < ttl:
        return entry["data"], {"cache": "hit", "age_s": int(now - entry["fetched_at"]),
                               "source": "disk", "stale": False}

    attempt = 0
    last_error = None
    while attempt <= retries:
        attempt += 1
        try:
            request_obj = urllib.request.Request(
                url, headers=_headers(entry.get("etag") if entry else None))
            with urllib.request.urlopen(request_obj, timeout=timeout) as response:
                payload = json.loads(response.read())
                store[key] = {
                    "url": url,
                    "data": payload,
                    "etag": response.headers.get("ETag"),
                    "fetched_at": now,
                }
                _save(store)
                remaining = response.headers.get("X-RateLimit-Remaining")
                return payload, {"cache": "miss", "attempt": attempt,
                                 "rate_remaining": remaining, "stale": False}
        except urllib.error.HTTPError as exc:
            if exc.code == 304 and entry:
                entry["fetched_at"] = now
                _save(store)
                return entry["data"], {"cache": "revalidated", "source": "disk", "stale": False}
            remaining = exc.headers.get("X-RateLimit-Remaining") if exc.headers else None
            exhausted = exc.code in (403, 429) and str(remaining) == "0"
            last_error = exc
            if exhausted or exc.code >= 500:
                break
            if exc.code in (400, 404, 422):
                raise ExternalApiError(f"GitHub 拒绝请求 ({exc.code}): {_message(exc)}") from None
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last_error = exc
            time.sleep(0.4)

    if entry:
        return entry["data"], {
            "cache": "stale",
            "age_s": int(now - entry["fetched_at"]),
            "source": "disk",
            "stale": True,
            "degraded_because": _reason(last_error),
        }
    raise ExternalApiError(
        f"GitHub API 不可用且本地无缓存（{_reason(last_error)}）。"
        f"请稍后重试，或设置 GITHUB_TOKEN 提高限流额度。"
    )


def _message(exc):
    try:
        return json.loads(exc.read()).get("message", "")
    except Exception:  # noqa: BLE001
        return ""


def _reason(exc):
    if exc is None:
        return "unknown"
    if isinstance(exc, urllib.error.HTTPError):
        remaining = exc.headers.get("X-RateLimit-Remaining") if exc.headers else None
        if str(remaining) == "0":
            reset = exc.headers.get("X-RateLimit-Reset", "")
            return f"rate limit exhausted, resets at {reset}"
        return f"HTTP {exc.code}"
    return type(exc).__name__


# ---------------------------------------------------------------- tools
def search_issues(query, limit=5, repo="modelcontextprotocol/servers", ttl=DEFAULT_TTL):
    payload, meta = request(
        "/search/issues",
        {"q": f"repo:{repo} {query}", "per_page": max(1, min(limit, 20)),
         "sort": "updated", "order": "desc"},
        ttl=ttl,
    )
    items = [
        {
            "number": item["number"],
            "title": item["title"],
            "state": item["state"],
            "comments": item.get("comments", 0),
            "labels": [label["name"] for label in item.get("labels", [])],
            "author": (item.get("user") or {}).get("login"),
            "url": item.get("html_url"),
        }
        for item in payload.get("items", [])
    ]
    return {"repo": repo, "query": query, "total": payload.get("total_count", 0),
            "items": items, "_meta": meta}


def get_issue(number, repo="modelcontextprotocol/servers", ttl=DEFAULT_TTL):
    payload, meta = request(f"/repos/{repo}/issues/{int(number)}", ttl=ttl)
    body = payload.get("body") or ""
    return {
        "number": payload.get("number"),
        "title": payload.get("title"),
        "state": payload.get("state"),
        "author": (payload.get("user") or {}).get("login"),
        "comments": payload.get("comments"),
        "labels": [label["name"] for label in payload.get("labels", [])],
        # Truncated here rather than letting a 40 KB issue body eat the context
        # window: the gateway truncates again, but catching it at the source
        # keeps the payload small enough to cache and to log.
        "body": body[:1500],
        "body_truncated": len(body) > 1500,
        "url": payload.get("html_url"),
        "_meta": meta,
    }


def create_comment(number, body, repo="modelcontextprotocol/servers", confirm=False, dry_run=True):
    """Write operation. dry_run defaults to True so a demo never posts to a real repo."""
    if not confirm:
        raise ExternalApiError("用户未确认，拒绝写入。")
    if dry_run:
        return {"ok": True, "dry_run": True, "repo": repo, "number": int(number),
                "would_post_chars": len(body),
                "note": "dry_run=true，未真正写入 GitHub；设置 GITHUB_WRITE=1 才会真发。"}

    url = f"{BASE}/repos/{repo}/issues/{int(number)}/comments"
    data = json.dumps({"body": body}).encode()
    headers = _headers()
    headers["Content-Type"] = "application/json"
    request_obj = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request_obj, timeout=10) as response:
            payload = json.loads(response.read())
    except urllib.error.HTTPError as exc:
        raise ExternalApiError(f"写入失败 ({exc.code}): {_message(exc)}") from None
    return {"ok": True, "dry_run": False, "url": payload.get("html_url")}
