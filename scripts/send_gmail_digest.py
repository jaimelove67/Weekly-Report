#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as dt
import json
import mimetypes
import os
import smtplib
from email.message import EmailMessage
from pathlib import Path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Send the generated GitHub digest to Gmail via SMTP."
    )
    parser.add_argument(
        "--markdown",
        default="output/weekly-github-digest.md",
        help="Path to the generated markdown digest.",
    )
    parser.add_argument(
        "--json",
        default="output/weekly-github-digest.json",
        help="Path to the generated json digest.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the email preview without sending.",
    )
    return parser


def read_text(path: str) -> str:
    return Path(path).read_text(encoding="utf-8")


def read_json(path: str) -> dict:
    return json.loads(read_text(path))


def build_subject(payload: dict) -> str:
    prefix = os.getenv("GMAIL_SUBJECT_PREFIX", "GitHub Weekly Digest").strip()
    generated_on = payload.get("generated_on", "")
    date_text = generated_on[:10] if generated_on else dt.date.today().isoformat()
    return f"{prefix} - {date_text}"


def build_intro(payload: dict) -> str:
    days = payload.get("days", 7)
    project_count = len(payload.get("projects", []))
    return (
        f"这是一封自动发送的 GitHub 周报邮件。\n"
        f"统计窗口：近 {days} 天\n"
        f"项目数量：{project_count}\n"
    )


def build_preview_lines(payload: dict, limit: int = 5) -> list[str]:
    lines: list[str] = []
    for index, project in enumerate(payload.get("projects", [])[:limit], start=1):
        lines.append(f"{index}. {project['name']} ({project['stars']} stars)")
        lines.append(f"   {project['summary']}")
    return lines


def build_body(markdown: str, payload: dict) -> str:
    parts = [
        build_intro(payload).strip(),
        "",
        "前几条摘要预览：",
        *build_preview_lines(payload),
        "",
        "完整周报见正文附件或以下 Markdown 内容：",
        "",
        markdown.strip(),
        "",
        "这封邮件由 GitHub Actions 自动发送。",
    ]
    return "\n".join(parts).strip() + "\n"


def add_attachment(message: EmailMessage, path: str) -> None:
    file_path = Path(path)
    data = file_path.read_bytes()
    mime_type, _ = mimetypes.guess_type(file_path.name)
    if mime_type:
        maintype, subtype = mime_type.split("/", 1)
    else:
        maintype, subtype = "application", "octet-stream"
    message.add_attachment(data, maintype=maintype, subtype=subtype, filename=file_path.name)


def build_message(markdown_path: str, json_path: str) -> EmailMessage:
    smtp_user = os.environ["GMAIL_SMTP_USER"]
    recipient = os.getenv("GMAIL_TO", "").strip() or smtp_user
    markdown = read_text(markdown_path)
    payload = read_json(json_path)

    message = EmailMessage()
    message["From"] = smtp_user
    message["To"] = recipient
    message["Subject"] = build_subject(payload)
    message.set_content(build_body(markdown, payload))
    add_attachment(message, markdown_path)
    add_attachment(message, json_path)
    return message


def send_message(message: EmailMessage) -> None:
    smtp_user = os.environ["GMAIL_SMTP_USER"]
    app_password = os.environ["GMAIL_APP_PASSWORD"]
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30) as smtp:
        smtp.login(smtp_user, app_password)
        smtp.send_message(message)


def main() -> int:
    args = build_parser().parse_args()
    message = build_message(args.markdown, args.json)

    if args.dry_run:
        print(message["Subject"])
        print(message.get_body(preferencelist=("plain",)).get_content())
        return 0

    send_message(message)
    print(f"Sent digest email to {message['To']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
