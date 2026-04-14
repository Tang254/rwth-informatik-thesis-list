from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
import smtplib
import ssl
from dataclasses import dataclass
from datetime import UTC, datetime
from email.message import EmailMessage
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen
import difflib


DATA_DIR = Path("data")


class HTMLTextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._chunks: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style", "noscript"}:
            self._skip_depth += 1
            return
        if self._skip_depth == 0 and tag in {"p", "div", "section", "article", "li", "tr", "br", "h1", "h2", "h3", "h4"}:
            self._chunks.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "noscript"} and self._skip_depth > 0:
            self._skip_depth -= 1
            return
        if self._skip_depth == 0 and tag in {"p", "div", "section", "article", "li", "tr", "br", "h1", "h2", "h3", "h4"}:
            self._chunks.append("\n")

    def handle_data(self, data: str) -> None:
        if self._skip_depth == 0:
            self._chunks.append(data)

    def get_text(self) -> str:
        return "".join(self._chunks)


@dataclass
class FetchResult:
    raw_html: str
    content: str
    content_hash: str
    fetched_at: str


def load_config(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def slugify(value: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9]+", "-", value.strip().lower()).strip("-")
    return cleaned or "watcher"


def now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def fetch_url(url: str, timeout: int, user_agent: str) -> str:
    request = Request(url, headers={"User-Agent": user_agent})
    with urlopen(request, timeout=timeout) as response:
        charset = response.headers.get_content_charset() or "utf-8"
        body = response.read()
        return body.decode(charset, errors="replace")


def extract_content(html_text: str, watcher: dict[str, Any]) -> str:
    content = html.unescape(html_text)

    match_pattern = watcher.get("match_pattern")
    if match_pattern:
        match = re.search(match_pattern, content, re.IGNORECASE | re.DOTALL)
        if match:
            content = match.group(1) if match.groups() else match.group(0)

    start_marker = watcher.get("start_marker")
    end_marker = watcher.get("end_marker")
    if start_marker and start_marker in content:
        content = content.split(start_marker, 1)[1]
    if end_marker and end_marker in content:
        content = content.split(end_marker, 1)[0]

    if watcher.get("strip_html", True):
        parser = HTMLTextExtractor()
        parser.feed(content)
        content = parser.get_text()

    for pattern in watcher.get("ignore_patterns", []):
        content = re.sub(pattern, " ", content, flags=re.IGNORECASE | re.DOTALL)

    lines = [re.sub(r"\s+", " ", line).strip() for line in content.splitlines()]
    normalized = "\n".join(line for line in lines if line)
    return normalized.strip()


def build_fetch_result(watcher: dict[str, Any], defaults: dict[str, Any]) -> FetchResult:
    timeout = int(watcher.get("timeout_seconds", defaults.get("timeout_seconds", 15)))
    user_agent = watcher.get("user_agent", defaults.get("user_agent", "ThesisMonitor/1.0"))
    raw_html = fetch_url(watcher["url"], timeout=timeout, user_agent=user_agent)
    content = extract_content(raw_html, watcher)
    content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
    return FetchResult(
        raw_html=raw_html,
        content=content,
        content_hash=content_hash,
        fetched_at=now_iso(),
    )


def state_path(base_name: str) -> Path:
    return DATA_DIR / f"{slugify(base_name)}.json"


def load_state(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def save_state(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, ensure_ascii=True)


def summarize_changes(old_text: str, new_text: str, max_items: int = 6, max_chars: int = 280) -> str:
    old_lines = [line.strip() for line in old_text.splitlines() if line.strip()]
    new_lines = [line.strip() for line in new_text.splitlines() if line.strip()]

    added: list[str] = []
    removed: list[str] = []
    for line in difflib.ndiff(old_lines, new_lines):
        if line.startswith("+ "):
            added.append(line[2:])
        elif line.startswith("- "):
            removed.append(line[2:])

    parts: list[str] = []
    for line in added[:max_items]:
        parts.append(f"Added: {trim(line, max_chars)}")
    for line in removed[:max_items]:
        parts.append(f"Removed: {trim(line, max_chars)}")

    if not parts:
        similarity = difflib.SequenceMatcher(a=old_text, b=new_text).ratio()
        return f"Content changed, but the line-level diff was noisy. Similarity: {similarity:.0%}."

    return "\n".join(parts[:max_items])


def trim(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 3].rstrip() + "..."


def send_email(notification: dict[str, Any], subject: str, body: str) -> None:
    smtp = notification["smtp"]
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = smtp["from_email"]
    message["To"] = ", ".join(smtp["to_emails"])
    message.set_content(body)

    host = smtp["host"]
    port = int(smtp["port"])
    username = smtp.get("username")
    password = smtp.get("password")
    use_tls = smtp.get("use_tls", True)
    use_ssl = smtp.get("use_ssl", False)

    if use_ssl:
        context = ssl.create_default_context()
        with smtplib.SMTP_SSL(host, port, context=context) as server:
            if username and password:
                server.login(username, password)
            server.send_message(message)
        return

    with smtplib.SMTP(host, port) as server:
        if use_tls:
            context = ssl.create_default_context()
            server.starttls(context=context)
        if username and password:
            server.login(username, password)
        server.send_message(message)


def notify(config: dict[str, Any], watcher: dict[str, Any], summary: str, result: FetchResult) -> None:
    notification = config.get("notification", {})
    channel = watcher.get("notification_channel", notification.get("channel", "console"))
    subject = f"[Site Update] {watcher['name']}"
    body = (
        f"Watcher: {watcher['name']}\n"
        f"URL: {watcher['url']}\n"
        f"Detected at: {result.fetched_at}\n\n"
        f"Summary:\n{summary}\n"
    )

    if channel == "email":
        send_email(notification, subject, body)
    else:
        print("=" * 80)
        print(subject)
        print(body)


def watcher_matches_filter(watcher: dict[str, Any], selected_names: set[str] | None) -> bool:
    if not selected_names:
        return True
    return watcher["name"] in selected_names


def check_watcher(config: dict[str, Any], watcher: dict[str, Any]) -> dict[str, Any]:
    defaults = config.get("defaults", {})
    result = build_fetch_result(watcher, defaults)
    path = state_path(watcher["name"])
    previous = load_state(path)

    state = {
        "name": watcher["name"],
        "url": watcher["url"],
        "content_hash": result.content_hash,
        "content": result.content,
        "fetched_at": result.fetched_at,
    }

    if previous is None:
        save_state(path, state)
        return {"name": watcher["name"], "status": "initialized"}

    if previous.get("content_hash") == result.content_hash:
        save_state(path, state)
        return {"name": watcher["name"], "status": "unchanged"}

    summary = summarize_changes(previous.get("content", ""), result.content)
    notify(config, watcher, summary, result)
    save_state(path, state)
    return {"name": watcher["name"], "status": "changed", "summary": summary}


def validate_config(config: dict[str, Any]) -> None:
    if "watchers" not in config or not isinstance(config["watchers"], list) or not config["watchers"]:
        raise ValueError("Config must include a non-empty 'watchers' list.")
    for watcher in config["watchers"]:
        if "name" not in watcher or "url" not in watcher:
            raise ValueError("Each watcher must define 'name' and 'url'.")


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Monitor websites for updates and notify on change.")
    parser.add_argument("--config", default="config.json", help="Path to a JSON config file.")
    parser.add_argument(
        "--watcher",
        action="append",
        dest="watchers",
        help="Run only the watcher(s) with the given name. Can be used multiple times.",
    )
    return parser


def main() -> int:
    parser = build_arg_parser()
    args = parser.parse_args()

    config_path = Path(args.config)
    if not config_path.exists():
        parser.error(f"Config file not found: {config_path}")

    config = load_config(config_path)
    validate_config(config)

    selected_names = set(args.watchers) if args.watchers else None
    any_ran = False

    for watcher in config["watchers"]:
        if not watcher_matches_filter(watcher, selected_names):
            continue
        any_ran = True
        try:
            outcome = check_watcher(config, watcher)
            print(f"{outcome['name']}: {outcome['status']}")
            if outcome.get("summary"):
                print(trim(outcome["summary"], 600))
        except (HTTPError, URLError, TimeoutError) as exc:
            print(f"{watcher['name']}: fetch_failed ({exc})")
        except smtplib.SMTPException as exc:
            print(f"{watcher['name']}: notification_failed ({exc})")

    if not any_ran:
        available = ", ".join(w["name"] for w in config["watchers"])
        parser.error(f"No watchers matched. Available watcher names: {available}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
