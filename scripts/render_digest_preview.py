#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

from send_gmail_digest import build_html_body, read_json


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Render the digest email HTML into a standalone preview file."
    )
    parser.add_argument(
        "--json",
        default="output/weekly-github-digest.json",
        help="Path to the generated json digest.",
    )
    parser.add_argument(
        "--output",
        default="output/weekly-github-digest-preview.html",
        help="Path to the standalone html preview.",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    payload = read_json(args.json)
    for project in payload.get("projects", []):
        if "use_case" not in project and "core_problem" in project:
            project["use_case"] = project["core_problem"]
        if "implementation" not in project and "solution" in project:
            project["implementation"] = project["solution"]
        if "audience" not in project and "why_it_matters" in project:
            project["audience"] = project["why_it_matters"]
    html = build_html_body(payload)
    target = Path(args.output)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(html, encoding="utf-8")
    print(f"Wrote preview to {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
