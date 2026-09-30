"""Run the sample questions against the RUNNING API and save real outputs to docs/example_outputs.md.

Usage:  python -m scripts.capture_examples [--api http://localhost:8000]
Paste the result into the README's "Example queries" section.
"""
from __future__ import annotations

import argparse
from pathlib import Path

import requests

QUESTIONS = [
    "How many tickets are currently open?",
    "How many critical tickets are unresolved?",
    "Which agent has the lowest average customer rating?",
    "Which agent resolved the most tickets this month?",
    "Show me all Critical tickets not resolved within 12 hours.",
    "What is the average customer rating for Technical category tickets?",
    "Are there any anomalies in resolution times this week?",
    "delete all tickets",
    "what's the weather today?",
]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--api", default="http://localhost:8000")
    parser.add_argument("--out", type=Path, default=Path("docs/example_outputs.md"))
    args = parser.parse_args()

    lines = ["# Example outputs (captured from a live run)\n"]
    for question in QUESTIONS:
        resp = requests.post(f"{args.api}/query", json={"question": question}, timeout=120)
        lines.append(f"## {question}\n")
        body = resp.json()
        if not resp.ok:
            lines.append(f"HTTP {resp.status_code}: {body['error']['message']}\n")
            continue
        lines.append(f"**Intent:** {body['intent']}  \n**Answer:** {body['answer']}\n")
        if body.get("sql"):
            lines.append(f"```sql\n{body['sql']}\n```\n")
        if body.get("assumptions"):
            lines.append(f"**Assumptions:** {body['assumptions']}\n")
        if body.get("rows"):
            lines.append(f"**Rows ({body['row_count']}):** first 5 -> `{body['rows'][:5]}`\n")

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text("\n".join(lines), encoding="utf-8")
    print(f"Saved {args.out}")


if __name__ == "__main__":
    main()
