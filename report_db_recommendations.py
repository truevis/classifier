"""Turn classifier/db_recommendations.json into a markdown report."""

from __future__ import annotations

import argparse
import os
import sys
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
DEFAULT_OUT = os.path.join(CURRENT_DIR, "db_recommendations_report.md")


def escape_markdown_cell(text: str) -> str:
    """Escape characters that break markdown tables or headings."""
    escaped = (
        str(text)
        .replace("\\", "\\\\")
        .replace("|", "\\|")
        .replace("\r\n", " ")
        .replace("\n", " ")
        .replace("\r", " ")
    )
    if escaped.lstrip().startswith("#"):
        leading = len(escaped) - len(escaped.lstrip())
        escaped = escaped[:leading] + "\\" + escaped[leading:]
    return escaped


def escape_markdown_prompt(text: str) -> str:
    """Escape prompt text so it cannot break surrounding markdown structure."""
    lines: List[str] = []
    for line in str(text).splitlines() or [""]:
        if line.lstrip().startswith("#"):
            leading = len(line) - len(line.lstrip())
            line = line[:leading] + "\\" + line[leading:]
        lines.append(line.replace("|", "\\|"))
    return "\n".join(lines)


def build_report_markdown(payload: Dict[str, Any]) -> str:
    """Build markdown summary + frequency table + per-prompt sections."""
    display_names = get_db_display_names()
    stats = build_summary_stats(payload)
    results: List[Dict[str, Any]] = payload["results"]
    model = payload["model"]
    threshold = payload["threshold"]
    top_k = payload["top_k_fallback"]
    freq = stats["freq"]
    n_prompts = stats["n_prompts"]

    usage = payload.get("usage") or {}
    timing = payload.get("timing") or {}
    pricing = payload.get("pricing") or {}
    estimated_cost = payload.get("estimated_cost_usd")
    generated_at = payload.get("generated_at")

    lines: List[str] = [
        "# DB recommendations report",
        "",
        f"- Model: {model}",
        f"- Threshold: {threshold}",
        f"- Top-k fallback: {top_k}",
        f"- Prompts: {n_prompts}",
        f"- Unique recommended DBs: {stats['unique_dbs']}",
        f"- Average DBs per prompt: {stats['avg_dbs']:.2f}",
    ]

    if generated_at:
        lines.append(f"- Generated at (UTC): {generated_at}")
    if usage:
        input_tokens = usage.get("input_tokens", "n/a")
        output_tokens = usage.get("output_tokens", "n/a")
        total_tokens = usage.get("total_tokens", "n/a")
        lines.append(
            f"- Tokens: input={input_tokens}, output={output_tokens}, "
            f"total={total_tokens}"
        )
    if estimated_cost is not None:
        lines.append(f"- Estimated cost (USD): ")
    if pricing:
        in_rate = pricing.get("input_usd_per_million")
        out_rate = pricing.get("output_usd_per_million")
        if in_rate is not None or out_rate is not None:
            lines.append(
                f"- Pricing: /M input, /M output "
                f"({pricing.get('source', 'n/a')})"
            )
    if timing:
        query_s = timing.get("total_query_seconds")
        wall_s = timing.get("wall_clock_seconds")
        if query_s is not None:
            lines.append(f"- Total query time (s): {float(query_s):.2f}")
        if wall_s is not None:
            lines.append(f"- Wall-clock time (s): {float(wall_s):.2f}")

    lines.extend(
        [
            "",
            "## Frequency by database",
            "",
            "| Code | Display name | Count | % of prompts |",
            "| --- | --- | ---: | ---: |",
        ]
    )

    for code, count in freq.most_common():
        name = escape_markdown_cell(display_names.get(code, code))
        pct = (100.0 * count / n_prompts) if n_prompts else 0.0
        lines.append(
            f"| {escape_markdown_cell(code)} | {name} | {count} | {pct:.1f}% |"
        )

    if not freq:
        lines.append("| - | - | 0 | n/a |")

    lines.extend(["", "## Per-prompt recommendations", ""])

    for row in results:
        index = row.get("prompt_index", "?")
        prompt = escape_markdown_prompt(str(row.get("prompt", "")).strip())
        recommended = row.get("recommended_databases") or []
        scores = row.get("scores") or {}

        lines.append(f"### Prompt {index}")
        lines.append("")
        lines.append(prompt)
        lines.append("")

        elapsed = row.get("elapsed_seconds")
        row_usage = row.get("usage") or {}
        row_cost = row.get("estimated_cost_usd")
        meta_bits: List[str] = []
        if elapsed is not None:
            meta_bits.append(f"{float(elapsed):.2f}s")
        if row_usage.get("input_tokens") is not None:
            meta_bits.append(f"{row_usage.get('input_tokens')} input tokens")
        if row_cost is not None:
            meta_bits.append(f"")
        if meta_bits:
            lines.append(f"_Query: {', '.join(meta_bits)}_")
            lines.append("")

        if not recommended:
            lines.append("_No databases recommended._")
            lines.append("")
            continue

        lines.append("| Code | Display name | Score |")
        lines.append("| --- | --- | ---: |")
        for code, score in ranked_score_rows(recommended, scores):
            if code not in recommended:
                continue
            name = escape_markdown_cell(display_names.get(code, code))
            score_str = format_score(score)
            lines.append(
                f"| {escape_markdown_cell(code)} | {name} | {score_str} |"
            )
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def write_report(json_path: str, out_path: str) -> str:
    """Load JSON, write markdown report, return the markdown path."""
    payload = validate_recommendations(load_recommendations(json_path))
    markdown = build_report_markdown(payload)
    os.makedirs(os.path.dirname(os.path.abspath(out_path)) or ".", exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(markdown)
    return out_path


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Render DB recommendation JSON as markdown."
    )
    parser.add_argument(
        "--json",
        default=DEFAULT_JSON,
        help="Input JSON path (default: classifier/db_recommendations.json)",
    )
    parser.add_argument(
        "--out",
        default=DEFAULT_OUT,
        help="Output markdown path (default: classifier/db_recommendations_report.md)",
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
