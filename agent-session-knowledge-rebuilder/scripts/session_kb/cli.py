from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from . import VERIFIED_ADAPTERS, __version__
from .adapters import AdapterRegistry, builtin_registry
from .config import list_knowledge_bases, output_guidance, register_knowledge_base, validate_output_location
from .discovery import DiscoveryResult, discover, freeze_sources, load_snapshot, source_from_snapshot
from .inventory import inventory_environment
from .pipeline import build_knowledge_base
from .query import query_knowledge
from .release import release_check
from .review import create_review_packet, create_review_template, distill_review, validate_review


def _json_print(value: Any) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, default=str))


def _parse_source(value: str) -> tuple[str, Path]:
    if "=" not in value:
        raise argparse.ArgumentTypeError("--source must be ADAPTER=PATH")
    adapter, raw_path = value.split("=", 1)
    if not adapter or not raw_path:
        raise argparse.ArgumentTypeError("--source must be ADAPTER=PATH")
    return adapter, Path(raw_path)


def _registry(adapter_dirs: list[Path]) -> AdapterRegistry:
    registry = builtin_registry()
    for directory in adapter_dirs:
        if not directory.expanduser().is_dir():
            raise ValueError(f"adapter directory does not exist: {directory}")
        registry.load_directory(directory.expanduser())
    return registry


def _add_source_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--root", action="append", type=Path, default=[], help="Explicit root or export to probe automatically; repeatable.")
    parser.add_argument("--source", action="append", type=_parse_source, default=[], metavar="ADAPTER=PATH", help="Explicit adapter and root/file; repeatable.")
    parser.add_argument("--adapter-dir", action="append", type=Path, default=[], help="Explicit trusted custom-adapter directory; repeatable.")
    parser.add_argument("--max-files", type=int, help="Stop after this many supported files (mainly for bounded smoke tests).")


def _discover_from_args(args: argparse.Namespace, registry: AdapterRegistry, output: Path | None = None) -> DiscoveryResult:
    return discover(
        registry,
        roots=args.root or None,
        explicit_sources=args.source or None,
        output_dir=output,
        max_files=args.max_files,
    )


def _discovery_from_snapshot(value: dict[str, Any]) -> DiscoveryResult:
    discovery_value = value.get("discovery") if isinstance(value.get("discovery"), dict) else {}
    result = DiscoveryResult()
    result.roots = list(discovery_value.get("roots") or [])
    result.unsupported = list(discovery_value.get("unsupported") or [])
    result.coverage_gaps = list(discovery_value.get("coverage_gaps") or [])
    result.errors = list(discovery_value.get("errors") or [])
    result.probe_counts = dict(discovery_value.get("probe_counts") or {})
    result.sources = [source_from_snapshot(item) for item in value.get("sources") or []]
    return result


def make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="session_kb.py",
        description="Discover Agent sessions and rebuild an auditable, incremental knowledge base.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    discover_parser = subparsers.add_parser("discover", help="Probe the current environment or explicit roots without changing sources.")
    _add_source_arguments(discover_parser)
    discover_parser.add_argument("--json", action="store_true", help="Print the complete JSON discovery report.")

    inventory_parser = subparsers.add_parser("inventory", help="Inventory installed and niche Agent candidates, supported sessions, and safe unknown-format fingerprints.")
    _add_source_arguments(inventory_parser)
    inventory_parser.add_argument("--json", action="store_true", help="Print the complete privacy-safe structural report.")
    inventory_parser.add_argument("--fingerprint-limit", type=int, default=100, help="Maximum unknown candidate fingerprints; content values are never emitted.")

    guide_parser = subparsers.add_parser("guide-output", help="Show cross-platform output choices and the exact question to ask before writing a knowledge base.")
    guide_parser.add_argument("--name", required=True, help="Portable registry name for the future knowledge base.")
    guide_parser.add_argument("--cwd", type=Path, help="Project directory used for the project-local suggestion; defaults to the current directory.")

    register_parser = subparsers.add_parser("register-kb", help="Register a reviewed knowledge base for the separate reader Skill.")
    register_parser.add_argument("--name", required=True)
    register_parser.add_argument("--kb", type=Path, required=True)
    register_parser.add_argument("--config", type=Path, help="Override the platform-default local registry path.")
    register_parser.add_argument("--default", action="store_true", help="Make this the default knowledge base.")

    locations_parser = subparsers.add_parser("list-kbs", help="List locally registered reviewed knowledge bases.")
    locations_parser.add_argument("--config", type=Path, help="Override the platform-default local registry path.")

    freeze_parser = subparsers.add_parser("freeze", help="Freeze the exact source-file boundary into a snapshot manifest.")
    _add_source_arguments(freeze_parser)
    freeze_parser.add_argument("--snapshot", type=Path, required=True, help="Snapshot JSON to create.")

    rebuild_parser = subparsers.add_parser("rebuild", help="Parse, sanitize, merge, and materialize the knowledge base.")
    _add_source_arguments(rebuild_parser)
    rebuild_parser.add_argument("--snapshot", type=Path, help="Use an existing frozen snapshot instead of discovery.")
    rebuild_parser.add_argument("--output", type=Path, required=True, help="Generated knowledge-base directory.")
    rebuild_parser.add_argument("--incremental", action="store_true", help="Reuse prior state and read verified append-only tails.")
    rebuild_parser.add_argument("--dry-run", action="store_true", help="Parse and report planned results without writing artifacts.")

    query_parser = subparsers.add_parser("query", help="Select the smallest relevant knowledge set for a task.")
    query_parser.add_argument("--kb", type=Path, required=True, help="Knowledge-base root or its knowledge/ directory.")
    query_parser.add_argument("--task", required=True, help="Current task or question.")
    query_parser.add_argument("--max-projects", type=int, default=3, help="Maximum project dossiers to select.")
    query_parser.add_argument("--emit-content", action="store_true", help="Include selected file contents in JSON output.")
    query_parser.add_argument("--allow-draft", action="store_true", help="Allow querying unreviewed evidence drafts; never use this as confirmed knowledge.")
    query_parser.add_argument("--min-score", type=int, default=4, help="Minimum project relevance score; default 4.")
    query_parser.add_argument("--max-related", type=int, default=2, help="Maximum one-hop evidence-backed related documents to add.")

    review_parser = subparsers.add_parser("review-init", help="Create a project-hashed semantic-review template for a rebuilt evidence layer.")
    review_parser.add_argument("--kb", type=Path, required=True, help="Knowledge-base root containing audit/events.jsonl.")
    review_parser.add_argument("--review", type=Path, help="Review JSON to create; defaults to KB/review/review.json.")

    packet_parser = subparsers.add_parser("review-packet", help="Read one complete sanitized project chain without loading the entire corpus.")
    packet_parser.add_argument("--kb", type=Path, required=True)
    packet_parser.add_argument("--project-key", required=True)

    validate_parser = subparsers.add_parser("validate-review", help="Validate review coverage and claim provenance without publishing.")
    validate_parser.add_argument("--kb", type=Path, required=True)
    validate_parser.add_argument("--review", type=Path, required=True)

    distill_parser = subparsers.add_parser("distill", help="Publish reviewed knowledge only after all semantic gates pass.")
    distill_parser.add_argument("--kb", type=Path, required=True)
    distill_parser.add_argument("--review", type=Path, required=True)

    release_parser = subparsers.add_parser("release-check", help="Fail closed if an open-source Skill tree contains private, secret, binary, session, or generated artifacts.")
    release_parser.add_argument("--root", type=Path, required=True, help="Skill-pack source root to scan.")
    release_parser.add_argument("--deny-term", action="append", default=[], help="Private term that must not occur; repeatable and never echoed.")
    return parser


def run(args: argparse.Namespace) -> int:
    if args.command == "guide-output":
        _json_print(output_guidance(args.name, args.cwd))
        return 0
    if args.command == "register-kb":
        _json_print(register_knowledge_base(args.name, args.kb, args.config, args.default))
        return 0
    if args.command == "list-kbs":
        _json_print(list_knowledge_bases(args.config))
        return 0
    if args.command == "query":
        if args.max_projects < 0:
            raise ValueError("--max-projects must be non-negative")
        if args.min_score < 0 or args.max_related < 0:
            raise ValueError("--min-score and --max-related must be non-negative")
        _json_print(
            query_knowledge(
                args.kb,
                args.task,
                max_projects=args.max_projects,
                emit_content=args.emit_content,
                allow_draft=args.allow_draft,
                min_score=args.min_score,
                max_related=args.max_related,
            )
        )
        return 0
    if args.command == "review-init":
        _json_print(create_review_template(args.kb, args.review))
        return 0
    if args.command == "review-packet":
        _json_print(create_review_packet(args.kb, args.project_key))
        return 0
    if args.command == "validate-review":
        _, events, errors = validate_review(args.kb, args.review)
        _json_print({"status": "passed" if not errors else "failed", "events": len(events), "errors": errors})
        return 0 if not errors else 2
    if args.command == "distill":
        _json_print(distill_review(args.kb, args.review))
        return 0
    if args.command == "release-check":
        result = release_check(args.root, args.deny_term)
        _json_print(result)
        return 0 if result["status"] == "passed" else 3

    if args.max_files is not None and args.max_files <= 0:
        raise ValueError("--max-files must be positive")
    registry = _registry(args.adapter_dir)
    if args.command == "inventory":
        if args.fingerprint_limit < 0:
            raise ValueError("--fingerprint-limit must be non-negative")
        report, _ = inventory_environment(
            registry,
            roots=args.root or None,
            max_files=args.max_files,
            fingerprint_limit=args.fingerprint_limit,
        )
        if args.json:
            _json_print(report)
        else:
            print(f"platform: {report['platform']['system']} {report['platform']['machine']}")
            print(f"installed Agent commands: {len(report['installed_agent_commands'])}")
            print(f"supported session files: {report['supported_files']}")
            for adapter, count in report["adapter_counts"].items():
                print(f"  {adapter}: {count}")
            print(f"unknown or ambiguous candidates: {report['unknown_or_ambiguous_candidates']}")
            print(f"coverage gaps: {len(report['coverage_gaps'])}")
            print(f"discovery errors: {len(report['discovery_errors'])}")
        return 0

    if args.command == "discover":
        result = _discover_from_args(args, registry)
        value = result.to_dict()
        if args.json:
            _json_print(value)
        else:
            print(f"supported files: {value['supported_files']}")
            for adapter, count in value["adapter_counts"].items():
                print(f"  {adapter}: {count}")
            print(f"unsupported candidates: {len(value['unsupported'])}")
            print(f"discovery coverage gaps: {len(value['coverage_gaps'])}")
            print(f"discovery errors: {len(value['errors'])}")
        return 0

    if args.command == "freeze":
        result = _discover_from_args(args, registry)
        snapshot = freeze_sources(result.sources)
        snapshot["discovery"] = result.to_dict()
        destination = args.snapshot.expanduser()
        if destination.exists():
            raise ValueError(f"snapshot already exists: {destination}")
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        _json_print({"snapshot": str(destination.resolve()), "source_count": snapshot["source_count"], "errors": snapshot["errors"]})
        return 0

    if args.command == "rebuild":
        output = args.output.expanduser()
        if args.snapshot:
            snapshot, sources = load_snapshot(args.snapshot.expanduser())
            discovery_result = _discovery_from_snapshot(snapshot)
        else:
            discovery_result = _discover_from_args(args, registry, output=output)
            sources = discovery_result.sources
            snapshot = freeze_sources(sources)
            snapshot["discovery"] = discovery_result.to_dict()
        if not sources:
            raise ValueError("no supported session files discovered; inspect discover output or specify --root/--source")
        output = validate_output_location(output, sources)
        result = build_knowledge_base(
            registry,
            sources,
            output_dir=output,
            discovery=discovery_result,
            snapshot=snapshot,
            incremental=args.incremental,
            dry_run=args.dry_run,
            verified_adapters=VERIFIED_ADAPTERS,
        )
        _json_print(result)
        return 0
    raise ValueError(f"unknown command: {args.command}")


def main(argv: list[str] | None = None) -> int:
    parser = make_parser()
    args = parser.parse_args(argv)
    try:
        return run(args)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
