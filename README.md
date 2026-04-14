# ThesisMonitor

`ThesisMonitor` is a small Python tool for watching one or more web pages, detecting changes, generating a short summary of the update, and sending a notification.

## What it does

- Fetches the target page on demand.
- Extracts the meaningful text content from the HTML.
- Stores the last snapshot locally in `data/`.
- Detects whether the page has changed since the previous run.
- Creates a short summary from the text diff.
- Sends the result to the console or by email via SMTP.

## Quick start

1. Copy `config.example.json` to `config.json`.
2. Edit the watcher URL and notification settings.
3. Run:

```powershell
python .\watcher.py --config .\config.json
```

The first run initializes the snapshot. Later runs compare against that saved state and only notify when content changes.

## Local dashboard

You can also generate a website-like overview of explicit thesis openings:

```powershell
python .\dashboard.py --config .\config.json --refresh
```

This creates:

- `dashboard/index.html`: a local page you can open in your browser.
- `dashboard/summary.json`: the structured summary behind the page.

Important:

- The dashboard now prefers strict, site-specific extraction rules.
- Pages without a strict extractor are shown as `unsupported` instead of guessed heuristically.
- This keeps the results honest, but it also means you need to add rules per website if you want broader coverage.

If you want to generate the page from already cached snapshots instead of fetching live again:

```powershell
python .\dashboard.py --config .\config.json
```

## Links dashboard

You can also generate a minimal jump page that only lists the watcher name and source URL from `config.json`:

```powershell
python .\links_dashboard.py --config .\config.json
```

This creates:

- `dashboard_links/index.html`: a simple link directory for jumping to the original websites.

The links dashboard can also merge requirement notes from your Excel file and lets you filter by institute name and requirement text.

## Publish To GitHub Pages

This workspace now includes a publish-ready file at `docs/index.html`.

For a project site:

1. Push this folder to a GitHub repository.
2. In the repository settings, open `Pages`.
3. Choose `Deploy from a branch`.
4. Select branch `main` and folder `/docs`.

For a user site at `https://<your-username>.github.io/`:

1. Create a repository named exactly `<your-username>.github.io`.
2. Push the same contents there.
3. In `Pages`, choose branch `main` and folder `/docs`, or move `docs/index.html` to the repository root as `index.html`.

If you regenerate the links dashboard later, copy `dashboard_links/index.html` over `docs/index.html` before pushing.

## Example config notes

Each watcher supports:

- `name`: Friendly watcher name used in logs and alerts.
- `url`: Page to monitor.
- `notification_channel`: `console` or `email`.
- `strip_html`: Convert HTML into plain text before diffing.
- `start_marker` and `end_marker`: Limit monitoring to a relevant section of the page.
- `match_pattern`: Optional regex to extract a targeted block before cleanup.
- `ignore_patterns`: Regex patterns for noisy content like timestamps or counters.
- `timeout_seconds`: Optional per-watcher timeout override.
- `user_agent`: Optional per-watcher request header override.

## Email setup

Set the global `notification.channel` to `email` or set a specific watcher's `notification_channel` to `email`.

SMTP settings:

- `host` / `port`: Your mail server.
- `use_tls`: For STARTTLS on ports like `587`.
- `use_ssl`: For implicit SSL on ports like `465`.
- `username` / `password`: Mail credentials if required.
- `from_email`: Sender address.
- `to_emails`: Recipient list.

If you use Gmail, Outlook, or similar providers, you will often need an app password instead of your normal account password.

## Running specific watchers

```powershell
python .\watcher.py --config .\config.json --watcher "Example News"
```

You can pass `--watcher` multiple times.

## Scheduling ideas

This script is designed to be run repeatedly by a scheduler.

On Windows, use Task Scheduler to run:

```powershell
python E:\ThesisMonitor\watcher.py --config E:\ThesisMonitor\config.json
```

Examples:

- Every 30 minutes for fast-moving pages.
- Every morning for institutional or publication pages.
- Different tasks for different watcher groups.

## Limitations and next steps

- The summary is rule-based, not LLM-generated.
- Some websites render content with JavaScript after page load; those may need a browser-based fetcher later.
- Highly dynamic pages often need better `ignore_patterns` or tighter extraction markers.

Good next upgrades:

- Add Slack, Telegram, or Discord notifications.
- Add browser automation for JavaScript-heavy pages.
- Add a small web dashboard or SQLite history.
- Add LLM summarization for richer update explanations.
