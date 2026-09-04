#!/usr/bin/env python3
"""
Look up electrical-symbol names against covaga/electrical-symbols-dataset
(Hugging Face) - what a short symbol name like "SL" or "K" actually means.

Background: eplan_live_symbol_catalog() gives real symbol names and pin
geometry from a project's own libraries, but never what a symbol MEANS - only
this dataset's `description` column supplies that (see
Testing/05-external-symbol-dataset.md for what it did and didn't confirm
live). Cross-check any hit against eplan_live_symbol_catalog() on the actual
project before using a name - this script's job is narrowing candidates, not
proving a name resolves.

Usage:
    python lookup_symbol_dataset.py --contains "contactor coil"
    python lookup_symbol_dataset.py --short-name SL
    python lookup_symbol_dataset.py --number 0
    python lookup_symbol_dataset.py --contains "push button" --limit 20

First run downloads the ~120MB parquet file to a local cache (default:
./.cache/electrical-symbols.parquet, next to this script - already covered by
the repo's .gitignore) and reuses it after that. Pass --refresh to re-download.

Requires: pandas, pyarrow, requests (pip install pandas pyarrow requests)
"""

import argparse
import sys
from pathlib import Path

DATASET_URL = (
    "https://huggingface.co/datasets/covaga/electrical-symbols-dataset/"
    "resolve/main/data/train-00000-of-00001.parquet"
)
DEFAULT_CACHE = Path(__file__).parent / ".cache" / "electrical-symbols.parquet"


def ensure_dataset(cache_path: Path, refresh: bool = False) -> Path:
    if cache_path.exists() and not refresh:
        return cache_path
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        import requests
    except ImportError:
        sys.exit("Missing dependency: pip install requests")

    print(f"Downloading dataset (~120MB) to {cache_path} ...", file=sys.stderr)
    with requests.get(DATASET_URL, stream=True) as r:
        r.raise_for_status()
        total = int(r.headers.get("content-length", 0))
        done = 0
        with open(cache_path, "wb") as f:
            for chunk in r.iter_content(chunk_size=1 << 20):
                f.write(chunk)
                done += len(chunk)
                if total:
                    pct = 100 * done / total
                    print(f"\r  {pct:5.1f}%", end="", file=sys.stderr)
    print("\ndone.", file=sys.stderr)
    return cache_path


def load_index(cache_path: Path):
    try:
        import pandas as pd
    except ImportError:
        sys.exit("Missing dependency: pip install pandas pyarrow")
    # file_name is an embedded-image column - never load it here, it is most
    # of the file's weight and this script only ever needs the text columns.
    df = pd.read_parquet(
        cache_path,
        columns=["short_name", "number", "description", "variant_id"],
    )
    return df.drop_duplicates("short_name")[["short_name", "number", "description"]]


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                  formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--short-name", help="Exact short_name match (case-insensitive), e.g. SL")
    ap.add_argument("--number", type=int, help="Exact numeric id match (the same id "
                     "eplan_insert_symbol_reference's SymbolId expects). NOT unique across "
                     "the whole dataset - it's scoped per symbol library/family, so "
                     "--number 20 alone returns several unrelated symbols; pair it with "
                     "--contains or check --short-name too before trusting a match")
    ap.add_argument("--contains", help="Case-insensitive substring match on description")
    ap.add_argument("--limit", type=int, default=25, help="Max rows to print (default 25)")
    ap.add_argument("--cache", default=str(DEFAULT_CACHE), help="Local parquet cache path")
    ap.add_argument("--refresh", action="store_true", help="Re-download even if cached")
    args = ap.parse_args()

    if not (args.short_name or args.number is not None or args.contains):
        ap.error("pass at least one of --short-name / --number / --contains")

    cache_path = ensure_dataset(Path(args.cache), refresh=args.refresh)
    df = load_index(cache_path)

    if args.short_name:
        df = df[df["short_name"].str.lower() == args.short_name.lower()]
    if args.number is not None:
        df = df[df["number"] == str(args.number)]
    if args.contains:
        df = df[df["description"].str.contains(args.contains, case=False, na=False)]

    if df.empty:
        print("No matches.")
        return

    for _, row in df.head(args.limit).iterrows():
        print(f"{row['short_name']:<12} {row['number']:<6} {row['description']}")
    if len(df) > args.limit:
        print(f"... {len(df) - args.limit} more (raise --limit to see them)")


if __name__ == "__main__":
    main()
