"""Deterministic synthetic telemetry; never uses production or personal data."""
from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path


def generate_records(count: int, seed: int = 17) -> list[dict[str, int | bool]]:
    if count < 0:
        raise ValueError("count must be nonnegative")
    rng = random.Random(seed)
    # Include exact threshold values and unsigned extremes in every sizeable dataset.
    edges = [
        {"status": 200, "latency_us": 0, "bytes": 0, "cached": True},
        {"status": 499, "latency_us": 999, "bytes": 128, "cached": False},
        {"status": 500, "latency_us": 1000, "bytes": 256, "cached": False},
        {"status": 503, "latency_us": 1001, "bytes": (1 << 64) - 1, "cached": True},
    ]
    records = edges[:count]
    for _ in range(len(records), count):
        records.append({"status": rng.choice((200, 200, 200, 201, 404, 499, 500, 503)),
                        "latency_us": rng.randrange(10000), "bytes": rng.randrange(1 << 20),
                        "cached": bool(rng.getrandbits(1))})
    return records


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rows", type=int, default=10000)
    parser.add_argument("--seed", type=int, default=17)
    parser.add_argument("--output", default="-")
    args = parser.parse_args()
    lines = "".join(json.dumps(row, separators=(",", ":")) + "\n" for row in generate_records(args.rows, args.seed))
    if args.output == "-":
        sys.stdout.write(lines)
    else:
        Path(args.output).write_text(lines, encoding="utf-8")


if __name__ == "__main__":
    main()
