"""Fetch and pin the mod sources named in a build config.

    python tools/vendor_sync.py            # sync to pinned commits
    python tools/vendor_sync.py --check    # report drift without fetching
    python tools/vendor_sync.py --update   # move pins to current branch tips
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pipeline import config as build_config
from pipeline.vendor import GitSource, VendorError, remote_head, sync, verify_layout


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default=build_config.DEFAULT_CONFIG, type=Path)
    parser.add_argument(
        "--check",
        action="store_true",
        help="report whether pinned commits still match the tracked refs, and fetch nothing",
    )
    parser.add_argument(
        "--update",
        action="store_true",
        help="print the config edits needed to move each pin to its branch tip",
    )
    parser.add_argument("--force", action="store_true", help="re-checkout even if up to date")
    args = parser.parse_args()

    cfg = build_config.load(args.config)
    git_sources = [s for s in (*cfg.sources, *cfg.reference_sources) if isinstance(s, GitSource)]

    if not git_sources:
        print(f"{args.config}: no git sources to sync")
        return 0

    if args.check or args.update:
        return _report_drift(git_sources, update=args.update)

    failures = 0
    for source in git_sources:
        print(f"syncing {source.display_name} ({source.key})")
        try:
            result = sync(source, cfg.vendor_root, force=args.force)
        except VendorError as exc:
            print(f"  FAILED: {exc}", file=sys.stderr)
            failures += 1
            continue

        state = "fetched" if result.fetched else "already current"
        print(f"  {state} at {result.short_commit} -> {result.path}")
        for note in result.notes:
            print(f"  note: {note}")
        for problem in verify_layout(result):
            print(f"  WARNING: {problem}", file=sys.stderr)

    return 1 if failures else 0


def _report_drift(sources: list[GitSource], *, update: bool) -> int:
    drifted = 0
    for source in sources:
        try:
            head = remote_head(source.repo, source.ref)
        except VendorError as exc:
            print(f"{source.key}: could not reach remote: {exc}", file=sys.stderr)
            drifted += 1
            continue

        if source.commit is None:
            print(f"{source.key}: not pinned; {source.ref} is at {head[:12]}")
            drifted += 1
        elif head == source.commit:
            print(f"{source.key}: current ({head[:12]})")
        else:
            drifted += 1
            print(f"{source.key}: PINNED {source.commit[:12]} but {source.ref} is at {head[:12]}")
            if update:
                print(f'    commit = "{head}"')

    if drifted and not update:
        print(
            f"\n{drifted} source(s) have moved. Re-pin deliberately, then rebuild "
            "and re-check the corpus counts in tests/."
        )
    return 1 if drifted else 0


if __name__ == "__main__":
    raise SystemExit(main())
