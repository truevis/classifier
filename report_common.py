"""Shared helpers for recommendation reports."""

from __future__ import annotations

import json
from collections import Counter
from typing import Any, Dict, List, Tuple

REQUIRED_TOP_KEYS = ("model", "threshold", "top_k_fallback", "results")
REQUIRED_RESULT_KEYS = (
    "prompt_index",
    "prompt",
    "recommended_databases",
    "scores",
)


def load_recommendations(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def validate_recommendations(payload: Any) -> Dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("Recommendations JSON must be an object.")
    missing = [k for k in REQUIRED_TOP_KEYS if k not in payload]
    if missing:
        raise ValueError("Missing top-level keys: " + ", ".join(missing))
    for optional_key in ("usage", "timing", "pricing"):
        if optional_key in payload and not isinstance(payload[optional_key], dict):
            raise ValueError(f"'{optional_key}' must be an object when present.")
    results = payload["results"]
    if not isinstance(results, list):
        raise ValueError("'results' must be a list.")
    for index, row in enumerate(results):
        if not isinstance(row, dict):
            raise ValueError(f"Result {index} must be an object.")
        missing_row = [k for k in REQUIRED_RESULT_KEYS if k not in row]
        if missing_row:
            raise ValueError(f"Result {index} missing: " + ", ".join(missing_row))
        if not isinstance(row["recommended_databases"], list):
            raise ValueError(f"Result {index}: recommended_databases must be a list.")
        if not isinstance(row["scores"], dict):
            raise ValueError(f"Result {index}: scores must be an object.")
    return payload


def format_score(score: Any) -> str:
    if score is None:
        return "n/a"
    try:
        return f"{float(score):.3f}"
    except (TypeError, ValueError):
        return "n/a"


def ranked_score_rows(
    recommended: List[str], scores: Dict[str, Any]
) -> List[Tuple[str, Any]]:
    def sort_key(item: Tuple[str, Any]) -> Tuple[float, str]:
        code, score = item
        try:
            return (float(score), code)
        except (TypeError, ValueError):
            return (float("-inf"), code)

    rows: List[Tuple[str, Any]] = [(code, scores.get(code)) for code in recommended]
    for code, score in scores.items():
        if code not in recommended:
            rows.append((code, score))
    return sorted(rows, key=sort_key, reverse=True)


def build_summary_stats(payload: Dict[str, Any]) -> Dict[str, Any]:
    results = payload["results"]
    freq: Counter = Counter()
    total = 0
    for row in results:
        rec = row.get("recommended_databases") or []
        freq.update(rec)
        total += len(rec)
    n = len(results)
    return {
        "freq": freq,
        "n_prompts": n,
        "unique_dbs": len(freq),
        "avg_dbs": (total / n) if n else 0.0,
        "total_recs": total,
    }
