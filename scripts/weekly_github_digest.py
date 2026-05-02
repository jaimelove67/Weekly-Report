#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import datetime as dt
import json
import os
import re
import sys
import textwrap
import urllib.error
import urllib.parse
import urllib.request
import unicodedata
from pathlib import Path
from typing import Any

GITHUB_API = "https://api.github.com"
OPENAI_API = "https://api.openai.com/v1/chat/completions"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build a weekly GitHub digest with concise project summaries."
    )
    parser.add_argument("--days", type=int, default=7, help="Look back this many days.")
    parser.add_argument("--top", type=int, default=10, help="How many repositories to keep.")
    parser.add_argument(
        "--min-stars",
        type=int,
        default=150,
        help="Minimum stars for the search query.",
    )
    parser.add_argument(
        "--query-extra",
        action="append",
        default=[],
        help="Extra GitHub search qualifier, such as language:Python.",
    )
    parser.add_argument(
        "--output",
        default="output/weekly-github-digest.md",
        help="Markdown output path.",
    )
    parser.add_argument(
        "--json-output",
        default="output/weekly-github-digest.json",
        help="JSON output path.",
    )
    return parser


def request_json(url: str, headers: dict[str, str]) -> dict[str, Any]:
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def request_text(url: str, headers: dict[str, str]) -> str:
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read().decode("utf-8")


def github_headers() -> dict[str, str]:
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "weekly-github-digest-script",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    token = os.getenv("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def search_repositories(days: int, top: int, min_stars: int, extras: list[str]) -> list[dict[str, Any]]:
    since = (dt.date.today() - dt.timedelta(days=days)).isoformat()
    query_parts = [
        f"created:>={since}",
        f"stars:>={min_stars}",
        "fork:false",
        "archived:false",
        "is:public",
    ]
    query_parts.extend(extras)
    query = " ".join(query_parts)
    params = urllib.parse.urlencode(
        {
            "q": query,
            "sort": "stars",
            "order": "desc",
            "per_page": min(top, 100),
        }
    )
    url = f"{GITHUB_API}/search/repositories?{params}"
    payload = request_json(url, github_headers())
    return payload.get("items", [])[:top]


def fetch_readme(full_name: str) -> str:
    url = f"{GITHUB_API}/repos/{full_name}/readme"
    try:
        payload = request_json(url, github_headers())
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return ""
        raise

    content = payload.get("content", "")
    if not content:
        return ""

    try:
        raw = base64.b64decode(content)
        text = raw.decode("utf-8", errors="ignore")
    except (ValueError, UnicodeDecodeError):
        return ""
    return clean_text(text)


def clean_text(text: str) -> str:
    lines = [line.strip() for line in text.splitlines()]
    kept: list[str] = []
    for line in lines:
        if not line:
            if kept and kept[-1] != "":
                kept.append("")
            continue
        if line.startswith("```"):
            continue
        if line.startswith("#"):
            kept.append(line.lstrip("# ").strip())
            continue
        if line.startswith("!"):
            continue
        kept.append(normalize_plain_text(line))
    compact = "\n".join(kept).strip()
    return compact[:5000]


def normalize_plain_text(text: str) -> str:
    text = "".join(
        " " if unicodedata.category(char) in {"So", "Sk"} else char for char in text
    )
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"!\[[^\]]*\]\([^)]+\)", " ", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    text = text.replace("\xa0", " ")
    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(r"^[^\w\u4e00-\u9fff]+", "", text)
    return text


def maybe_summarize_with_openai(repo: dict[str, Any], readme: str) -> dict[str, Any] | None:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        return None

    model = os.getenv("OPENAI_MODEL", "gpt-4.1-mini")
    prompt = textwrap.dedent(
        f"""
        Summarize this GitHub project in concise Chinese.
        Return strict JSON with keys:
        one_liner: string
        highlights: array with exactly 3 short strings

        Repository: {repo.get("full_name", "")}
        Description: {repo.get("description") or ""}
        Language: {repo.get("language") or "Unknown"}
        Topics: {", ".join(repo.get("topics") or [])}
        Stars: {repo.get("stargazers_count", 0)}
        README excerpt:
        {readme[:3500]}
        """
    ).strip()

    body = {
        "model": model,
        "response_format": {"type": "json_object"},
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are a technical editor. Keep the output crisp, useful, and factual."
                ),
            },
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.3,
    }

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    try:
        req = urllib.request.Request(
            OPENAI_API,
            data=json.dumps(body).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=60) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None

    try:
        content = payload["choices"][0]["message"]["content"]
        return json.loads(content)
    except (KeyError, IndexError, json.JSONDecodeError, TypeError):
        return None


def fallback_summary(repo: dict[str, Any], readme: str) -> dict[str, Any]:
    description = normalize_plain_text(repo.get("description") or "")
    language = repo.get("language") or "Unknown"
    topics = repo.get("topics") or []
    highlights: list[str] = []

    if topics:
        highlights.append("关键词：" + " / ".join(topics[:3]))
    highlights.append(f"主要语言：{language}")
    highlights.append(f"当前星标：{repo.get('stargazers_count', 0)}")

    readme_snippet = ""
    if readme:
        first_block = normalize_plain_text(readme.split("\n\n", 1)[0].replace("\n", " ").strip())
        if first_block and first_block.lower() != repo.get("name", "").lower():
            readme_snippet = first_block[:100]

    if description:
        one_liner = f"这是一个围绕“{description[:140]}”展开的开源项目。"
    elif readme_snippet:
        one_liner = f"这是一个近期升温很快的开源项目，README 重点提到：{readme_snippet}"
    else:
        one_liner = f"这是一个近期在 GitHub 上快速升温的 {language} 开源项目。"

    if readme_snippet and description and readme_snippet.lower() not in description.lower():
        one_liner += f" README 重点：{readme_snippet}"

    return {"one_liner": one_liner, "highlights": highlights[:3]}


def build_digest_items(repos: list[dict[str, Any]]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for repo in repos:
        readme = fetch_readme(repo["full_name"])
        summary = maybe_summarize_with_openai(repo, readme) or fallback_summary(repo, readme)
        items.append(
            {
                "name": repo["full_name"],
                "url": repo["html_url"],
                "description": repo.get("description") or "",
                "language": repo.get("language") or "Unknown",
                "stars": repo.get("stargazers_count", 0),
                "topics": repo.get("topics") or [],
                "created_at": repo.get("created_at"),
                "updated_at": repo.get("updated_at"),
                "summary": summary["one_liner"].strip(),
                "highlights": [item.strip() for item in summary["highlights"][:3]],
            }
        )
    return items


def render_markdown(days: int, items: list[dict[str, Any]]) -> str:
    date_label = dt.date.today().isoformat()
    lines = [
        "# GitHub 每周热门项目速览",
        "",
        f"- 生成日期：{date_label}",
        f"- 统计窗口：近 {days} 天",
        f"- 项目数量：{len(items)}",
        "",
    ]

    for index, item in enumerate(items, start=1):
        lines.append(f"## {index}. [{item['name']}]({item['url']})")
        lines.append("")
        lines.append(f"- 星标：{item['stars']}")
        lines.append(f"- 主要语言：{item['language']}")
        topic_text = ", ".join(item["topics"][:5]) if item["topics"] else "无"
        lines.append(f"- 主题：{topic_text}")
        lines.append(f"- 精炼说明：{item['summary']}")
        for highlight in item["highlights"]:
            lines.append(f"- {highlight}")
        lines.append("")

    return "\n".join(lines).strip() + "\n"


def write_text(path: str, content: str) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")


def write_json(path: str, payload: dict[str, Any]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> int:
    args = build_parser().parse_args()
    try:
        repos = search_repositories(args.days, args.top, args.min_stars, args.query_extra)
        items = build_digest_items(repos)
    except urllib.error.HTTPError as exc:
        print(f"HTTP error: {exc.code} {exc.reason}", file=sys.stderr)
        return 1
    except urllib.error.URLError as exc:
        print(f"Network error: {exc.reason}", file=sys.stderr)
        return 1

    digest = {
        "generated_on": dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat(),
        "days": args.days,
        "top": args.top,
        "min_stars": args.min_stars,
        "projects": items,
    }

    write_text(args.output, render_markdown(args.days, items))
    write_json(args.json_output, digest)

    print(f"Wrote {len(items)} projects to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
