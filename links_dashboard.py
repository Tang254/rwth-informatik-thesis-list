from __future__ import annotations

import argparse
import html
import json
import re
import xml.etree.ElementTree as ET
import zipfile
from datetime import UTC, datetime
from pathlib import Path

import watcher


OUTPUT_DIR = Path("dashboard_links")
DEFAULT_XLSX_PATH = Path(r"E:\RWTH\Info\SS26\毕设\毕设列表.xlsx")
DEFAULT_REQUIREMENTS_PATH = Path("requirements.json")


def now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def normalize_space(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def normalize_multiline(value: str) -> str:
    lines = [normalize_space(line) for line in value.splitlines()]
    return "\n".join(line for line in lines if line)


def col_letters(ref: str) -> str:
    match = re.match(r"([A-Z]+)", ref)
    return match.group(1) if match else ""


def load_shared_strings(zf: zipfile.ZipFile, ns: dict[str, str]) -> list[str]:
    if "xl/sharedStrings.xml" not in zf.namelist():
        return []
    root = ET.fromstring(zf.read("xl/sharedStrings.xml"))
    values: list[str] = []
    for item in root.findall("m:si", ns):
        values.append("".join(text.text or "" for text in item.iterfind(".//m:t", ns)))
    return values


def cell_value(cell: ET.Element, shared_strings: list[str], ns: dict[str, str]) -> str:
    cell_type = cell.attrib.get("t")
    if cell_type == "inlineStr":
        return "".join(text.text or "" for text in cell.iterfind(".//m:t", ns))

    value = cell.find("m:v", ns)
    if value is None:
        return ""
    raw = value.text or ""
    if cell_type == "s":
        try:
            return shared_strings[int(raw)]
        except Exception:
            return raw
    return raw


def load_requirement_map(xlsx_path: Path) -> dict[str, str]:
    ns = {
        "m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
    }

    with zipfile.ZipFile(xlsx_path) as zf:
        shared_strings = load_shared_strings(zf, ns)
        sheet = ET.fromstring(zf.read("xl/worksheets/sheet1.xml"))
        requirements: dict[str, str] = {}

        for row in sheet.findall(".//m:sheetData/m:row", ns):
            row_num = int(row.attrib.get("r", "0"))
            if row_num <= 1:
                continue

            values: dict[str, str] = {}
            for cell in row.findall("m:c", ns):
                ref = cell.attrib.get("r", "")
                values[col_letters(ref)] = normalize_multiline(cell_value(cell, shared_strings, ns))

            name = values.get("B", "")
            if not name:
                continue

            requirement_parts = [part for part in (values.get("C", ""), values.get("D", "")) if part]
            requirements[name] = "\n".join(requirement_parts)

        return requirements


def load_requirements_file(path: Path) -> dict[str, dict[str, str]]:
    with path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)

    items = data.get("items", {})
    if not isinstance(items, dict):
        raise ValueError("requirements.json must contain an 'items' object.")
    return items


def ensure_requirements_file(path: Path, fallback_map: dict[str, str], watcher_names: list[str]) -> dict[str, dict[str, str]]:
    if path.exists():
        return load_requirements_file(path)

    items = {
        name: {
            "primary": fallback_map.get(name, ""),
            "secondary": "",
        }
        for name in watcher_names
    }
    payload = {
        "labels": {
            "primary": "Primary",
            "secondary": "Secondary",
        },
        "items": items,
    }
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, ensure_ascii=False)
    return items


def load_requirement_labels(path: Path) -> dict[str, str]:
    if not path.exists():
        return {"primary": "Primary", "secondary": "Secondary"}
    with path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    labels = data.get("labels", {})
    return {
        "primary": labels.get("primary", "Primary"),
        "secondary": labels.get("secondary", "Secondary"),
    }


def build_items(config: dict[str, object], requirements_data: dict[str, dict[str, str]]) -> list[dict[str, str]]:
    items: list[dict[str, str]] = []
    for raw in config["watchers"]:
        watcher_item = dict(raw)
        name = watcher_item["name"]
        requirement_entry = requirements_data.get(name, {})
        primary = normalize_multiline(requirement_entry.get("primary", ""))
        secondary = normalize_multiline(requirement_entry.get("secondary", ""))
        items.append(
            {
                "name": name,
                "url": watcher_item["url"],
                "requirement_primary": primary,
                "requirement_secondary": secondary,
                "requirement_search": normalize_space(primary + " " + secondary),
            }
        )
    items.sort(key=lambda item: item["name"].lower())
    return items


def render_requirement(requirement: str) -> str:
    if not requirement:
        return '<span class="empty">No requirement notes recorded.</span>'

    lines = [line for line in requirement.splitlines() if line.strip()]
    if len(lines) == 1:
        return f"<p class=\"requirement\">{html.escape(lines[0])}</p>"

    entries = "".join(f"<li>{html.escape(line)}</li>" for line in lines)
    return f'<ul class="requirement-list">{entries}</ul>'


def render_page(items: list[dict[str, str]], generated_at: str, labels: dict[str, str]) -> str:
    rows = "\n".join(
        f"""
<article class="row-card" data-name="{html.escape(item["name"].lower())}" data-requirement="{html.escape(item["requirement_search"].lower())}">
  <div class="cell title-cell">
    <div class="label">Title</div>
    <h2>{html.escape(item["name"])}</h2>
  </div>
  <div class="cell url-cell">
    <div class="label">URL</div>
    <p class="url">{html.escape(item["url"])}</p>
  </div>
  <div class="cell requirement-cell">
    <div class="label">Requirement</div>
    <div class="requirement-panel"
         data-primary="{html.escape(item["requirement_primary"])}"
         data-secondary="{html.escape(item["requirement_secondary"])}">
      {render_requirement(item["requirement_primary"])}
    </div>
  </div>
  <div class="cell action-cell">
    <div class="label">Open</div>
    <a class="link" href="{html.escape(item["url"])}" target="_blank" rel="noreferrer">Open</a>
  </div>
</article>
"""
        for item in items
    )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Thesis list</title>
  <style>
    :root {{
      --bg: #f5f1e8;
      --surface: rgba(255, 253, 249, 0.94);
      --surface-strong: #fffdfa;
      --ink: #1d1d1b;
      --muted: #6c675c;
      --accent: #135d66;
      --border: rgba(29, 29, 27, 0.09);
      --shadow: 0 14px 30px rgba(62, 42, 17, 0.08);
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: Georgia, "Times New Roman", serif;
      color: var(--ink);
      background:
        radial-gradient(circle at top left, rgba(19,93,102,0.08), transparent 32%),
        radial-gradient(circle at right, rgba(194,141,84,0.09), transparent 28%),
        linear-gradient(180deg, var(--bg), #fcfaf6);
    }}
    .wrap {{
      width: min(1320px, calc(100vw - 32px));
      margin: 0 auto;
      padding: 28px 0 46px;
    }}
    .hero {{
      background: linear-gradient(180deg, rgba(255,253,250,0.96), rgba(247,240,228,0.98));
      border: 1px solid var(--border);
      border-radius: 20px;
      padding: 24px 26px 20px;
      box-shadow: var(--shadow);
      margin-bottom: 18px;
    }}
    .hero-top {{
      display: flex;
      align-items: end;
      justify-content: space-between;
      gap: 18px;
    }}
    .hero h1 {{
      margin: 0;
      font-size: clamp(32px, 4.5vw, 50px);
      line-height: 1.02;
    }}
    .meta {{
      color: var(--muted);
      font-size: 13px;
      font-family: "Courier New", monospace;
      white-space: nowrap;
    }}
    .filters {{
      display: grid;
      grid-template-columns: minmax(220px, 1fr) minmax(260px, 1.4fr) auto;
      gap: 14px;
      margin-top: 18px;
      align-items: end;
    }}
    .field {{
      display: flex;
      flex-direction: column;
      gap: 7px;
    }}
    .field label {{
      font-size: 12px;
      text-transform: uppercase;
      letter-spacing: 0.1em;
      color: var(--muted);
      font-weight: 700;
    }}
    .field input {{
      width: 100%;
      border: 1px solid var(--border);
      background: #fffdfa;
      color: var(--ink);
      border-radius: 12px;
      padding: 12px 14px;
      font: inherit;
      font-size: 15px;
    }}
    .lang-switch {{
      display: flex;
      flex-direction: column;
      gap: 7px;
    }}
    .lang-switch-title {{
      font-size: 12px;
      text-transform: uppercase;
      letter-spacing: 0.1em;
      color: var(--muted);
      font-weight: 700;
    }}
    .lang-buttons {{
      display: inline-flex;
      gap: 8px;
      padding: 4px;
      border: 1px solid var(--border);
      border-radius: 14px;
      background: #fffdfa;
    }}
    .lang-button {{
      border: 0;
      background: transparent;
      color: var(--muted);
      font: inherit;
      font-size: 14px;
      padding: 8px 12px;
      border-radius: 10px;
      cursor: pointer;
    }}
    .lang-button.active {{
      background: var(--accent);
      color: white;
      font-weight: 700;
    }}
    .board {{
      background: var(--surface);
      border: 1px solid var(--border);
      border-radius: 20px;
      overflow: hidden;
      box-shadow: var(--shadow);
    }}
    .header-row,
    .row-card {{
      display: grid;
      grid-template-columns: minmax(220px, 1.1fr) minmax(260px, 1.5fr) minmax(280px, 1.6fr) 110px;
      gap: 0;
      align-items: stretch;
    }}
    .header-row {{
      background: #f2ece1;
      border-bottom: 1px solid var(--border);
    }}
    .header-cell,
    .cell {{
      padding: 16px 18px;
    }}
    .header-cell {{
      font-size: 12px;
      text-transform: uppercase;
      letter-spacing: 0.12em;
      color: var(--muted);
      font-weight: 700;
    }}
    .row-card {{
      background: var(--surface-strong);
      border-bottom: 1px solid var(--border);
    }}
    .row-card:last-child {{
      border-bottom: 0;
    }}
    .row-card:hover {{
      background: #fffaf2;
    }}
    .row-card.hidden {{
      display: none;
    }}
    .cell {{
      display: flex;
      flex-direction: column;
      justify-content: center;
      min-height: 108px;
    }}
    .label {{
      display: none;
    }}
    .title-cell,
    .url-cell,
    .requirement-cell {{
      border-right: 1px solid var(--border);
    }}
    h2 {{
      margin: 0;
      font-size: 22px;
      line-height: 1.18;
      font-weight: 700;
    }}
    .url {{
      margin: 0;
      color: var(--muted);
      font-size: 14px;
      line-height: 1.5;
      word-break: break-word;
    }}
    .requirement,
    .empty {{
      margin: 0;
      color: var(--muted);
      font-size: 14px;
      line-height: 1.5;
    }}
    .requirement-list {{
      margin: 0;
      padding-left: 18px;
      color: var(--muted);
      font-size: 14px;
      line-height: 1.5;
    }}
    .action-cell {{
      align-items: flex-start;
    }}
    .link {{
      display: inline-block;
      padding: 10px 16px;
      border-radius: 10px;
      text-decoration: none;
      color: white;
      background: var(--accent);
      font-size: 14px;
      font-weight: 700;
      box-shadow: inset 0 -1px 0 rgba(0,0,0,0.1);
    }}
    .link:hover {{
      background: #0f5159;
    }}
    .results-bar {{
      padding: 12px 18px;
      border-bottom: 1px solid var(--border);
      background: #faf6ef;
      color: var(--muted);
      font-size: 13px;
      font-family: "Courier New", monospace;
    }}
    @media (max-width: 920px) {{
      .filters {{
        grid-template-columns: 1fr;
      }}
      .header-row {{
        display: none;
      }}
      .row-card {{
        grid-template-columns: 1fr;
      }}
      .title-cell,
      .url-cell,
      .requirement-cell {{
        border-right: 0;
        border-bottom: 1px solid var(--border);
      }}
      .label {{
        display: block;
        margin-bottom: 8px;
        font-size: 11px;
        text-transform: uppercase;
        letter-spacing: 0.12em;
        color: var(--muted);
        font-weight: 700;
      }}
      .action-cell {{
        min-height: 76px;
      }}
    }}
  </style>
</head>
<body>
  <main class="wrap">
    <section class="hero">
      <div class="hero-top">
        <h1>Thesis list</h1>
        <div class="meta">Generated at: {html.escape(generated_at)} | Total links: {len(items)}</div>
      </div>
      <div class="filters">
        <div class="field">
          <label for="nameFilter">Filter by institute</label>
          <input id="nameFilter" type="text" placeholder="e.g. vision, security, graphics">
        </div>
        <div class="field">
          <label for="requirementFilter">Filter by requirement</label>
          <input id="requirementFilter" type="text" placeholder="e.g. C++, Python, formal methods, transcript">
        </div>
        <div class="lang-switch">
          <div class="lang-switch-title">Requirement version</div>
          <div class="lang-buttons">
            <button class="lang-button active" type="button" data-lang="primary">{html.escape(labels["primary"])}</button>
            <button class="lang-button" type="button" data-lang="secondary">{html.escape(labels["secondary"])}</button>
          </div>
        </div>
      </div>
    </section>
    <section class="board">
      <div class="results-bar"><span id="resultCount">{len(items)}</span> entries shown</div>
      <div class="header-row">
        <div class="header-cell">Title</div>
        <div class="header-cell">URL</div>
        <div class="header-cell">Requirement</div>
        <div class="header-cell">Open</div>
      </div>
      {rows}
    </section>
  </main>
  <script>
    const nameFilter = document.getElementById('nameFilter');
    const requirementFilter = document.getElementById('requirementFilter');
    const resultCount = document.getElementById('resultCount');
    const rows = Array.from(document.querySelectorAll('.row-card'));
    const langButtons = Array.from(document.querySelectorAll('.lang-button'));
    const requirementPanels = Array.from(document.querySelectorAll('.requirement-panel'));
    let currentLang = 'primary';

    function renderRequirement(value) {{
      const trimmed = (value || '').trim();
      if (!trimmed) {{
        return '<span class="empty">No requirement notes recorded.</span>';
      }}

      const lines = trimmed.split('\\n').map(line => line.trim()).filter(Boolean);
      if (lines.length === 1) {{
        return `<p class="requirement">${{escapeHtml(lines[0])}}</p>`;
      }}

      const entries = lines.map(line => `<li>${{escapeHtml(line)}}</li>`).join('');
      return `<ul class="requirement-list">${{entries}}</ul>`;
    }}

    function escapeHtml(value) {{
      return value
        .replaceAll('&', '&amp;')
        .replaceAll('<', '&lt;')
        .replaceAll('>', '&gt;')
        .replaceAll('"', '&quot;')
        .replaceAll("'", '&#39;');
    }}

    function applyLanguage() {{
      for (const panel of requirementPanels) {{
        const candidate = panel.dataset[currentLang] || panel.dataset.primary || '';
        const fallback = candidate.trim() ? candidate : (panel.dataset.primary || '');
        panel.innerHTML = renderRequirement(fallback);
      }}

      for (const button of langButtons) {{
        button.classList.toggle('active', button.dataset.lang === currentLang);
      }}
    }}

    function applyFilters() {{
      const nameNeedle = nameFilter.value.trim().toLowerCase();
      const requirementNeedle = requirementFilter.value.trim().toLowerCase();
      let visible = 0;

      for (const row of rows) {{
        const name = row.dataset.name || '';
        const requirement = row.dataset.requirement || '';
        const matchesName = !nameNeedle || name.includes(nameNeedle);
        const matchesRequirement = !requirementNeedle || requirement.includes(requirementNeedle);
        const show = matchesName && matchesRequirement;
        row.classList.toggle('hidden', !show);
        if (show) visible += 1;
      }}

      resultCount.textContent = String(visible);
    }}

    nameFilter.addEventListener('input', applyFilters);
    requirementFilter.addEventListener('input', applyFilters);
    for (const button of langButtons) {{
      button.addEventListener('click', () => {{
        currentLang = button.dataset.lang || 'primary';
        applyLanguage();
      }});
    }}
    applyLanguage();
  </script>
</body>
</html>
"""


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Generate a simple dashboard of source links from config.json.")
    parser.add_argument("--config", default="config.json", help="Path to the JSON config.")
    parser.add_argument("--output-dir", default=str(OUTPUT_DIR), help="Directory for generated HTML output.")
    parser.add_argument("--xlsx", default=str(DEFAULT_XLSX_PATH), help="Path to the source XLSX file.")
    parser.add_argument("--requirements", default=str(DEFAULT_REQUIREMENTS_PATH), help="Path to editable requirements JSON.")
    return parser


def main() -> int:
    parser = build_arg_parser()
    args = parser.parse_args()

    config = watcher.load_config(Path(args.config))
    watcher.validate_config(config)

    fallback_map = load_requirement_map(Path(args.xlsx))
    watcher_names = [item["name"] for item in config["watchers"]]
    requirements_path = Path(args.requirements)
    requirements_data = ensure_requirements_file(requirements_path, fallback_map, watcher_names)
    labels = load_requirement_labels(requirements_path)
    items = build_items(config, requirements_data)

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / "index.html"
    output_path.write_text(render_page(items, now_iso(), labels), encoding="utf-8")

    print(f"Links dashboard written to {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
