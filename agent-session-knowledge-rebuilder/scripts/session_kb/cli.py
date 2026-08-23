from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

from . import VERIFIED_ADAPTERS, __version__
from .adapters import AdapterRegistry, builtin_registry
from .config import list_knowledge_bases, output_guidance, register_knowledge_base, validate_output_location
from .discovery import DiscoveryResult, discover, freeze_sources, load_snapshot, source_from_snapshot
from .evolution_ops import (
    approval_queue,
    atomic_write_review,
    decide_rule_evolution,
    feedback_clusters,
    propose_rule_evolution,
    record_behavior_evaluation,
)
from .golden import run_golden_manifest
from .inventory import inventory_environment
from .locking import mutation_lock
from .lifecycle import apply_lifecycle_plan, list_due_revalidations, plan_lifecycle
from .pipeline import build_knowledge_base
from .query import query_knowledge
from .release import release_check
from .review import cross_project_state_sha256, create_review_packet, create_review_template, distill_review, validate_review
from .verification import verify_retrieval, verify_retrieval_suite


def _json_print(value: Any) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, default=str))


def _write_json_exclusive(path: Path, value: Any) -> None:
    """Create a JSON file once without an exists/write race."""

    target = path.expanduser()
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError as exc:
        raise ValueError(f"snapshot already exists: {target}") from exc
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
    except Exception:
        try:
            target.unlink()
        except FileNotFoundError:
            pass
        raise


def _load_review_for_evolution(kb: Path, review_path: Path) -> tuple[Path, dict[str, Any], list[Any]]:
    raw_target = review_path.expanduser()
    if raw_target.is_symlink():
        raise ValueError("review path cannot be a symlink")
    target = raw_target.resolve()
    review, events, errors = validate_review(kb, target, allow_pending_cross_project_recheck=True)
    if errors:
        raise ValueError("current review does not pass the evolution draft contract:\n- " + "\n- ".join(errors))
    return target, review, events


def _reopen_cross_project_recheck(
    original: dict[str, Any],
    updated: dict[str, Any],
    *,
    operation: str,
    evolution_id: str,
) -> dict[str, Any]:
    """Invalidate, but never manufacture, a human cross-project attestation."""

    before_state = cross_project_state_sha256(original)
    after_state = cross_project_state_sha256(updated)
    original_recheck = original.get("cross_project_recheck") if isinstance(original.get("cross_project_recheck"), dict) else {}
    required = original_recheck.get("required") is True
    if before_state == after_state or not required:
        completed = original_recheck.get("completed") is True
        status = "completed" if required and completed else "pending" if required else "not-required"
        return {
            "status": status,
            "required": required,
            "completed": completed,
            "state_changed": before_state != after_state,
            "distill_ready": not required or completed,
        }

    recheck = updated.get("cross_project_recheck") if isinstance(updated.get("cross_project_recheck"), dict) else {}
    recheck = dict(recheck)
    was_completed = original_recheck.get("completed") is True
    pending = [dict(item) for item in original_recheck.get("pending_evolution_mutations") or [] if isinstance(item, dict)]
    mutation = {"operation": operation, "evolution_id": evolution_id, "state_sha256": after_state}
    if mutation not in pending:
        pending.append(mutation)
    reopen_count = original_recheck.get("reopen_count")
    if not isinstance(reopen_count, int) or isinstance(reopen_count, bool) or reopen_count < 0:
        reopen_count = 0
    if was_completed:
        reopen_count += 1
        recheck["reopened_from_state_sha256"] = str(original_recheck.get("checked_state_sha256") or before_state)
    elif not str(recheck.get("reopened_from_state_sha256") or ""):
        recheck["reopened_from_state_sha256"] = before_state
    recheck.update(
        {
            "required": True,
            "completed": False,
            "reviewed_at": "",
            "rationale": "",
            "checked_state_sha256": "",
            "reopen_reason": "evolution-state-changed",
            "reopen_count": reopen_count,
            "pending_evolution_mutations": pending,
        }
    )
    updated["cross_project_recheck"] = recheck
    return {
        "status": "reopened" if was_completed else "pending",
        "required": True,
        "completed": False,
        "state_changed": True,
        "distill_ready": False,
        "reopen_count": reopen_count,
        "pending_mutation_count": len(pending),
    }


def _write_validated_review_update(
    kb: Path,
    review_path: Path,
    original: dict[str, Any],
    value: dict[str, Any],
    *,
    operation: str,
    evolution_id: str,
) -> dict[str, Any]:
    """Save a legal evolution draft, reopening any invalidated human recheck."""

    raw_target = review_path.expanduser()
    if raw_target.is_symlink():
        raise ValueError("review path cannot be a symlink")
    target = raw_target.resolve()
    recheck_status = _reopen_cross_project_recheck(
        original,
        value,
        operation=operation,
        evolution_id=evolution_id,
    )
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{target.name}.validate.", suffix=".json", dir=target.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        _, _, errors = validate_review(kb, temporary, allow_pending_cross_project_recheck=True)
        if errors:
            raise ValueError("evolution update does not pass the evolution draft contract:\n- " + "\n- ".join(errors))
        atomic_write_review(raw_target, value)
    finally:
        if temporary.exists():
            temporary.unlink()
    return recheck_status


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
    review_parser.add_argument("--from-review", type=Path, help="Carry forward only hash-identical reviewed projects and still-valid evidence records from a prior review.")
    review_parser.add_argument("--membership-plan", type=Path, help="Evidence-bound project merge/split/reassignment JSON produced after reviewing proposed chains.")

    packet_parser = subparsers.add_parser("review-packet", help="Read one complete sanitized project chain or a hash-bound contiguous range.")
    packet_parser.add_argument("--kb", type=Path, required=True)
    packet_parser.add_argument("--project-key", required=True)
    packet_parser.add_argument("--start-event", type=int, default=0, help="Zero-based event index for a contiguous checkpoint range.")
    packet_parser.add_argument("--max-events", type=int, help="Maximum events to return; omit for the complete project chain.")
    packet_parser.add_argument("--review", type=Path, help="Use the effective project membership stored in this review file.")

    validate_parser = subparsers.add_parser("validate-review", help="Validate review coverage and claim provenance without publishing.")
    validate_parser.add_argument("--kb", type=Path, required=True)
    validate_parser.add_argument("--review", type=Path, required=True)

    distill_parser = subparsers.add_parser("distill", help="Publish reviewed knowledge only after all semantic gates pass.")
    distill_parser.add_argument("--kb", type=Path, required=True)
    distill_parser.add_argument("--review", type=Path, required=True)

    verify_parser = subparsers.add_parser("verify-retrieval", help="Verify a multi-case retrieval eval set, or a legacy relevant/unrelated pair.")
    verify_parser.add_argument("--kb", type=Path, required=True)
    verify_parser.add_argument("--eval-set", type=Path, help="Private JSON eval set with multiple related paraphrases and hard negatives.")
    verify_parser.add_argument("--related-task", help="Legacy single task that must retrieve reviewed knowledge.")
    verify_parser.add_argument("--unrelated-task", help="Legacy single task that must return no_match.")
    verify_parser.add_argument("--expected-project-key", help="Optional project key that the related task must select.")

    clusters_parser = subparsers.add_parser("evolution-clusters", help="List exact evidence-bound feedback clusters without semantic invention.")
    clusters_parser.add_argument("--review", type=Path, required=True)

    propose_parser = subparsers.add_parser("evolution-propose", help="Add a non-active rule candidate from an Agent-reviewed proposal JSON.")
    propose_parser.add_argument("--kb", type=Path, required=True)
    propose_parser.add_argument("--review", type=Path, required=True)
    propose_parser.add_argument("--proposal", type=Path, required=True)

    queue_parser = subparsers.add_parser("evolution-queue", help="Show rule candidates awaiting a user decision.")
    queue_parser.add_argument("--review", type=Path, required=True)

    decide_parser = subparsers.add_parser("evolution-decide", help="Record an explicit approve/reject decision for one rule candidate.")
    decide_parser.add_argument("--kb", type=Path, required=True)
    decide_parser.add_argument("--review", type=Path, required=True)
    decide_parser.add_argument("--evolution-id", required=True)
    decide_parser.add_argument("--decision", choices=("approve", "reject"), required=True)
    decide_parser.add_argument("--approval-event-id", action="append", default=[])
    decide_parser.add_argument("--promoted-claim-id")
    decide_parser.add_argument("--explicit-global-approval", action="store_true")

    evaluate_parser = subparsers.add_parser("evolution-evaluate", help="Record a before/after behavior evaluation for an approved rule.")
    evaluate_parser.add_argument("--kb", type=Path, required=True)
    evaluate_parser.add_argument("--review", type=Path, required=True)
    evaluate_parser.add_argument("--evolution-id", required=True)
    evaluate_parser.add_argument("--result", choices=("passed", "failed", "mixed"), required=True)
    evaluate_parser.add_argument("--baseline-event-id", action="append", default=[], required=True)
    evaluate_parser.add_argument("--validation-event-id", action="append", default=[], required=True)
    evaluate_parser.add_argument("--observed-change", default="")

    golden_parser = subparsers.add_parser("golden-check", help="Run an isolated synthetic or user-held golden fixture manifest.")
    golden_parser.add_argument("--fixture-root", type=Path, required=True)
    golden_parser.add_argument("--manifest", type=Path, required=True)
    golden_parser.add_argument("--output", type=Path, help="Optional isolated output directory; omit to use a temporary directory.")
    golden_parser.add_argument("--adapter-dir", action="append", type=Path, default=[], help="Explicit trusted custom-adapter directory; repeatable.")

    lifecycle_plan_parser = subparsers.add_parser("lifecycle-plan", help="Dry-run a retract, forget, or revalidation schedule against generated knowledge.")
    lifecycle_plan_parser.add_argument("--kb", type=Path, required=True)
    lifecycle_plan_parser.add_argument("--action", choices=("retract", "forget", "schedule"), required=True)
    lifecycle_plan_parser.add_argument("--source-file-id", action="append", default=[])
    lifecycle_plan_parser.add_argument("--event-id", action="append", default=[])
    lifecycle_plan_parser.add_argument("--claim-id", action="append", default=[])
    lifecycle_plan_parser.add_argument("--project-key", action="append", default=[])
    lifecycle_plan_parser.add_argument("--reason", default="", help="Optional private reason; only its hash enters the plan.")
    lifecycle_plan_parser.add_argument("--observed-at")
    lifecycle_plan_parser.add_argument("--checked-at")
    lifecycle_plan_parser.add_argument("--recheck-after")
    lifecycle_plan_parser.add_argument("--retain-until")
    lifecycle_plan_parser.add_argument("--plan", type=Path, help="Optional private plan file to create exclusively; otherwise print only.")

    lifecycle_apply_parser = subparsers.add_parser("lifecycle-apply", help="Apply an unchanged state-bound lifecycle plan only with --commit.")
    lifecycle_apply_parser.add_argument("--kb", type=Path, required=True)
    lifecycle_apply_parser.add_argument("--plan", type=Path, required=True)
    lifecycle_apply_parser.add_argument("--commit", action="store_true", help="Required explicit mutation switch; omitted means dry-run output only.")

    lifecycle_due_parser = subparsers.add_parser("lifecycle-due", help="List due rechecks and retention expiries without claim bodies.")
    lifecycle_due_parser.add_argument("--kb", type=Path, required=True)
    lifecycle_due_parser.add_argument("--as-of", help="Optional ISO-8601 date/time; defaults to now.")

    release_parser = subparsers.add_parser("release-check", help="Fail closed if a public Skill source tree contains private, secret, binary, session, or generated artifacts.")
    release_parser.add_argument("--root", type=Path, required=True, help="Skill-pack source root to scan.")
    release_parser.add_argument("--deny-term", action="append", default=[], help="Private term that must not occur; repeatable and never echoed.")
    release_parser.add_argument(
        "--deny-term-file",
        action="append",
        type=Path,
        default=[],
        help="Private newline-delimited deny terms from a file outside the release root; repeatable and safer than command-line terms.",
    )
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
    if args.command == "lifecycle-plan":
        plan = plan_lifecycle(
            args.kb,
            args.action,
            source_file_ids=args.source_file_id,
            event_ids=args.event_id,
            claim_ids=args.claim_id,
            project_keys=args.project_key,
            reason=args.reason,
            observed_at=args.observed_at,
            checked_at=args.checked_at,
            recheck_after=args.recheck_after,
            retain_until=args.retain_until,
        )
        if args.plan:
            _write_json_exclusive(args.plan, plan)
        _json_print({**plan, **({"plan_file": str(args.plan.expanduser().resolve())} if args.plan else {})})
        return 0
    if args.command == "lifecycle-apply":
        plan = json.loads(args.plan.expanduser().read_text(encoding="utf-8"))
        result = apply_lifecycle_plan(args.kb, plan, commit=args.commit)
        _json_print(result)
        return 0
    if args.command == "lifecycle-due":
        _json_print(list_due_revalidations(args.kb, as_of=args.as_of))
        return 0
    if args.command == "evolution-clusters":
        review = json.loads(args.review.expanduser().read_text(encoding="utf-8"))
        clusters = feedback_clusters(review)
        _json_print({"cluster_count": len(clusters), "clusters": clusters})
        return 0
    if args.command == "evolution-queue":
        review = json.loads(args.review.expanduser().read_text(encoding="utf-8"))
        _json_print(approval_queue(review))
        return 0
    if args.command == "evolution-propose":
        review_path = args.review.expanduser()
        proposal = json.loads(args.proposal.expanduser().read_text(encoding="utf-8"))
        with mutation_lock(args.kb, "evolution-propose"):
            review_path, review, _ = _load_review_for_evolution(args.kb, review_path)
            result = propose_rule_evolution(review, proposal)
            recheck = _write_validated_review_update(
                args.kb,
                review_path,
                review,
                result["review"],
                operation="evolution-propose",
                evolution_id=str(result["evolution"]["evolution_id"]),
            )
        _json_print({"status": "candidate-recorded", "evolution": result["evolution"], "cross_project_recheck": recheck})
        return 0
    if args.command == "evolution-decide":
        review_path = args.review.expanduser()
        with mutation_lock(args.kb, "evolution-decide"):
            review_path, review, _ = _load_review_for_evolution(args.kb, review_path)
            updated = decide_rule_evolution(
                review,
                args.evolution_id,
                args.decision,
                approval_event_ids=args.approval_event_id,
                promoted_claim_id=args.promoted_claim_id,
                explicit_global_approval=args.explicit_global_approval,
            )
            recheck = _write_validated_review_update(
                args.kb,
                review_path,
                review,
                updated,
                operation="evolution-decide",
                evolution_id=args.evolution_id,
            )
        _json_print(
            {
                "status": f"evolution-{args.decision}-recorded",
                "evolution_id": args.evolution_id,
                "cross_project_recheck": recheck,
            }
        )
        return 0
    if args.command == "evolution-evaluate":
        review_path = args.review.expanduser()
        with mutation_lock(args.kb, "evolution-evaluate"):
            review_path, review, events = _load_review_for_evolution(args.kb, review_path)
            updated = record_behavior_evaluation(
                review,
                args.evolution_id,
                validation_result=args.result,
                baseline_event_ids=args.baseline_event_id,
                validation_event_ids=args.validation_event_id,
                events=events,
                observed_behavior_change=args.observed_change,
            )
            recheck = _write_validated_review_update(
                args.kb,
                review_path,
                review,
                updated,
                operation="evolution-evaluate",
                evolution_id=args.evolution_id,
            )
        _json_print(
            {
                "status": "behavior-evaluation-recorded",
                "evolution_id": args.evolution_id,
                "result": args.result,
                "cross_project_recheck": recheck,
            }
        )
        return 0
    if args.command == "golden-check":
        result = run_golden_manifest(_registry(args.adapter_dir), args.fixture_root, args.manifest, output_root=args.output)
        _json_print(result)
        return 0 if result["status"] == "passed" else 2
    if args.command == "review-init":
        membership = None
        if args.membership_plan:
            membership_value = json.loads(args.membership_plan.expanduser().read_text(encoding="utf-8"))
            membership = membership_value.get("project_membership", membership_value) if isinstance(membership_value, dict) else membership_value
            if not isinstance(membership, dict):
                raise ValueError("--membership-plan must contain a JSON object")
        with mutation_lock(args.kb, "review-init"):
            result = create_review_template(args.kb, args.review, args.from_review, membership)
        _json_print(result)
        return 0
    if args.command == "review-packet":
        _json_print(create_review_packet(args.kb, args.project_key, args.start_event, args.max_events, review_path=args.review))
        return 0
    if args.command == "validate-review":
        _, events, errors = validate_review(args.kb, args.review)
        _json_print({"status": "passed" if not errors else "failed", "events": len(events), "errors": errors})
        return 0 if not errors else 2
    if args.command == "distill":
        with mutation_lock(args.kb, "distill"):
            result = distill_review(args.kb, args.review)
        _json_print(result)
        return 0
    if args.command == "verify-retrieval":
        with mutation_lock(args.kb, "verify-retrieval"):
            if args.eval_set:
                if args.related_task or args.unrelated_task or args.expected_project_key:
                    raise ValueError("--eval-set cannot be combined with legacy related/unrelated arguments")
                eval_set = json.loads(args.eval_set.expanduser().read_text(encoding="utf-8"))
                result = verify_retrieval_suite(args.kb, eval_set)
            else:
                if not args.related_task or not args.unrelated_task:
                    raise ValueError("use --eval-set, or provide both --related-task and --unrelated-task")
                result = verify_retrieval(args.kb, args.related_task, args.unrelated_task, args.expected_project_key)
        _json_print(result)
        return 0 if result["status"] == "passed" else 2
    if args.command == "release-check":
        release_root = args.root.expanduser().resolve()
        deny_terms = list(args.deny_term)
        for term_file in args.deny_term_file:
            resolved = term_file.expanduser().resolve()
            try:
                resolved.relative_to(release_root)
            except ValueError:
                pass
            else:
                raise ValueError("--deny-term-file must stay outside the public release root")
            deny_terms.extend(line.strip() for line in resolved.read_text(encoding="utf-8").splitlines() if line.strip())
        result = release_check(args.root, deny_terms)
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
        if not result.sources:
            raise ValueError("no supported session files discovered; inspect discover output or specify --root/--source")
        snapshot = freeze_sources(result.sources)
        snapshot["discovery"] = result.to_dict()
        if snapshot.get("errors") or snapshot.get("complete") is not True:
            raise ValueError("freeze failed for one or more discovered sources; no snapshot was written")
        destination = args.snapshot.expanduser()
        _write_json_exclusive(destination, snapshot)
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
        if args.dry_run:
            result = build_knowledge_base(
                registry,
                sources,
                output_dir=output,
                discovery=discovery_result,
                snapshot=snapshot,
                incremental=args.incremental,
                dry_run=True,
                verified_adapters=VERIFIED_ADAPTERS,
            )
        else:
            with mutation_lock(output, "incremental-rebuild" if args.incremental else "rebuild"):
                result = build_knowledge_base(
                    registry,
                    sources,
                    output_dir=output,
                    discovery=discovery_result,
                    snapshot=snapshot,
                    incremental=args.incremental,
                    dry_run=False,
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
