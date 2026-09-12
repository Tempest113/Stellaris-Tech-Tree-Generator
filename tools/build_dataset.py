"""Build the tech tree dataset from a local game install and pinned mod sources.

    python tools/build_dataset.py
    python tools/build_dataset.py --out client/public/data

Runs locally, never in CI: base game data cannot be redistributed, so the
dataset is built here and published as a release artifact.
"""

from __future__ import annotations

import argparse
from collections import Counter
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pipeline import config as build_config
from pipeline import emit as emit_mod
from pipeline import gates as gates_mod
from pipeline import unlocks as unlocks_mod
from pipeline import graph as graph_mod
from pipeline import layout as layout_mod
from pipeline import localisation as loc_mod
from pipeline import rows as rows_mod
from pipeline.config import LocalSource, WorkshopSource
from pipeline.loadorder import LoadOrder, base_game_source, mod_source
from pipeline.records import extract
from pipeline.steam import find_install
from pipeline.vendor import GitSource

#: Files whose technologies belong to a crisis family that is not yet fully
#: classified. Used only to decide what the review report should mention.
CRISIS_ADJACENT_FILES = {
    "giga_08_ehof_components.txt": "E.H.O.F. / Compound family",
    "giga_09_ehof_other.txt": "E.H.O.F. / Compound family",
}
CRISIS_ADJACENT_PREFIXES = ("tech_qnm_", "tech_sm_", "tech_nm_")


def resolve_source(source, install, vendor_root: Path):
    """Turn a configured source into a load-order entry."""
    if isinstance(source, GitSource):
        root = vendor_root / source.key
        if not (root / "common").is_dir():
            raise SystemExit(
                f"{source.key}: nothing vendored at {root}. Run tools/vendor_sync.py first."
            )
        return mod_source(root, key=source.key, name=source.name)
    if isinstance(source, WorkshopSource):
        root = install.workshop_mod(source.workshop_id)
        if root is None:
            return None
        return mod_source(root, key=source.key, name=source.name)
    if isinstance(source, LocalSource):
        return mod_source(source.path, key=source.key, name=source.name)
    raise SystemExit(f"unknown source type: {type(source).__name__}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default=build_config.DEFAULT_CONFIG, type=Path)
    parser.add_argument("--rows", default=rows_mod.DEFAULT_ROWS_CONFIG, type=Path)
    parser.add_argument("--out", type=Path, help="output directory (default: <output_root>/data)")
    args = parser.parse_args()

    cfg = build_config.load(args.config)
    install = find_install() if cfg.game_path is None else None
    game = cfg.game_path or install.game
    version = install.version if install else None

    load_order = LoadOrder().add(base_game_source(game, version))
    for source in cfg.sources:
        entry = resolve_source(source, install, cfg.vendor_root)
        if entry is None:
            raise SystemExit(f"{source.key}: not installed")
        load_order.add(entry)

    reference = LoadOrder()
    for source in cfg.reference_sources:
        entry = resolve_source(source, install, cfg.vendor_root)
        if entry is None:
            print(f"  note: reference source {source.key} not installed; skipping")
            continue
        reference.add(entry)

    print("load order:")
    for source in load_order:
        print(f"  - {source}")

    extraction = extract(load_order, reference_load_order=reference or None)
    print(f"\nextract: {extraction.summary()}")
    for problem in extraction.problems:
        print(f"  warning: {problem}")
    if extraction.icons:
        print(f"icons:   {extraction.icons.summary()}")
        missing = extraction.icons.missing_icon_keys()
        if missing:
            print(f"  {len(missing)} technologies use the placeholder: {', '.join(missing)}")

    if extraction.triggers:
        print(f"triggers: {extraction.triggers.summary()}")
    if extraction.unlocks:
        print(f"unlocks: {extraction.unlocks.summary()}")
        for source in load_order:
            if not (source.root / "events").is_dir():
                print(
                    f"  note: {source.name} has no events/ -- grants in its events are "
                    "not traced, and its event-granted technologies fall back to 'Event'"
                )
        tags = Counter(
            unlocks_mod.tag_for(record, extraction.unlock_routes.get(key, ()), extraction.unlock_config)
            for key, record in extraction.technologies.items()
        )
        del tags[None]
        print("  tags: " + ", ".join(f"{tag} {count}" for tag, count in tags.most_common()))

    graph = graph_mod.build(extraction)
    print(f"graph:   {graph.summary()}")
    effective = gates_mod.with_inherited(graph, extraction.gates)
    print(f"gates:   {gates_mod.summary(extraction.gates, effective)}")

    assignment = rows_mod.assign(
        extraction,
        graph,
        rows_mod.load_config(args.rows),
        uncertainty_hints=_hints(extraction),
    )
    layout = layout_mod.build(graph, assignment)
    print(f"layout:  {layout.summary()}")

    problems = layout_mod.check_invariants(layout, graph)
    if problems:
        print(f"\nLAYOUT INVARIANTS VIOLATED ({len(problems)}):", file=sys.stderr)
        for problem in problems[:20]:
            print(f"  {problem}", file=sys.stderr)
        return 1

    localisation = loc_mod.load(load_order, language=cfg.language)
    coverage = loc_mod.coverage(localisation, sorted(extraction.technologies))
    print(
        f"loc:     {len(localisation)} entries, "
        f"{len(coverage['missing'])} technologies without one, "
        f"{len(coverage['empty'])} resolving empty, "
        f"{len(coverage['placeholder'])} placeholder"
    )
    for kind in ("missing", "empty", "placeholder"):
        if coverage[kind]:
            print(f"  {kind}: {', '.join(coverage[kind][:8])}")

    out = args.out or (cfg.output_root / "data")
    result = emit_mod.emit(extraction, graph, layout, localisation, assignment, out)
    print(f"emit:    {result.summary()}")

    report = cfg.output_root / "crisis-rows.md"
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(assignment.report(), encoding="utf-8")
    print(
        f"rows:    {len(assignment.assigned)} technologies in crisis rows, "
        f"{len(assignment.uncertain)} need review -> {report}"
    )
    return 0


def _hints(extraction) -> dict[str, list[str]]:
    hints: dict[str, list[str]] = {}
    for key, record in extraction.technologies.items():
        why: list[str] = []
        origin = record.origin.relative if record.origin else ""
        if origin in CRISIS_ADJACENT_FILES:
            why.append(f"declared in {origin} ({CRISIS_ADJACENT_FILES[origin]})")
        if key.startswith(CRISIS_ADJACENT_PREFIXES):
            why.append("negative mass / sentient metal naming")
        if why:
            hints[key] = why
    return hints


if __name__ == "__main__":
    raise SystemExit(main())
