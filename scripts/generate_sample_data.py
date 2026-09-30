"""Generate a realistic demo dataset (only needed if you don't have the real support_tickets.csv).

Usage:  python -m scripts.generate_sample_data [--rows 500] [--seed 42] [--force]

Deliberately injected anomalies (so the detector has something to find):
  * 3 resolved tickets with extreme resolution times (150-400h)
  * 4 old, still-Open Critical tickets
  * 2 tickets whose resolution time is shorter than their response time
  * AGT-07 is a weak agent (slower, lower ratings)
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

ISSUES = {
    "Billing": ["Incorrect charge on invoice", "Refund not processed", "Duplicate payment", "Cannot update card details"],
    "Technical": ["Login failure after update", "API timeout in production", "Dashboard not loading", "Data export failing"],
    "General": ["Request for product docs", "How to change account email", "Feature request", "Question about pricing plans"],
}
RES_MEDIAN = {"Low": 8.0, "Medium": 6.0, "High": 4.5, "Critical": 4.0}
RESP_MEDIAN = {"Low": 3.0, "Medium": 2.0, "High": 1.0, "Critical": 0.5}
AGENTS = [f"AGT-{i:02d}" for i in range(1, 11)]
WEAK_AGENT = "AGT-07"


def generate(n: int = 500, seed: int = 42) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    start = datetime(2024, 1, 3, 8, 0)
    offsets = np.sort(rng.uniform(0, 88 * 24, n))  # ~88 days of tickets

    category = rng.choice(list(ISSUES), n, p=[0.3, 0.4, 0.3])
    priority = rng.choice(list(RES_MEDIAN), n, p=[0.25, 0.4, 0.25, 0.1])
    status = rng.choice(["Resolved", "Open", "Escalated"], n, p=[0.72, 0.16, 0.12])
    agent = rng.choice(AGENTS, n)

    rows = []
    for i in range(n):
        weak = agent[i] == WEAK_AGENT
        resp = round(float(RESP_MEDIAN[priority[i]] * rng.lognormal(0, 0.5)), 1)
        resolved = status[i] == "Resolved"
        res = round(resp + float(RES_MEDIAN[priority[i]] * rng.lognormal(0, 0.5) * (1.6 if weak else 1.0)), 1) if resolved else None
        rating = None
        if resolved:
            mean = 4.1 - (1.3 if weak else 0.0) - min(res / 40, 1.0)
            rating = int(np.clip(round(rng.normal(mean, 0.8)), 1, 5))
        rows.append(
            {
                "ticket_id": f"TKT-{i + 1:03d}",
                "created_at": (start + timedelta(hours=float(offsets[i]))).strftime("%Y-%m-%d %H:%M"),
                "category": category[i],
                "priority": priority[i],
                "status": status[i],
                "response_time_hrs": resp,
                "resolution_time_hrs": res,
                "agent_id": agent[i],
                "customer_rating": rating,
                "issue_summary": str(rng.choice(ISSUES[category[i]])),
            }
        )
    df = pd.DataFrame(rows)

    # --- inject anomalies -------------------------------------------------
    resolved_idx = df.index[(df["status"] == "Resolved") & (df.index > n // 2)]
    for idx, hours in zip(rng.choice(resolved_idx, 3, replace=False), (150.0, 260.0, 400.0)):
        df.loc[idx, "resolution_time_hrs"] = hours
    for idx in range(5, 9):  # old unresolved Critical tickets
        df.loc[idx, ["priority", "status", "resolution_time_hrs", "customer_rating"]] = ["Critical", "Open", None, None]
    for idx in rng.choice(resolved_idx, 2, replace=False):  # resolution faster than response
        df.loc[idx, ["response_time_hrs", "resolution_time_hrs"]] = [6.0, 1.5]
    return df


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--rows", type=int, default=500)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--out", type=Path, default=Path("data/support_tickets.csv"))
    parser.add_argument("--force", action="store_true", help="overwrite an existing file")
    args = parser.parse_args()

    if args.out.exists() and not args.force:
        raise SystemExit(f"{args.out} already exists. Use --force to overwrite it (your real data will be lost!).")
    args.out.parent.mkdir(parents=True, exist_ok=True)
    generate(args.rows, args.seed).to_csv(args.out, index=False)
    print(f"Wrote {args.rows} rows to {args.out}")


if __name__ == "__main__":
    main()
