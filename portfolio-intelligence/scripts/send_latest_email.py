#!/usr/bin/env python3
"""Send the latest rendered portfolio brief HTML by SMTP."""

from __future__ import annotations

import os
import smtplib
import ssl
from email.message import EmailMessage
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LATEST_HTML = ROOT / "ui" / "latest.html"
REPORTS_DIR = ROOT / "reports"


def env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def latest_report_date() -> str:
    reports = sorted(REPORTS_DIR.glob("20*.md"))
    return reports[-1].stem if reports else "latest"


def main() -> int:
    required = {
        "SMTP_HOST": env("SMTP_HOST"),
        "SMTP_USERNAME": env("SMTP_USERNAME"),
        "SMTP_PASSWORD": env("SMTP_PASSWORD"),
        "MAIL_TO": env("MAIL_TO", "geanya@me.com"),
    }
    missing = [key for key, value in required.items() if not value]
    if missing:
        print(f"::warning::Email skipped; missing secrets/env: {', '.join(missing)}")
        return 0

    if not LATEST_HTML.exists():
        raise FileNotFoundError(f"Missing latest HTML report: {LATEST_HTML}")

    port = int(env("SMTP_PORT", "587"))
    sender = env("MAIL_FROM", required["SMTP_USERNAME"])
    recipient = required["MAIL_TO"]
    report_date = latest_report_date()
    html = LATEST_HTML.read_text(encoding="utf-8")

    message = EmailMessage()
    message["Subject"] = f"A股持仓每日盘前情报 - {report_date}"
    message["From"] = sender
    message["To"] = recipient
    message.set_content(
        "今日 A股与持仓盘前情报已生成。HTML 报告已附在本邮件中；如果邮件客户端支持 HTML，也会直接显示正文。"
    )
    message.add_alternative(html, subtype="html")
    message.add_attachment(
        html.encode("utf-8"),
        maintype="text",
        subtype="html",
        filename=f"portfolio-brief-{report_date}.html",
    )

    if port == 465:
        context = ssl.create_default_context()
        with smtplib.SMTP_SSL(required["SMTP_HOST"], port, context=context, timeout=30) as smtp:
            smtp.login(required["SMTP_USERNAME"], required["SMTP_PASSWORD"])
            smtp.send_message(message)
    else:
        with smtplib.SMTP(required["SMTP_HOST"], port, timeout=30) as smtp:
            smtp.ehlo()
            smtp.starttls(context=ssl.create_default_context())
            smtp.login(required["SMTP_USERNAME"], required["SMTP_PASSWORD"])
            smtp.send_message(message)

    print(f"Email sent to {recipient}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
