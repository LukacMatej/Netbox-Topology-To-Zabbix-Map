from __future__ import annotations

"""Minimal shared HTML shell for the small pages this app serves.

Kept deliberately tiny (no template engine, no client-side JS) so it stays
easy to reason about alongside the rest of this project's plain-Flask style.
"""

_STYLE = """
<style>
  :root {
    color-scheme: light dark;
    --bg: #f7f7f8;
    --surface: #ffffff;
    --border: #e2e2e5;
    --text: #1c1c1f;
    --muted: #6b6b70;
    --accent: #2563eb;
    --accent-contrast: #ffffff;
    --ok-bg: #ecfdf3;
    --ok-border: #a6e9c5;
    --ok-text: #087443;
  }
  @media (prefers-color-scheme: dark) {
    :root {
      --bg: #16171a;
      --surface: #1f2023;
      --border: #303136;
      --text: #f1f1f3;
      --muted: #9a9aa0;
      --accent: #3b82f6;
      --accent-contrast: #0b1220;
      --ok-bg: #0d2417;
      --ok-border: #1c4d31;
      --ok-text: #6fe3a4;
    }
  }
  * { box-sizing: border-box; }
  body {
    margin: 0;
    background: var(--bg);
    color: var(--text);
    font: 15px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
  }
  .page { max-width: 640px; margin: 0 auto; padding: 2.5rem 1.25rem; }
  h1 { font-size: 1.25rem; margin: 0 0 0.35rem; }
  .subtitle { color: var(--muted); font-size: 0.9rem; margin: 0 0 1.25rem; }
  .card {
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 10px;
    padding: 1.25rem 1.5rem;
  }
  .card + .card { margin-top: 1rem; }
  ul.links { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 0.5rem; }
  ul.links a { color: var(--accent); text-decoration: none; }
  ul.links a:hover { text-decoration: underline; }
  .trigger-list { display: flex; flex-direction: column; gap: 0.15rem; margin: 0 0 1.25rem; max-height: 60vh; overflow-y: auto; }
  label.trigger {
    display: flex;
    align-items: center;
    gap: 0.6rem;
    padding: 0.5rem 0.6rem;
    border-radius: 6px;
    cursor: pointer;
  }
  label.trigger:hover { background: var(--bg); }
  label.trigger input { width: 16px; height: 16px; flex-shrink: 0; }
  button {
    background: var(--accent);
    color: var(--accent-contrast);
    border: none;
    border-radius: 6px;
    padding: 0.55rem 1.1rem;
    font-size: 0.95rem;
    cursor: pointer;
  }
  button:hover { opacity: 0.9; }
  .empty { color: var(--muted); font-style: italic; margin: 0; }
  .banner {
    border: 1px solid var(--ok-border);
    background: var(--ok-bg);
    color: var(--ok-text);
    border-radius: 8px;
    padding: 0.6rem 0.9rem;
    font-size: 0.9rem;
    margin: 0 0 1rem;
  }
  code { background: var(--bg); border-radius: 4px; padding: 0.1rem 0.35rem; }
</style>
"""


def render_page(title: str, body: str) -> str:
    return (
        "<!doctype html><html><head>"
        "<meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        f"<title>{title}</title>"
        f"{_STYLE}"
        "</head><body><div class='page'>"
        f"{body}"
        "</div></body></html>"
    )


def render_banner(text: str) -> str:
    return f"<div class='banner'>{text}</div>"
