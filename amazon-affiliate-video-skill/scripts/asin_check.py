#!/usr/bin/env python3
"""Validate an ASIN and print the clean product URL and your tagged link.

This does NOT replace a live check: open the product page today and confirm
the title, variant and stock match the pack you will show, then record a
one-line proof in the sidecar (asin_live_proof).

    python3 asin_check.py B0XXXXXXXX [--tag YOUR_TAG-20]
"""
from __future__ import annotations

import argparse
import os
import re
import sys

ASIN_RE = re.compile(r"^(B0[A-Z0-9]{8}|\d{9}[\dX])$")


def is_valid_asin(asin: str) -> bool:
    return bool(ASIN_RE.match(asin.strip().upper()))


def product_url(asin: str) -> str:
    return f"https://www.amazon.com/dp/{asin.strip().upper()}"


def tagged_url(asin: str, tag: str) -> str:
    return f"{product_url(asin)}?tag={tag}"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("asin")
    ap.add_argument("--tag", default=os.environ.get("AFFILIATE_TAG", "YOUR_TAG-20"))
    a = ap.parse_args()
    if not is_valid_asin(a.asin):
        print(f"INVALID ASIN: {a.asin!r} (expected 10 chars like B0XXXXXXXX)")
        return 1
    print(f"product page : {product_url(a.asin)}")
    print(f"tagged link  : {tagged_url(a.asin, a.tag)}")
    print("next         : open the product page, confirm title/variant/stock, record asin_live_proof")
    return 0


if __name__ == "__main__":
    sys.exit(main())
