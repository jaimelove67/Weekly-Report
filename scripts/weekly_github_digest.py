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
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

GITHUB_API = "https://api.github.com"
OPENAI_API = "https://api.openai.com/v1/chat/completions"

TERM_GLOSSARY = {
    "BYOK": "自带 API Key（Bring Your Own Key）",
    "Cloudflare Workers": "Cloudflare 的边缘函数运行环境",
    "CVE": "公开披露的安全漏洞编号",
    "DPI": "深度包检测（Deep Packet Inspection）",
    "domain fronting": "域名前置，用允许访问的域名做流量转发",
    "end-to-end": "端到端，覆盖完整流程",
    "GAS": "Google Apps Script，Google 的脚本自动化平台",
    "hCaptcha": "一种验证码服务",
    "local-first": "本地优先，核心数据和工作流优先在本机完成",
    "protocol replay": "协议重放，按真实接口流程复现请求",
    "RAG": "检索增强生成（Retrieval-Augmented Generation）",
}

NOISE_PATTERNS = (
    "installation",
    "getting started",
    "quickstart",
    "license",
    "contributing",
    "roadmap",
    "table of contents",
    "sponsor",
    "english |",
    "deutsch |",
    "español |",
    "français |",
    "简体中文",
    "繁體中文",
)


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


def search_repositories(
    days: int, top: int, min_stars: int, extras: list[str]
) -> list[dict[str, Any]]:
    since = (dt.date.today() - dt.timedelta(days=days)).isoformat()
    query_parts = [
        f"created:>={since}",
        f"stars:>={min_stars}",
        "fork:false",
        "archived:false",
        "is:public",
    ]
    query_parts.extend(extras)
    params = urllib.parse.urlencode(
        {
            "q": " ".join(query_parts),
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
    except ValueError:
        return ""
    return clean_text(raw.decode("utf-8", errors="ignore"))


def clean_text(text: str) -> str:
    lines = [line.rstrip() for line in text.splitlines()]
    kept: list[str] = []
    in_code_block = False

    for raw_line in lines:
        line = raw_line.strip()
        if line.startswith("```"):
            in_code_block = not in_code_block
            continue
        if in_code_block or not line:
            if kept and kept[-1] != "":
                kept.append("")
            continue
        if line.startswith("!"):
            continue
        if line.startswith("#"):
            line = line.lstrip("# ").strip()
        if line.startswith(("- ", "* ", "+ ")):
            line = line[2:].strip()
        kept.append(normalize_plain_text(line))

    compact = "\n".join(part for part in kept if part is not None).strip()
    return compact[:7000]


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


def shorten(text: str, limit: int) -> str:
    text = normalize_plain_text(text)
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip(" ,;:.-") + "…"


def compact_english(text: str) -> str:
    text = normalize_plain_text(text)
    text = re.sub(r"\[[^\]]+\]\[[^\]]+\]", " ", text)
    text = re.sub(r"`[^`]+`", " ", text)
    text = re.sub(r"\([^)]*released[^)]*\)", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def extract_signal_lines(readme: str) -> list[str]:
    signals: list[str] = []
    seen: set[str] = set()
    for block in readme.splitlines():
        line = normalize_plain_text(block)
        lower = line.lower()
        if not line or len(line) < 18:
            continue
        if line.count("|") >= 2:
            continue
        if re.fullmatch(r"[A-Za-z0-9 _/\-+().,:]{1,24}", line):
            continue
        if any(noise in lower for noise in NOISE_PATTERNS):
            continue
        if "provided for educational" in lower:
            continue
        if line.lower() in seen:
            continue
        seen.add(line.lower())
        signals.append(line)
        if len(signals) >= 8:
            break
    return signals


def collect_term_notes(*texts: str) -> list[dict[str, str]]:
    haystack = " ".join(texts)
    notes: list[dict[str, str]] = []
    lower_haystack = haystack.lower()
    for term, note in TERM_GLOSSARY.items():
        if term.lower() in lower_haystack:
            notes.append({"term": term, "note": note})
        if len(notes) >= 3:
            break
    return notes


def filter_term_notes(term_notes: list[dict[str, str]]) -> list[dict[str, str]]:
    filtered: list[dict[str, str]] = []
    for note in term_notes:
        if len(note["term"]) < 4 and note["term"] not in {"CVE", "DPI", "RAG", "GAS"}:
            continue
        filtered.append(note)
    return filtered[:3]


def first_nonempty(*values: str) -> str:
    for value in values:
        value = normalize_plain_text(value)
        if value:
            return value
    return ""


def extract_cve_identifier(*texts: str) -> str:
    merged = " ".join(texts)
    match = re.search(r"CVE-\d{4}-\d{4,7}", merged, flags=re.IGNORECASE)
    return match.group(0).upper() if match else ""


def normalize_summary_payload(payload: dict[str, Any]) -> dict[str, Any]:
    term_notes: list[dict[str, str]] = []
    for raw_note in payload.get("term_notes", [])[:3]:
        term = normalize_plain_text(str(raw_note.get("term", "")))
        note = normalize_plain_text(str(raw_note.get("note", "")))
        if term and note:
            term_notes.append({"term": term, "note": note})

    return {
        "headline": shorten(normalize_plain_text(str(payload.get("headline", ""))), 80),
        "use_case": shorten(normalize_plain_text(str(payload.get("use_case", ""))), 110),
        "implementation": shorten(
            normalize_plain_text(str(payload.get("implementation", ""))), 110
        ),
        "audience": shorten(normalize_plain_text(str(payload.get("audience", ""))), 90),
        "term_notes": filter_term_notes(term_notes),
    }


def build_pattern_summary(repo: dict[str, Any], readme: str) -> dict[str, Any] | None:
    description = compact_english(repo.get("description") or "")
    signals = extract_signal_lines(readme)
    primary = compact_english(signals[0]) if signals else ""
    merged = " ".join([description, primary, compact_english(readme[:2500])]).lower()
    cve = extract_cve_identifier(repo.get("name", ""), description, readme)

    if cve:
        return normalize_summary_payload(
            {
                "headline": f"这是一个围绕 {cve} 漏洞做复现和分析的安全项目。",
                "use_case": f"如果你要快速理解 {cve} 的影响和复现路径，这个仓库相当于一份可操作实验材料。",
                "implementation": "它把漏洞编号、复现目标和实验线索集中整理，方便直接搭环境验证。",
                "audience": "适合安全研究员、漏洞分析人员和想快速跟进漏洞细节的开发者。",
                "term_notes": [{"term": "CVE", "note": TERM_GLOSSARY["CVE"]}],
            }
        )

    if "alternative to" in merged and "design" in merged:
        return normalize_summary_payload(
            {
                "headline": "这是一个面向 AI 设计工作的开源替代品，目标是替代闭源设计代理工具。",
                "use_case": "如果你想自己部署 AI 设计流程、切换模型或接入团队现有设计系统，它提供了一条开源方案。",
                "implementation": "项目把本地优先的设计代理、现成设计系统和多种导出能力整合成一套工作流。",
                "audience": "适合设计工具 PM、前端团队、做 AI 创作流程的人快速评估可替代性。",
                "term_notes": collect_term_notes(description, readme, "BYOK local-first"),
            }
        )

    if "examples for building with cursor" in merged or "cursor sdk" in merged:
        return normalize_summary_payload(
            {
                "headline": "这是一套围绕 Cursor SDK 的示例库，用来展示 Cursor 能怎么接进真实应用。",
                "use_case": "如果你在评估 Cursor SDK 是否值得接入自己的产品，这个仓库能帮你快速扫清上手成本。",
                "implementation": "仓库用一批最小示例演示 SDK 调用方式、代理接入方法和典型集成场景。",
                "audience": "适合工具 PM、平台工程师和要做 AI 编码集成的开发者。",
                "term_notes": [],
            }
        )

    if "domain-fronting relay" in merged or ("cloudflare workers" in merged and "gas" in merged):
        return normalize_summary_payload(
            {
                "headline": "这是一个网络流量中继方案，前端走 GAS，后端转发到 Cloudflare Workers。",
                "use_case": "如果你在研究受限网络下的链路可达性或流量绕行方案，这个仓库能提供一条现成实验路径。",
                "implementation": "项目把 GAS、域名前置和 Cloudflare Workers 串成可复用的转发链路。",
                "audience": "适合安全研究、网络工程和做连接策略验证的开发者。",
                "term_notes": collect_term_notes(description, readme, "GAS domain fronting Cloudflare Workers DPI"),
            }
        )

    if "menu bar app" in merged and "usb-c" in merged:
        return normalize_summary_payload(
            {
                "headline": "这是一个 macOS 菜单栏工具，用来识别每根 USB-C 线到底支持哪些能力。",
                "use_case": "如果你经常分不清 USB-C 线能不能跑高速传输、视频输出或快充，这个工具就是直接答案。",
                "implementation": "工具读取系统层硬件信息，再把复杂参数翻译成普通用户能看懂的结果。",
                "audience": "适合做消费级工具的 PM，也适合 Mac 重度用户和硬件开发者参考交互方式。",
                "term_notes": [],
            }
        )

    if "legal platform" in merged:
        return normalize_summary_payload(
            {
                "headline": "这是一个开源 AI 法律工作平台，想把检索、问答和文档处理放进同一个产品里。",
                "use_case": "如果你在看垂直行业 AI 产品，这个仓库能帮助判断法律场景里的产品形态应该怎么落地。",
                "implementation": "项目采用前后端分层方式，把法律文档处理、检索和 AI 交互能力组合成完整应用。",
                "audience": "适合法律科技 PM、做企业知识库的人，以及关心垂直场景 AI 落地的开发者。",
                "term_notes": collect_term_notes(description, readme, "RAG"),
            }
        )

    if "tweak system" in merged and "codex" in merged:
        return normalize_summary_payload(
            {
                "headline": "这是一个给 Codex 桌面版做功能增强和界面修补的扩展系统。",
                "use_case": "如果你把代理型 IDE 当主力工具，这个项目展示了怎样在不重造产品的前提下补齐缺口。",
                "implementation": "项目通过注入自定义增强模块，为现有桌面应用补功能、修界面并扩展交互。",
                "audience": "适合桌面工具 PM、效率工具开发者和重度 AI IDE 用户。",
                "term_notes": [],
            }
        )

    if "subscription" in merged and ("hcaptcha" in merged or "protocol replay" in merged):
        return normalize_summary_payload(
            {
                "headline": "这是一个围绕订阅协议重放、验证码处理和反欺诈研究的实验工具集。",
                "use_case": "如果你关注订阅链路里的协议细节、验证码环节和反欺诈对抗，这个仓库是现成研究入口。",
                "implementation": "项目把协议重放、验证码求解和实证研究数据整理到一套实验材料里。",
                "audience": "适合安全研究、增长风控和做支付/订阅链路分析的技术人员。",
                "term_notes": collect_term_notes(description, readme, "protocol replay hCaptcha end-to-end"),
            }
        )

    return None


def maybe_summarize_with_openai(
    repo: dict[str, Any], readme: str
) -> dict[str, Any] | None:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        return None

    model = os.getenv("OPENAI_MODEL", "gpt-4.1-mini")
    prompt = textwrap.dedent(
        f"""
        你在写一份给产品经理和开发者快速扫读的 GitHub 热门项目简报。

        返回严格 JSON，字段如下：
        headline: 1 句话，直接说“这是什么”
        use_case: 1 句话，直接说“能拿它做什么/什么场景会用到”
        implementation: 1 句话，直接说“它大概怎么做”
        audience: 1 句话，直接说“谁最该看这个项目”
        term_notes: 最多 3 项术语注释；只有确实影响理解时才保留

        要求：
        - 用中文写，允许保留必要英文术语
        - 不要泛泛写“热度高”“很强”
        - 让没看过仓库的人 10 秒内就能抓到用途和判断值不值得点进去

        Repository: {repo.get("full_name", "")}
        Description: {repo.get("description") or ""}
        Language: {repo.get("language") or "Unknown"}
        Topics: {", ".join(repo.get("topics") or [])}
        Stars: {repo.get("stargazers_count", 0)}
        README excerpt:
        {readme[:5000]}
        """
    ).strip()

    body = {
        "model": model,
        "response_format": {"type": "json_object"},
        "messages": [
            {
                "role": "system",
                "content": "You write concise project digests for PMs and developers.",
            },
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.2,
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
        content = payload["choices"][0]["message"]["content"]
        result = json.loads(content)
    except Exception:
        return None

    return normalize_summary_payload(result)


def fallback_summary(repo: dict[str, Any], readme: str) -> dict[str, Any]:
    pattern_summary = build_pattern_summary(repo, readme)
    if pattern_summary:
        return pattern_summary

    description = first_nonempty(repo.get("description") or "")
    signals = extract_signal_lines(readme)
    primary_signal = first_nonempty(signals[0] if signals else "", description)
    secondary_signal = first_nonempty(
        signals[1] if len(signals) > 1 else "",
        signals[0] if signals else "",
    )
    term_notes = collect_term_notes(
        description,
        readme,
        " ".join(repo.get("topics") or []),
        repo.get("name", ""),
    )

    headline_source = first_nonempty(description, primary_signal)
    if headline_source:
        headline = f"这是一个在做“{shorten(headline_source, 62)}”的项目。"
    else:
        headline = f"这是一个近期快速升温的 {repo.get('language') or '开源'} 项目。"

    if description:
        use_case = f"你会在这样的场景里需要它：{shorten(description, 90)}"
    elif primary_signal:
        use_case = f"从 README 看，它更像是给这类需求准备的：{shorten(primary_signal, 90)}"
    else:
        use_case = "当前公开信息不多，但它明显击中了一个最近升温很快的细分需求。"

    if secondary_signal:
        implementation = f"它当前公开的做法是：{shorten(secondary_signal, 90)}"
    elif primary_signal and primary_signal != description:
        implementation = f"README 里最关键的实现线索是：{shorten(primary_signal, 90)}"
    else:
        language = repo.get("language") or "多语言"
        implementation = f"从仓库结构看，它主要以 {language} 生态下的工具或应用形态提供能力。"

    topic_hint = "、".join(repo.get("topics", [])[:2])
    if topic_hint:
        audience = f"最适合关注它的人：在做 {topic_hint} 方向产品判断或技术落地的人。"
    else:
        audience = "最适合关注它的人：想快速判断这个方向有没有可直接复用实现的 PM 或开发者。"

    return normalize_summary_payload(
        {
            "headline": headline,
            "use_case": use_case,
            "implementation": implementation,
            "audience": audience,
            "term_notes": term_notes,
        }
    )


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
                "headline": summary["headline"],
                "use_case": summary["use_case"],
                "implementation": summary["implementation"],
                "audience": summary["audience"],
                "term_notes": summary["term_notes"],
            }
        )
    return items


def render_markdown(days: int, items: list[dict[str, Any]]) -> str:
    date_label = dt.date.today().isoformat()
    lines = [
        "# GitHub 每周热门项目速读",
        "",
        f"- 生成日期：{date_label}",
        f"- 统计窗口：近 {days} 天",
        f"- 项目数量：{len(items)}",
        "",
    ]

    for index, item in enumerate(items, start=1):
        topic_text = "、".join(item["topics"][:5]) if item["topics"] else "无"
        lines.extend(
            [
                f"## {index}. [{item['name']}]({item['url']})",
                "",
                f"- 这是什么：{item['headline']}",
                f"- 能拿来干嘛：{item['use_case']}",
                f"- 怎么实现：{item['implementation']}",
                f"- 谁该看：{item['audience']}",
                f"- 基本信息：{item['stars']} 星；主要语言 {item['language']}；主题 {topic_text}",
            ]
        )
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
