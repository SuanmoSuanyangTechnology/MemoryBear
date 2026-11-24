"""Surface unmatched gateway paths (would-be 404s) from a Prometheus scrape.

Usage: reconcile_unmatched.py --metrics http://gateway-service:8002/metrics \
         [--allow /api/old] [--top 20]
Exit 1 when any non-exempt label has traffic.
"""
from __future__ import annotations

import argparse
import re
import sys
from urllib.request import urlopen

_LINE_RE = re.compile(
    r'gateway_unmatched_route_total\{path_prefix="([^"]+)"\}\s+([0-9.eE+-]+)')


def parse_metrics(text: str) -> dict[str, float]:
    return {m.group(1): float(m.group(2)) for m in _LINE_RE.finditer(text)}


def find_unallowed(items: dict[str, float], allowed: set[str]) -> dict[str, float]:
    return {k: v for k, v in items.items() if k not in allowed and v > 0}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--metrics", required=True)
    parser.add_argument("--allow", action="append", default=[])
    parser.add_argument("--top", type=int, default=20)
    args = parser.parse_args(argv)
    if args.metrics.startswith(("http://", "https://")):
        with urlopen(args.metrics, timeout=5) as resp:  # noqa: S310
            text = resp.read().decode()
    else:
        with open(args.metrics, encoding="utf-8") as fh:
            text = fh.read()
    unallowed = find_unallowed(parse_metrics(text), set(args.allow))
    for path, count in sorted(unallowed.items(), key=lambda kv: -kv[1])[:args.top]:
        print(f"{count:>10.0f}  {path}")
    print("OK" if not unallowed else f"{len(unallowed)} unmatched prefix(es)")
    return 0 if not unallowed else 1


if __name__ == "__main__":
    sys.exit(main())
