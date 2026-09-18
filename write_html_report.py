"""Turn classifier/db_recommendations.json into a single-file HTML report."""

from __future__ import annotations

import argparse
import html
import os
import sys
from collections import Counter
from typing import Any, Dict, List

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
if CURRENT_DIR not in sys.path:
    sys.path.insert(0, CURRENT_DIR)

from report_common import (  # noqa: E402
    build_summary_stats,
    format_score,
    load_recommendations,
    ranked_score_rows,
    validate_recommendations,
)

def get_db_display_names(databases_path: str | None = None) -> dict[str, str]:
    import json, os
    path = databases_path or os.path.join(os.path.dirname(os.path.abspath(__file__)), 'databases.json')
    rows = json.loads(open(path, encoding='utf-8').read())
    return {row['code']: row['name'] for row in rows}


DEFAULT_JSON = os.path.join(CURRENT_DIR, "db_recommendations.json")
DEFAULT_OUT = os.path.join(CURRENT_DIR, "index.html")


def escape_html(text: Any) -> str:
    """Escape text for safe HTML insertion."""
    return html.escape(str(text), quote=True)


def render_summary_section(payload: Dict[str, Any], stats: Dict[str, Any]) -> str:
    """Render the summary metrics strip and detail list."""
    usage = payload.get("usage") or {}
    timing = payload.get("timing") or {}
    estimated_cost = payload.get("estimated_cost_usd")
    generated_at = payload.get("generated_at")

    cards = [
        ("Prompts", str(stats["n_prompts"])),
        ("Unique DBs", str(stats["unique_dbs"])),
        ("Avg DBs / prompt", f"{stats['avg_dbs']:.2f}"),
        ("Threshold", str(payload["threshold"])),
    ]

    query_s = timing.get("total_query_seconds")
    if query_s is not None:
        cards.append(("Query time", f"{float(query_s):.2f}s"))
    if estimated_cost is not None:
        cards.append(("Est. cost", f"${float(estimated_cost):.6f}"))

    card_html = "\n".join(
        f'<div class="metric"><span class="metric-label">{escape_html(label)}</span>'
        f'<span class="metric-value">{escape_html(value)}</span></div>'
        for label, value in cards
    )

    details: List[str] = [
        f"<li><span>Model</span><code>{escape_html(payload['model'])}</code></li>",
        (
            f"<li><span>Top-k fallback</span>"
            f"<strong>{escape_html(payload['top_k_fallback'])}</strong></li>"
        ),
    ]
    if generated_at:
        details.append(
            f"<li><span>Generated at (UTC)</span>"
            f"<code>{escape_html(generated_at)}</code></li>"
        )
    if usage:
        details.append(
            "<li><span>Tokens</span><strong>"
            f"in {escape_html(usage.get('input_tokens', 'n/a'))} · "
            f"out {escape_html(usage.get('output_tokens', 'n/a'))} · "
            f"total {escape_html(usage.get('total_tokens', 'n/a'))}"
            "</strong></li>"
        )
    wall_s = timing.get("wall_clock_seconds")
    if wall_s is not None:
        details.append(
            f"<li><span>Wall-clock</span>"
            f"<strong>{escape_html(f'{float(wall_s):.2f}s')}</strong></li>"
        )

    return f"""
<section class="panel summary">
  <div class="section-head">
    <p class="eyebrow">Overview</p>
    <h2>Run summary</h2>
  </div>
  <div class="metrics">{card_html}</div>
  <ul class="detail-list">
    {"".join(details)}
  </ul>
</section>
"""


def render_frequency_section(
    stats: Dict[str, Any], display_names: Dict[str, str]
) -> str:
    """Render the frequency-by-database table."""
    freq: Counter = stats["freq"]
    n_prompts = stats["n_prompts"]
    rows: List[str] = []

    for code, count in freq.most_common():
        name = display_names.get(code, code)
        pct = (100.0 * count / n_prompts) if n_prompts else 0.0
        bar_width = max(pct, 2.0) if count else 0.0
        rows.append(
            "<tr>"
            f"<td><code>{escape_html(code)}</code></td>"
            f"<td>{escape_html(name)}</td>"
            f"<td class='num'>{escape_html(count)}</td>"
            f"<td class='num pct-cell'>"
            f"<span class='pct-bar' style='width:{bar_width:.1f}%'></span>"
            f"<span class='pct-text'>{pct:.1f}%</span>"
            "</td>"
            "</tr>"
        )

    if not rows:
        rows.append(
            "<tr><td colspan='4' class='empty'>No recommendations recorded.</td></tr>"
        )

    return f"""
<section class="panel">
  <div class="section-head">
    <p class="eyebrow">Coverage</p>
    <h2>Frequency by database</h2>
    <p class="lede">Share of prompts that recommended each database.</p>
  </div>
  <div class="table-wrap">
    <table>
      <thead>
        <tr>
          <th>Code</th>
          <th>Display name</th>
          <th class="num">Count</th>
          <th class="num">% of prompts</th>
        </tr>
      </thead>
      <tbody>
        {"".join(rows)}
      </tbody>
    </table>
  </div>
</section>
"""



def render_toc_section(results: List[Dict[str, Any]]) -> str:
    """Render jump links to each prompt card."""
    links: List[str] = []
    for row in results:
        index = row.get("prompt_index", "?")
        recommended = row.get("recommended_databases") or []
        label = f"Prompt {index}"
        if recommended:
            codes = ", ".join(str(c) for c in recommended[:3])
            label = f"{label} ({codes})"
        links.append(
            '<a class="toc-link" href="#prompt-'
            + escape_html(index)
            + '">'
            + escape_html(label)
            + '</a>'
        )

    if not links:
        body = '<p class="empty-note">No prompts to jump to.</p>'
    else:
        body = (
            '<nav class="toc-list" aria-label="Prompt index">'
            + ''.join(links)
            + '</nav>'
        )

    return (
        '<section class="panel toc">'
        '<div class="section-head">'
        '<p class="eyebrow">Navigate</p>'
        '<h2>Jump to prompt</h2>'
        '<p class="lede">Skip to a specific recommendation card.</p>'
        '</div>'
        + body
        + '</section>'
    )


def render_prompt_section(
    row: Dict[str, Any], display_names: Dict[str, str]
) -> str:
    """Render one per-prompt recommendation block."""
    index = row.get("prompt_index", "?")
    prompt = str(row.get("prompt", "")).strip()
    recommended = row.get("recommended_databases") or []
    scores = row.get("scores") or {}

    meta_bits: List[str] = []
    elapsed = row.get("elapsed_seconds")
    row_usage = row.get("usage") or {}
    row_cost = row.get("estimated_cost_usd")
    if elapsed is not None:
        meta_bits.append(f"{float(elapsed):.2f}s")
    if row_usage.get("input_tokens") is not None:
        meta_bits.append(f"{row_usage.get('input_tokens')} input tokens")
    if row_cost is not None:
        meta_bits.append(f"${float(row_cost):.6f}")

    meta_html = ""
    if meta_bits:
        meta_html = (
            f'<p class="prompt-meta">Query: {escape_html(", ".join(meta_bits))}</p>'
        )

    if not recommended:
        body = '<p class="empty-note">No databases recommended.</p>'
    else:
        score_rows: List[str] = []
        for code, score in ranked_score_rows(recommended, scores):
            if code not in recommended:
                continue
            name = display_names.get(code, code)
            score_str = format_score(score)
            try:
                score_val = float(score)
                bar = max(min(score_val, 1.0) * 100.0, 4.0)
            except (TypeError, ValueError):
                bar = 0.0
            score_rows.append(
                "<tr>"
                f"<td><code>{escape_html(code)}</code></td>"
                f"<td>{escape_html(name)}</td>"
                f"<td class='num score-cell'>"
                f"<span class='score-bar' style='width:{bar:.1f}%'></span>"
                f"<span class='score-text'>{escape_html(score_str)}</span>"
                "</td>"
                "</tr>"
            )
        body = f"""
<div class="table-wrap compact">
  <table>
    <thead>
      <tr>
        <th>Code</th>
        <th>Display name</th>
        <th class="num">Score</th>
      </tr>
    </thead>
    <tbody>
      {"".join(score_rows)}
    </tbody>
  </table>
</div>
"""

    chip_html = ""
    if recommended:
        chips = "".join(
            f'<span class="chip"><code>{escape_html(code)}</code></span>'
            for code in recommended
        )
        chip_html = f'<div class="chips">{chips}</div>'

    return f"""
<article class="prompt-card" id="prompt-{escape_html(index)}">
  <header class="prompt-head">
    <p class="prompt-index">Prompt {escape_html(index)}</p>
    {chip_html}
  </header>
  <p class="prompt-text">{escape_html(prompt)}</p>
  {meta_html}
  {body}
  <p class="back-top"><a href="#top">Back to top</a></p>
</article>
"""


def render_prompts_section(
    results: List[Dict[str, Any]], display_names: Dict[str, str]
) -> str:
    """Render all per-prompt recommendation sections."""
    articles = "\n".join(
        render_prompt_section(row, display_names) for row in results
    )
    return f"""
<section class="panel prompts">
  <div class="section-head">
    <p class="eyebrow">Detail</p>
    <h2>Per-prompt recommendations</h2>
    <p class="lede">Scores sorted descending within each prompt.</p>
  </div>
  <div class="prompt-list">
    {articles}
  </div>
</section>
"""


def build_report_css() -> str:
    """Inline CSS for a clean legal/research report aesthetic."""
    return """
:root {
  --ink: #1c2430;
  --muted: #5b6675;
  --line: #d7dde5;
  --paper: #f7f5f0;
  --panel: #ffffff;
  --accent: #1f4b63;
  --accent-soft: #e6eef2;
  --bar: #6b8797;
  --score: #2f6b4f;
  --shadow: 0 1px 2px rgba(28, 36, 48, 0.04), 0 12px 28px rgba(28, 36, 48, 0.06);
  --serif: "Iowan Old Style", "Palatino Linotype", Palatino, "Book Antiqua", Georgia, serif;
  --sans: "Segoe UI", "Helvetica Neue", Arial, sans-serif;
  --mono: "Cascadia Mono", "Consolas", "Liberation Mono", monospace;
}
* { box-sizing: border-box; }
html { scroll-behavior: smooth; }
body {
  margin: 0;
  color: var(--ink);
  background:
    radial-gradient(1200px 500px at 10% -10%, #ebe6dc 0%, transparent 55%),
    radial-gradient(900px 420px at 100% 0%, #e4ebef 0%, transparent 50%),
    var(--paper);
  font-family: var(--sans);
  line-height: 1.55;
}
a { color: var(--accent); }
.page {
  max-width: 980px;
  margin: 0 auto;
  padding: 2.5rem 1.25rem 4rem;
}
.hero {
  margin-bottom: 1.75rem;
  padding: 0.25rem 0 0.5rem;
  border-bottom: 1px solid var(--line);
}
.hero .kicker {
  margin: 0 0 0.55rem;
  font-size: 0.78rem;
  letter-spacing: 0.14em;
  text-transform: uppercase;
  color: var(--muted);
}
.hero h1 {
  margin: 0 0 0.55rem;
  font-family: var(--serif);
  font-weight: 600;
  font-size: clamp(1.9rem, 3.4vw, 2.55rem);
  letter-spacing: -0.02em;
  line-height: 1.15;
}
.hero .subtitle {
  margin: 0;
  max-width: 42rem;
  color: var(--muted);
  font-size: 1.02rem;
}
.panel {
  background: var(--panel);
  border: 1px solid var(--line);
  box-shadow: var(--shadow);
  border-radius: 4px;
  padding: 1.35rem 1.35rem 1.45rem;
  margin-bottom: 1.25rem;
}
.section-head { margin-bottom: 1rem; }
.eyebrow {
  margin: 0 0 0.3rem;
  font-size: 0.72rem;
  letter-spacing: 0.12em;
  text-transform: uppercase;
  color: var(--muted);
}
.section-head h2 {
  margin: 0;
  font-family: var(--serif);
  font-size: 1.45rem;
  font-weight: 600;
  letter-spacing: -0.01em;
}
.lede {
  margin: 0.35rem 0 0;
  color: var(--muted);
  font-size: 0.95rem;
}
.metrics {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
  gap: 0.75rem;
  margin-bottom: 1rem;
}
.metric {
  background: var(--accent-soft);
  border: 1px solid #d0dde4;
  border-radius: 3px;
  padding: 0.7rem 0.8rem;
  display: flex;
  flex-direction: column;
  gap: 0.2rem;
}
.metric-label {
  font-size: 0.72rem;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  color: var(--muted);
}
.metric-value {
  font-family: var(--serif);
  font-size: 1.35rem;
  font-weight: 600;
  color: var(--accent);
}
.detail-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: grid;
  gap: 0.45rem;
}
.detail-list li {
  display: flex;
  flex-wrap: wrap;
  gap: 0.35rem 0.85rem;
  align-items: baseline;
  padding: 0.35rem 0;
  border-top: 1px solid var(--line);
  font-size: 0.95rem;
}
.detail-list li span {
  min-width: 9.5rem;
  color: var(--muted);
  font-size: 0.85rem;
}
.toc-list {
  display: flex;
  flex-wrap: wrap;
  gap: 0.45rem;
}
.toc-link {
  display: inline-flex;
  align-items: center;
  text-decoration: none;
  background: var(--accent-soft);
  border: 1px solid #d0dde4;
  border-radius: 999px;
  padding: 0.28rem 0.7rem;
  font-size: 0.86rem;
  color: var(--accent);
}
.toc-link:hover { background: #d9e6ec; }
.back-top {
  margin: 0.75rem 0 0;
  font-size: 0.82rem;
}
.table-wrap { overflow-x: auto; }
table {
  width: 100%;
  border-collapse: collapse;
  font-size: 0.95rem;
}
th, td {
  padding: 0.65rem 0.55rem;
  border-bottom: 1px solid var(--line);
  text-align: left;
  vertical-align: middle;
}
th {
  font-size: 0.75rem;
  letter-spacing: 0.06em;
  text-transform: uppercase;
  color: var(--muted);
  font-weight: 600;
}
td.num, th.num { text-align: right; font-variant-numeric: tabular-nums; }
code {
  font-family: var(--mono);
  font-size: 0.88em;
  background: #f0eee8;
  border: 1px solid #e2ddd3;
  border-radius: 2px;
  padding: 0.05rem 0.3rem;
}
.pct-cell, .score-cell {
  position: relative;
  min-width: 6.5rem;
}
.pct-bar, .score-bar {
  position: absolute;
  left: 0;
  top: 50%;
  transform: translateY(-50%);
  height: 0.55rem;
  border-radius: 999px;
  opacity: 0.28;
  max-width: 100%;
}
.pct-bar { background: var(--bar); }
.score-bar { background: var(--score); }
.pct-text, .score-text { position: relative; z-index: 1; }
.prompt-list { display: grid; gap: 1rem; }
.prompt-card {
  border: 1px solid var(--line);
  border-radius: 3px;
  padding: 1rem 1.05rem 1.1rem;
  background: linear-gradient(180deg, #fcfbf8 0%, #ffffff 40%);
}
.prompt-head {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: 0.5rem 1rem;
  margin-bottom: 0.55rem;
}
.prompt-index {
  margin: 0;
  font-size: 0.72rem;
  letter-spacing: 0.12em;
  text-transform: uppercase;
  color: var(--accent);
  font-weight: 700;
}
.chips { display: flex; flex-wrap: wrap; gap: 0.35rem; }
.chip {
  display: inline-flex;
  align-items: center;
  background: var(--accent-soft);
  border: 1px solid #d0dde4;
  border-radius: 999px;
  padding: 0.1rem 0.15rem;
}
.chip code {
  background: transparent;
  border: 0;
  padding: 0.05rem 0.45rem;
}
.prompt-text {
  margin: 0 0 0.55rem;
  font-family: var(--serif);
  font-size: 1.05rem;
  line-height: 1.45;
}
.prompt-meta {
  margin: 0 0 0.75rem;
  color: var(--muted);
  font-size: 0.88rem;
}
.empty, .empty-note {
  color: var(--muted);
  font-style: italic;
}
.footer {
  margin-top: 1.5rem;
  color: var(--muted);
  font-size: 0.82rem;
  text-align: center;
}
@media (max-width: 640px) {
  .page { padding: 1.5rem 0.9rem 3rem; }
  .panel { padding: 1.05rem; }
  .detail-list li span { min-width: 100%; }
}
"""


def build_report_html(payload: Dict[str, Any]) -> str:
    """Build a polished single-file HTML report from validated payload."""
    display_names = get_db_display_names()
    stats = build_summary_stats(payload)
    results: List[Dict[str, Any]] = payload["results"]

    summary = render_summary_section(payload, stats)
    frequency = render_frequency_section(stats, display_names)
    toc = render_toc_section(results)
    prompts = render_prompts_section(results, display_names)
    css = build_report_css()

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>DB recommendations report</title>
  <style>{css}</style>
</head>
<body>
  <div class="page" id="top">
    <header class="hero">
      <p class="kicker">AIFAB Law · Classifier</p>
      <h1>DB recommendations report</h1>
      <p class="subtitle">
        Which statute databases the classifier selected for each research prompt,
        with confidence scores and run economics.
      </p>
    </header>
    {summary}
    {frequency}
    {toc}
    {prompts}
    <p class="footer">Generated from classifier/db_recommendations.json</p>
  </div>
</body>
</html>
"""


def write_report(json_path: str, out_path: str) -> str:
    """Load JSON, write HTML report, return the output path."""
    payload = validate_recommendations(load_recommendations(json_path))
    report_html = build_report_html(payload)
    os.makedirs(os.path.dirname(os.path.abspath(out_path)) or ".", exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(report_html)
    return out_path


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Render DB recommendation JSON as a single-file HTML report."
    )
    parser.add_argument(
        "--json",
        default=DEFAULT_JSON,
        help="Input JSON path (default: classifier/db_recommendations.json)",
    )
    parser.add_argument(
        "--out",
        default=DEFAULT_OUT,
        help="Output HTML path (default: classifier/index.html)",
    )
    args = parser.parse_args()

    if not os.path.exists(args.json):
        raise SystemExit(f"JSON not found: {args.json}")

    try:
        out_path = write_report(args.json, args.out)
    except ValueError as exc:
        raise SystemExit(f"Invalid recommendations JSON: {exc}") from exc
    print(f"Wrote {out_path}")


if __name__ == "__main__":
    main()
