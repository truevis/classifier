"""Classify research prompts into Texas statute databases via OpenRouter Jev.

Requires OPENROUTER_API_KEY in the environment.

Example:
  set OPENROUTER_API_KEY=sk-or-...
  python choose_db.py
  python choose_db.py --prompts prompts.csv --out db_recommendations.json
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests

HERE = Path(__file__).resolve().parent
DEFAULT_PROMPTS = HERE / "prompts.csv"
DEFAULT_DATABASES = HERE / "databases.json"
DEFAULT_OUT = HERE / "db_recommendations.json"

JEV_DECISIONS_URL = "https://openrouter.ai/api/alpha/decisions"
DEFAULT_MODEL = "~typesafe/jev-latest"
DEFAULT_THRESHOLD = 0.5
DEFAULT_TOP_K = 3


def get_api_key() -> str:
    """Read OPENROUTER_API_KEY from the environment."""
    key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if not key:
        raise RuntimeError(
            "OPENROUTER_API_KEY is not set. Export your OpenRouter API key first.\n"
            "  Windows: set OPENROUTER_API_KEY=sk-or-...\n"
            "  macOS/Linux: export OPENROUTER_API_KEY=sk-or-..."
        )
    return key


def load_prompts(path: Path | str) -> List[str]:
    """Load prompts from a one-column CSV (optional header)."""
    path = Path(path)
    prompts: List[str] = []
    with path.open("r", encoding="utf-8") as f:
        sample = f.read(1024)
        f.seek(0)
        has_header = False
        if sample.strip():
            try:
                has_header = csv.Sniffer().has_header(sample)
            except csv.Error:
                has_header = False
        reader = csv.reader(f)
        if has_header:
            next(reader, None)
        for row in reader:
            if row and str(row[0]).strip():
                prompts.append(str(row[0]).strip())
    return prompts


def load_databases(path: Path | str) -> List[Dict[str, str]]:
    """Load [{code, name, description}, ...] from databases.json."""
    path = Path(path)
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list) or not data:
        raise ValueError(f"No databases found in {path}")
    for i, row in enumerate(data):
        for key in ("code", "name", "description"):
            if key not in row:
                raise ValueError(f"databases.json[{i}] missing '{key}'")
    return data


def build_state(prompt: str, databases: List[Dict[str, str]]) -> Dict[str, Any]:
    """State for Jev: user prompt + short catalog lines."""
    catalog = [
        f"- {db['code']} ({db['name']}): {db['description']}"
        for db in databases
    ]
    return {"prompt": prompt, "available_databases": catalog}


def build_questions(databases: List[Dict[str, str]]) -> Dict[str, Dict[str, str]]:
    """One noul (yes/no probability) question per database code."""
    questions: Dict[str, Dict[str, str]] = {}
    for db in databases:
        questions[db["code"]] = {
            "type": "noul",
            "instructions": (
                f"This Texas legal research prompt should search the {db['name']} "
                f"database ({db['description']})."
            ),
        }
    return questions


def call_jev(
    state: Any,
    questions: Dict[str, Dict[str, str]],
    api_key: str,
    model: str = DEFAULT_MODEL,
    timeout: int = 120,
) -> Tuple[Dict[str, Any], Dict[str, int], float]:
    """POST OpenRouter Decisions API. Returns answers, usage, elapsed seconds."""
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://github.com/truevis/classifier",
        "X-Title": "classifier",
    }
    payload = {"model": model, "state": state, "questions": questions}
    started = time.perf_counter()
    response = requests.post(
        JEV_DECISIONS_URL, headers=headers, json=payload, timeout=timeout
    )
    elapsed = time.perf_counter() - started
    if response.status_code != 200:
        raise RuntimeError(
            f"Jev Decisions API error {response.status_code}: {response.text[:500]}"
        )
    data = response.json()
    answers = data.get("answers")
    if not isinstance(answers, dict):
        raise RuntimeError(f"Unexpected Decisions API response: {data!r}")
    usage_raw = data.get("usage") or {}
    usage = {
        "input_tokens": int(usage_raw.get("prompt_tokens") or usage_raw.get("input_tokens") or 0),
        "output_tokens": int(usage_raw.get("completion_tokens") or usage_raw.get("output_tokens") or 0),
        "total_tokens": int(usage_raw.get("total_tokens") or 0),
    }
    if usage["total_tokens"] == 0:
        usage["total_tokens"] = usage["input_tokens"] + usage["output_tokens"]
    return answers, usage, elapsed


def recommend_dbs(
    answers: Dict[str, Any],
    threshold: float = DEFAULT_THRESHOLD,
    top_k: int = DEFAULT_TOP_K,
) -> Tuple[List[str], Dict[str, float]]:
    """Keep codes with noul >= threshold; else top-k by score."""
    scores: Dict[str, float] = {}
    for code, answer in answers.items():
        if not isinstance(answer, dict):
            continue
        noul = answer.get("noul")
        if noul is None:
            continue
        scores[code] = float(noul)
    ranked = sorted(scores.items(), key=lambda item: item[1], reverse=True)
    recommended = [code for code, score in ranked if score >= threshold]
    if not recommended and ranked:
        recommended = [code for code, _ in ranked[: max(1, top_k)]]
    return recommended, scores


def classify_prompt(
    prompt: str,
    databases: List[Dict[str, str]],
    api_key: Optional[str] = None,
    model: str = DEFAULT_MODEL,
    threshold: float = DEFAULT_THRESHOLD,
    top_k: int = DEFAULT_TOP_K,
) -> Dict[str, Any]:
    """Classify one prompt. Returns recommended codes, scores, and meta."""
    key = api_key or get_api_key()
    state = build_state(prompt, databases)
    questions = build_questions(databases)
    answers, usage, elapsed = call_jev(state, questions, api_key=key, model=model)
    recommended, scores = recommend_dbs(answers, threshold=threshold, top_k=top_k)
    result_scores = {code: scores[code] for code in recommended if code in scores}
    return {
        "prompt": prompt,
        "recommended_databases": recommended,
        "scores": result_scores,
        "elapsed_seconds": round(elapsed, 4),
        "usage": usage,
    }


def run_classification(
    prompts: List[str],
    databases: List[Dict[str, str]],
    out_path: Path,
    api_key: Optional[str] = None,
    model: str = DEFAULT_MODEL,
    threshold: float = DEFAULT_THRESHOLD,
    top_k: int = DEFAULT_TOP_K,
) -> Dict[str, Any]:
    """Classify all prompts and write JSON."""
    key = api_key or get_api_key()
    results: List[Dict[str, Any]] = []
    total_usage = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
    total_query = 0.0
    wall_start = time.perf_counter()

    for index, prompt in enumerate(prompts):
        print(f"[{index + 1}/{len(prompts)}] {prompt[:80]}...")
        row = classify_prompt(
            prompt,
            databases,
            api_key=key,
            model=model,
            threshold=threshold,
            top_k=top_k,
        )
        row["prompt_index"] = index
        results.append(row)
        print(f"  -> {row['recommended_databases']}")
        for k in total_usage:
            total_usage[k] += int(row["usage"].get(k, 0))
        total_query += float(row["elapsed_seconds"])

    payload = {
        "model": model,
        "threshold": threshold,
        "top_k_fallback": top_k,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "usage": total_usage,
        "timing": {
            "total_query_seconds": round(total_query, 4),
            "wall_clock_seconds": round(time.perf_counter() - wall_start, 4),
            "queries": len(prompts),
        },
        "results": results,
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Wrote {out_path}")
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Recommend Texas statute DBs via OpenRouter Jev Decisions API."
    )
    parser.add_argument("--prompts", type=Path, default=DEFAULT_PROMPTS)
    parser.add_argument("--databases", type=Path, default=DEFAULT_DATABASES)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD)
    parser.add_argument("--top-k", type=int, default=DEFAULT_TOP_K, dest="top_k")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    args = parser.parse_args()

    prompts = load_prompts(args.prompts)
    if not prompts:
        raise SystemExit(f"No prompts in {args.prompts}")
    databases = load_databases(args.databases)
    run_classification(
        prompts=prompts,
        databases=databases,
        out_path=args.out,
        model=args.model,
        threshold=args.threshold,
        top_k=args.top_k,
    )


if __name__ == "__main__":
    main()
