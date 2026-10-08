#!/usr/bin/env python3
"""Compliance lint for affiliate captions and spoken scripts.

Flags price/deal/urgency claims, absolute or medical claims, wrong tracking
IDs and a missing disclosure. Exit code 1 when any issue is found.

    python3 lint_copy.py caption.txt [more.txt ...]
    echo "#ad text" | python3 lint_copy.py -
"""
from __future__ import annotations

import os
import re
import sys

BANNED = [
    (r"\$\s?\d", "price"),
    (r"\b\d+\s?(usd|dollars?|bucks)\b", "price"),
    (r"\bprime\b", "Prime claim"),
    (r"\b\d{1,3}\s?%\s?off\b", "percent-off claim"),
    (r"\b(deal|deals|sale|discount|coupon|promo code|markdown)\b", "deal claim"),
    (r"\b(today only|limited time|ends (today|tonight)|last chance|hurry|selling out|sold out|while supplies last)\b", "urgency claim"),
    (r"\b(lowest|cheapest|best) price\b", "price comparison"),
    (r"\b(guarantee|guaranteed|cures?|heals?|clinically proven|doctor recommended|miracle cure)\b", "absolute or medical claim"),
    (r"\b\d[\d,.]*\s?k?\+?\s?(people|customers|women|men) (switched|bought|swear)\b", "unverifiable social-proof count"),
]

WARN = [
    (r"amzn\.to/", "short link: prefer the full tagged product URL where the platform allows"),
]

DISCLOSURE = re.compile(r"(^|\s)#ad\b", re.I)
TAG_RE = re.compile(r"[?&]tag=([A-Za-z0-9_-]+)")


def lint(text: str, require_disclosure: bool = True, expected_tag: str | None = None) -> list[str]:
    issues: list[str] = []
    low = text.lower()
    for pat, label in BANNED:
        for m in re.finditer(pat, low):
            issues.append(f"{label}: '{text[m.start():m.end()]}'")
    for pat, label in WARN:
        if re.search(pat, low):
            issues.append(f"warning: {label}")
    if require_disclosure and not DISCLOSURE.search(text):
        issues.append("missing '#ad' disclosure")
    expected_tag = expected_tag or os.environ.get("AFFILIATE_TAG")
    if expected_tag:
        for t in TAG_RE.findall(text):
            if t != expected_tag:
                issues.append(f"tracking ID '{t}' does not match AFFILIATE_TAG '{expected_tag}'")
    return issues


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 2
    bad = 0
    for path in argv:
        text = sys.stdin.read() if path == "-" else open(path, encoding="utf-8").read()
        issues = [i for i in lint(text) if not i.startswith("warning:")]
        warns = [i for i in lint(text) if i.startswith("warning:")]
        for w in warns:
            print(f"{path}: {w}")
        if issues:
            bad += 1
            for i in issues:
                print(f"{path}: FAIL {i}")
        else:
            print(f"{path}: OK")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
