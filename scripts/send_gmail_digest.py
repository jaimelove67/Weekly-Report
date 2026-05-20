#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as dt
import html
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
    prefix = os.getenv("GMAIL_SUBJECT_PREFIX", "GitHub 每周热门项目速读").strip()
    generated_on = payload.get("generated_on", "")
    date_text = generated_on[:10] if generated_on else dt.date.today().isoformat()
    return f"{prefix} - {date_text}"


def build_intro(payload: dict) -> str:
    days = payload.get("days", 7)
    project_count = len(payload.get("projects", []))
    return (
        f"这是一封自动发送的 GitHub 项目速读邮件。\n"
        f"统计窗口：近 {days} 天\n"
        f"项目数量：{project_count}\n"
    )


def build_preview_lines(payload: dict, limit: int = 5) -> list[str]:
    lines: list[str] = []
    for index, project in enumerate(payload.get("projects", [])[:limit], start=1):
        lines.append(f"{index}. {project['name']} | {project['stars']} stars")
        lines.append(f"   这是什么：{project['headline']}")
        lines.append(f"   应用场景：{project['use_case']}")
        lines.append(f"   技术路径：{project['implementation']}")
        lines.append(f"   目标读者：{project['audience']}")
    return lines


def build_plain_body(markdown: str, payload: dict) -> str:
    parts = [
        build_intro(payload).strip(),
        "",
        "前 5 个项目预览：",
        *build_preview_lines(payload),
        "",
        "完整周报见附件，下面附上 Markdown 版本：",
        "",
        markdown.strip(),
        "",
        "这封邮件由 GitHub Actions 自动发送。",
    ]
    return "\n".join(parts).strip() + "\n"


def build_html_body(payload: dict) -> str:
    days = payload.get("days", 7)
    project_count = len(payload.get("projects", []))
    cards: list[str] = []

    for index, project in enumerate(payload.get("projects", []), start=1):
        topic_text = "、".join(project.get("topics", [])[:5]) or "无"
        card = f"""
        <div style="background:#ffffff;border:1px solid #d8e1ec;border-radius:12px;padding:20px 22px;margin:0 0 18px;">
          <div style="font-size:12px;color:#5f6b7a;margin-bottom:8px;letter-spacing:0.04em;">TOP {index}</div>
          <div style="font-size:20px;font-weight:750;color:#14213d;margin-bottom:8px;line-height:1.35;">
            <a href="{html.escape(project['url'])}" style="color:#14213d;text-decoration:none;">{html.escape(project['name'])}</a>
          </div>
          <div style="font-size:15px;font-weight:700;color:#0f172a;margin-bottom:14px;line-height:1.55;">{html.escape(project['headline'])}</div>
          <div style="font-size:14px;color:#243447;line-height:1.75;">
            <p style="margin:0 0 8px;"><strong style="display:inline-block;min-width:72px;color:#0f172a;">应用场景</strong>{html.escape(project['use_case'])}</p>
            <p style="margin:0 0 8px;"><strong style="display:inline-block;min-width:72px;color:#0f172a;">技术路径</strong>{html.escape(project['implementation'])}</p>
            <p style="margin:0;"><strong style="display:inline-block;min-width:72px;color:#0f172a;">目标读者</strong>{html.escape(project['audience'])}</p>
          </div>
          <div style="margin-top:12px;font-size:13px;color:#5f6b7a;">
            {project['stars']} 星 · {html.escape(project['language'])} · 主题：{html.escape(topic_text)}
          </div>
        </div>
        """
        cards.append(card.strip())

    return f"""
    <html>
      <body style="margin:0;padding:0;background:#f3f6fb;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;">
        <div style="max-width:880px;margin:0 auto;padding:28px 18px 40px;">
          <div style="background:linear-gradient(135deg,#14213d,#1f4b99);border-radius:18px;padding:28px 28px 24px;color:#ffffff;margin-bottom:20px;">
            <div style="font-size:30px;font-weight:800;">GitHub 每周热门项目速读</div>
            <div style="font-size:14px;opacity:0.92;margin-top:12px;line-height:1.7;">
              统计窗口：近 {days} 天<br>
              项目数量：{project_count}<br>
              发送时间：{html.escape(payload.get("generated_on", ""))}
            </div>
          </div>
          {''.join(cards)}
          <div style="font-size:12px;color:#7b8794;margin-top:16px;">
            这封邮件由 GitHub Actions 自动发送，完整 Markdown 与 JSON 周报已附在附件中。
          </div>
        </div>
      </body>
    </html>
    """.strip()


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
    message.set_content(build_plain_body(markdown, payload))
    message.add_alternative(build_html_body(payload), subtype="html")
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
        html_body = message.get_body(preferencelist=("html",))
        if html_body:
            print("\n--- HTML PREVIEW ---\n")
            print(html_body.get_content())
        return 0

    send_message(message)
    print(f"Sent digest email to {message['To']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
