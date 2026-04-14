from __future__ import annotations

import argparse
import html
import json
import re
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import watcher


DASHBOARD_DIR = Path("dashboard")
KEYWORDS = [
    "bachelor",
    "bachelor thesis",
    "abschlussarbeit",
    "thesis",
    "theses",
    "student project",
    "student projects",
    "project",
    "topics",
    "open",
    "available",
    "offene",
    "topic",
]
NEGATIVE_HINTS = [
    "master",
    "phd",
    "doctoral",
    "postdoc",
    "job",
    "career",
    "publication",
]


def now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def normalize_sentence(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip(" -\t\r\n")


def split_candidates(text: str) -> list[str]:
    pieces: list[str] = []
    for raw_line in text.splitlines():
        line = normalize_sentence(raw_line)
        if not line:
            continue
        subparts = re.split(r"(?<=[.!?;])\s{1,}| \| ", line)
        for part in subparts:
            candidate = normalize_sentence(part)
            if candidate:
                pieces.append(candidate)
    return pieces


def score_candidate(text: str) -> float:
    lower = text.lower()
    score = 0.0

    for keyword in KEYWORDS:
        if keyword in lower:
            score += 2.0 if " " in keyword else 1.0

    if "bachelor" in lower:
        score += 2.5
    if "thesis" in lower or "abschlussarbeit" in lower:
        score += 2.0
    if "open" in lower or "available" in lower or "offene" in lower:
        score += 1.0

    for negative in NEGATIVE_HINTS:
        if negative in lower:
            score -= 0.75

    word_count = len(text.split())
    if 4 <= word_count <= 28:
        score += 1.0
    if 28 < word_count <= 50:
        score += 0.3
    if word_count < 3 or word_count > 70:
        score -= 0.8

    if re.search(r"https?://", text):
        score -= 0.4

    return score


def extract_opening_summary(text: str, max_items: int = 5) -> dict[str, Any]:
    candidates = split_candidates(text)
    ranked: list[tuple[float, str]] = []

    for candidate in candidates:
        score = score_candidate(candidate)
        if score >= 2.6:
            ranked.append((score, candidate))

    ranked.sort(key=lambda item: (-item[0], item[1]))

    seen: set[str] = set()
    top_items: list[str] = []
    for _, candidate in ranked:
        folded = candidate.casefold()
        if folded in seen:
            continue
        seen.add(folded)
        top_items.append(candidate)
        if len(top_items) >= max_items:
            break

    status = "possible openings found" if top_items else "needs review"
    return {
        "status": status,
        "highlights": top_items,
        "opening_count_estimate": len(top_items),
        "keyword_hits": count_keyword_hits(candidates),
    }


def count_keyword_hits(candidates: list[str]) -> dict[str, int]:
    counter: Counter[str] = Counter()
    for candidate in candidates:
        lower = candidate.lower()
        for keyword in KEYWORDS:
            if keyword in lower:
                counter[keyword] += 1
    return dict(counter.most_common(8))


def html_to_text(value: str) -> str:
    text = re.sub(r"<br\s*/?>", "\n", value, flags=re.I)
    text = re.sub(r"</p\s*>", "\n", text, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    return normalize_sentence(text)


def apply_strict_extractor(html_text: str, page_url: str, extractor: dict[str, Any]) -> list[dict[str, str]]:
    mode = extractor.get("mode")

    if mode == "section_regex_items":
        start = extractor["section_start"]
        end = extractor.get("section_end")
        start_match = re.search(start, html_text, re.I | re.S)
        if not start_match:
            return []
        section = html_text[start_match.end() :]
        if end:
            end_match = re.search(end, section, re.I | re.S)
            if end_match:
                section = section[: end_match.start()]
        return extract_items_with_pattern(section, page_url, extractor)

    if mode == "regex_items":
        return extract_items_with_pattern(html_text, page_url, extractor)

    if mode == "follow_link":
        link_match = re.search(extractor["link_pattern"], html_text, re.I | re.S)
        if not link_match:
            return []
        link_url = urljoin(page_url, html.unescape(link_match.group(extractor.get("url_group", 1))))
        nested_html = watcher.fetch_url(link_url, timeout=20, user_agent="ThesisMonitor/1.0")
        nested_items = apply_strict_extractor(nested_html, link_url, extractor["nested"])
        for item in nested_items:
            item.setdefault("source_url", link_url)
        return nested_items

    raise ValueError(f"Unsupported extractor mode: {mode}")


def extract_items_with_pattern(source: str, page_url: str, extractor: dict[str, Any]) -> list[dict[str, str]]:
    item_pattern = extractor["item_pattern"]
    title_group = extractor.get("title_group", 1)
    summary_group = extractor.get("summary_group")
    url_group = extractor.get("url_group")
    degree_group = extractor.get("degree_group")

    items: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()

    for match in re.finditer(item_pattern, source, re.I | re.S):
        title = html_to_text(match.group(title_group))
        if not title:
            continue
        summary = html_to_text(match.group(summary_group)) if summary_group else ""
        entry_url = page_url
        if url_group:
            entry_url = urljoin(page_url, html.unescape(match.group(url_group)))
        degree = html_to_text(match.group(degree_group)) if degree_group else ""
        key = (title.casefold(), entry_url)
        if key in seen:
            continue
        seen.add(key)
        items.append(
            {
                "title": title,
                "summary": summary,
                "url": entry_url,
                "degree": degree,
            }
        )

    return items


def build_exact_summary(raw_html: str, watcher_config: dict[str, Any], page_url: str) -> dict[str, Any]:
    extractor = watcher_config.get("strict_extractor")
    if not extractor:
        return {
            "status": "unsupported",
            "openings": [],
            "opening_count": 0,
            "mode": "strict",
        }

    openings = apply_strict_extractor(raw_html, page_url, extractor)
    return {
        "status": "exact openings found" if openings else "no explicit openings found",
        "openings": openings,
        "opening_count": len(openings),
        "mode": "strict",
    }


def build_record(config: dict[str, Any], watcher_config: dict[str, Any], refresh: bool) -> dict[str, Any]:
    state_file = watcher.state_path(watcher_config["name"])
    source = "cache"
    raw_html = ""

    if refresh or not state_file.exists():
        result = watcher.build_fetch_result(watcher_config, config.get("defaults", {}))
        state = {
            "name": watcher_config["name"],
            "url": watcher_config["url"],
            "content_hash": result.content_hash,
            "content": result.content,
            "raw_html": result.raw_html,
            "fetched_at": result.fetched_at,
        }
        watcher.save_state(state_file, state)
        source = "live"
    else:
        state = watcher.load_state(state_file)
        if state is None:
            raise ValueError(f"Missing state for {watcher_config['name']}")

    raw_html = state.get("raw_html", "")
    summary = build_exact_summary(raw_html, watcher_config, watcher_config["url"])
    return {
        "name": watcher_config["name"],
        "url": watcher_config["url"],
        "fetched_at": state.get("fetched_at"),
        "content_hash": state.get("content_hash"),
        "source": source,
        "summary": summary,
        "content_preview": watcher.trim(state.get("content", ""), 320),
    }


def render_dashboard(records: list[dict[str, Any]], generated_at: str) -> str:
    cards = "\n".join(render_card(record) for record in records)
    found = sum(1 for record in records if record["summary"]["openings"])
    unsupported = sum(1 for record in records if record["summary"]["status"] == "unsupported")
    none_found = len(records) - found - unsupported

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>ThesisMonitor Dashboard</title>
  <style>
    :root {{
      --bg: #f4efe6;
      --bg-strong: #efe4d0;
      --surface: rgba(255, 251, 245, 0.88);
      --surface-strong: #fffdf8;
      --ink: #1f1f1a;
      --muted: #5c5a50;
      --accent: #0d6b58;
      --accent-soft: #d5ede6;
      --warn: #b55d1d;
      --warn-soft: #f8dfc8;
      --border: rgba(31, 31, 26, 0.1);
      --shadow: 0 18px 40px rgba(80, 54, 24, 0.12);
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: Georgia, "Times New Roman", serif;
      color: var(--ink);
      background:
        radial-gradient(circle at top left, rgba(13,107,88,0.14), transparent 32%),
        radial-gradient(circle at top right, rgba(181,93,29,0.15), transparent 28%),
        linear-gradient(180deg, var(--bg) 0%, #fbf8f2 42%, #f1ece2 100%);
      min-height: 100vh;
    }}
    .wrap {{
      width: min(1200px, calc(100vw - 32px));
      margin: 0 auto;
      padding: 40px 0 56px;
    }}
    .hero {{
      background: linear-gradient(135deg, rgba(255,253,248,0.88), rgba(239,228,208,0.92));
      border: 1px solid var(--border);
      border-radius: 28px;
      padding: 28px;
      box-shadow: var(--shadow);
      backdrop-filter: blur(8px);
    }}
    .kicker {{
      text-transform: uppercase;
      letter-spacing: 0.16em;
      font-size: 12px;
      color: var(--muted);
      margin-bottom: 12px;
    }}
    h1 {{
      margin: 0;
      font-size: clamp(36px, 5vw, 68px);
      line-height: 0.98;
      font-weight: 700;
    }}
    .lede {{
      max-width: 780px;
      color: var(--muted);
      font-size: 18px;
      line-height: 1.6;
      margin-top: 14px;
      margin-bottom: 0;
    }}
    .stats {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
      gap: 14px;
      margin-top: 26px;
    }}
    .stat {{
      border: 1px solid var(--border);
      border-radius: 20px;
      background: rgba(255,255,255,0.54);
      padding: 16px 18px;
    }}
    .stat strong {{
      display: block;
      font-size: 28px;
      margin-bottom: 6px;
    }}
    .grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(290px, 1fr));
      gap: 18px;
      margin-top: 26px;
    }}
    .card {{
      background: var(--surface);
      border: 1px solid var(--border);
      border-radius: 24px;
      padding: 22px;
      box-shadow: var(--shadow);
      display: flex;
      flex-direction: column;
      min-height: 280px;
    }}
    .card:hover {{
      transform: translateY(-2px);
      transition: transform 160ms ease;
    }}
    .status {{
      display: inline-flex;
      align-self: flex-start;
      padding: 6px 12px;
      border-radius: 999px;
      font-size: 12px;
      letter-spacing: 0.08em;
      text-transform: uppercase;
      margin-bottom: 14px;
    }}
    .status.good {{
      background: var(--accent-soft);
      color: var(--accent);
    }}
    .status.warn {{
      background: var(--warn-soft);
      color: var(--warn);
    }}
    .card h2 {{
      margin: 0 0 8px;
      font-size: 25px;
      line-height: 1.15;
    }}
    .meta {{
      font-family: "Courier New", monospace;
      color: var(--muted);
      font-size: 12px;
      margin-bottom: 16px;
    }}
    .highlights {{
      margin: 0;
      padding-left: 18px;
      line-height: 1.55;
      color: var(--ink);
      flex: 1;
    }}
    .empty {{
      color: var(--muted);
      line-height: 1.6;
      flex: 1;
    }}
    .preview {{
      margin-top: 14px;
      color: var(--muted);
      font-size: 14px;
      line-height: 1.5;
      border-top: 1px solid var(--border);
      padding-top: 14px;
    }}
    .actions {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 14px;
      margin-top: 18px;
    }}
    .source {{
      color: var(--muted);
      font-size: 13px;
    }}
    .link {{
      display: inline-block;
      padding: 10px 14px;
      border-radius: 999px;
      background: var(--ink);
      color: white;
      text-decoration: none;
      font-size: 14px;
    }}
    @media (max-width: 720px) {{
      .wrap {{ width: min(100vw - 20px, 1200px); padding-top: 20px; }}
      .hero, .card {{ border-radius: 22px; }}
      h1 {{ font-size: 42px; }}
    }}
  </style>
</head>
<body>
  <main class="wrap">
    <section class="hero">
      <div class="kicker">ThesisMonitor Dashboard</div>
      <h1>Current Bachelor Thesis Signals</h1>
      <p class="lede">This page is a local summary generated from the configured RWTH and partner institute thesis pages. It surfaces likely bachelor-thesis openings, links back to the source pages, and flags pages that still need manual review.</p>
      <div class="stats">
        <div class="stat"><strong>{len(records)}</strong> tracked pages</div>
        <div class="stat"><strong>{found}</strong> pages with exact openings</div>
        <div class="stat"><strong>{none_found}</strong> pages with no explicit openings</div>
        <div class="stat"><strong>{unsupported}</strong> unsupported page structures</div>
        <div class="stat"><strong>{html.escape(generated_at)}</strong> generated at</div>
      </div>
    </section>
    <section class="grid">
      {cards}
    </section>
  </main>
</body>
</html>
"""


def render_card(record: dict[str, Any]) -> str:
    summary = record["summary"]
    has_openings = bool(summary["openings"])
    status = summary["status"]
    status_class = "good" if has_openings else "warn"
    if status == "unsupported":
        status_class = "warn"
    status_text = summary["status"]

    if has_openings:
        bullet_items = "\n".join(render_opening_item(item) for item in summary["openings"])
        content_html = f'<ul class="highlights">{bullet_items}</ul>'
    elif status == "unsupported":
        content_html = (
            '<div class="empty">This page does not currently have a strict extractor. '
            'It is intentionally excluded from exact opening detection until we add a site-specific rule.</div>'
        )
    else:
        content_html = (
            '<div class="empty">No explicit open-thesis entries were found with the configured strict extractor.</div>'
        )

    fetched_at = record.get("fetched_at") or "unknown"
    preview = html.escape(record.get("content_preview", ""))
    return f"""
<article class="card">
  <div class="status {status_class}">{html.escape(status_text)}</div>
  <h2>{html.escape(record['name'])}</h2>
  <div class="meta">{html.escape(fetched_at)}</div>
  {content_html}
  <div class="preview">{preview}</div>
  <div class="actions">
    <span class="source">Source: {html.escape(record['source'])}</span>
    <a class="link" href="{html.escape(record['url'])}" target="_blank" rel="noreferrer">Open source</a>
  </div>
</article>
"""


def render_opening_item(item: dict[str, str]) -> str:
    title = html.escape(item["title"])
    summary = html.escape(item.get("summary", ""))
    degree = html.escape(item.get("degree", ""))
    parts = [f'<strong>{title}</strong>']
    if degree:
        parts.append(f'<span> [{degree}]</span>')
    if summary:
        parts.append(f'<div>{summary}</div>')
    parts.append(f'<div><a href="{html.escape(item["url"])}" target="_blank" rel="noreferrer">Open entry</a></div>')
    return "<li>" + "".join(parts) + "</li>"


def write_outputs(records: list[dict[str, Any]], output_dir: Path) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    generated_at = now_iso()
    html_path = output_dir / "index.html"
    json_path = output_dir / "summary.json"

    with json_path.open("w", encoding="utf-8") as handle:
        json.dump({"generated_at": generated_at, "records": records}, handle, indent=2, ensure_ascii=True)

    with html_path.open("w", encoding="utf-8") as handle:
        handle.write(render_dashboard(records, generated_at))

    return html_path, json_path


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Generate a local HTML dashboard summarizing thesis openings.")
    parser.add_argument("--config", default="config.json", help="Path to the JSON config.")
    parser.add_argument("--output-dir", default=str(DASHBOARD_DIR), help="Directory for generated HTML and JSON.")
    parser.add_argument("--refresh", action="store_true", help="Fetch sites live before generating the dashboard.")
    parser.add_argument("--watcher", action="append", dest="watchers", help="Only include the given watcher name.")
    return parser


def main() -> int:
    parser = build_arg_parser()
    args = parser.parse_args()

    config = watcher.load_config(Path(args.config))
    watcher.validate_config(config)
    selected = set(args.watchers) if args.watchers else None

    records: list[dict[str, Any]] = []
    for watcher_config in config["watchers"]:
        if selected and watcher_config["name"] not in selected:
            continue
        try:
            records.append(build_record(config, watcher_config, refresh=args.refresh))
        except Exception as exc:
            records.append(
                {
                    "name": watcher_config["name"],
                    "url": watcher_config["url"],
                    "fetched_at": None,
                    "content_hash": None,
                    "source": "error",
                    "summary": {
                        "status": f"error: {exc}",
                        "openings": [],
                        "opening_count": 0,
                        "mode": "strict",
                    },
                    "content_preview": "Unable to fetch or parse this page during dashboard generation.",
                }
            )

    records.sort(
        key=lambda item: (
            0 if item["summary"]["openings"] else 1 if item["summary"]["status"] == "no explicit openings found" else 2,
            item["name"].lower(),
        )
    )
    html_path, json_path = write_outputs(records, Path(args.output_dir))
    print(f"Dashboard written to {html_path}")
    print(f"Summary data written to {json_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
