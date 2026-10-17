#!/usr/bin/env python3
"""Generate deterministic synthetic support tickets."""

from __future__ import annotations

import argparse
import json
import random
from pathlib import Path

TENANTS = ("tenant-acme", "tenant-globex", "tenant-initech")
PRODUCTS = ("billing portal", "edge gateway", "analytics console")
ISSUES = (
    "The invoice export is missing the latest charge.",
    "Requests to the gateway return a timeout after deployment.",
    "The dashboard shows stale usage data for the last hour.",
    "A user cannot reset a password from the sign-in page.",
)


def generate(count: int = 30) -> list[dict[str, object]]:
    rng = random.Random(20260301)
    tickets = []
    for number in range(1, count + 1):
        tenant = TENANTS[(number - 1) % len(TENANTS)]
        tickets.append(
            {
                "id": f"ticket-{number:04d}",
                "tenant": tenant,
                "priority": rng.choice(("low", "normal", "high")),
                "product": rng.choice(PRODUCTS),
                "description": rng.choice(ISSUES),
            }
        )
    return tickets


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--count", type=int, default=30)
    parser.add_argument("--output", default="data/tickets.jsonl")
    args = parser.parse_args()
    path = Path(args.output)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as stream:
        for ticket in generate(args.count):
            stream.write(json.dumps(ticket, sort_keys=True) + "\n")
    print(f"wrote {args.count} tickets to {path}")


if __name__ == "__main__":
    main()
