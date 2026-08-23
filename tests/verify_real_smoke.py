from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "agent-session-knowledge-rebuilder" / "scripts"))

from session_kb.query import query_knowledge  # noqa: E402
from session_kb.sanitize import Sanitizer  # noqa: E402

REQUIRED_EVENT_FIELDS = {
    "schema_version",
    "event_id",
    "source_agent",
    "adapter",
    "source_file_id",
    "source_relpath",
    "session_id",
    "logical_session_id",
    "record_locator",
    "sequence",
    "role",
    "actor_kind",
    "event_type",
    "evidence_grade",
    "content",
    "content_sha256",
    "flags",
}
CASES = {
    "validated-codex": {"adapters": {"codex-jsonl"}},
    "validated-clacky": {"adapters": {"clacky-json", "clacky-chunk"}},
    "validated-claude": {"adapters": {"claude-code-jsonl"}},
    "validated-workbuddy": {"adapters": {"workbuddy-jsonl"}},
    "validated-neo": {"adapters": {"neo-claude-jsonl"}},
    "validated-cursor": {"adapters": {"cursor-agent-jsonl"}},
}
E2E_CASES = {
    "e2e-codex": {"adapters": {"codex-jsonl"}},
    "e2e-clacky": {"adapters": {"clacky-json", "clacky-chunk"}},
}
def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _display(path: Path, output_root: Path) -> str:
    try:
        return str(path.relative_to(output_root))
    except ValueError:
        return str(path)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Verify isolated real-sample smoke outputs without reading source sessions again.")
    parser.add_argument(
        "--output-root",
        type=Path,
        required=True,
        help="Isolated directory outside this source tree containing one validated-* directory per real-sample case.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    output_root = args.output_root.expanduser().resolve()
    try:
        output_root.relative_to(PROJECT_ROOT.resolve())
    except ValueError:
        pass
    else:
        raise SystemExit("error: --output-root must be outside the public project source tree")
    report_path = output_root / "smoke-report.json"
    failures: list[dict] = []
    case_reports: dict[str, dict] = {}
    for name, expectation in CASES.items():
        root = output_root / name
        required = [
            root / "audit" / "stats.json",
            root / "audit" / "events.jsonl",
            root / "audit" / "excluded.jsonl",
            root / "audit" / "errors.jsonl",
            root / "audit" / "dispositions.jsonl",
            root / "audit" / "completion-report.json",
            root / "audit" / "impact-report.json",
            root / "audit" / "unsupported-formats.json",
            root / "audit" / "coverage-gaps.json",
            root / "audit" / "compatibility-matrix.md",
            root / "knowledge" / "00-evidence-rules.md",
            root / "knowledge" / "01-identity-and-current-direction.md",
            root / "knowledge" / "02-collaboration-and-expression.md",
            root / "knowledge" / "knowledge-index.json",
            root / "review" / "review.json",
        ]
        missing = [_display(path, output_root) for path in required if not path.is_file()]
        if missing:
            failures.append({"case": name, "check": "required_files", "missing": missing})
            continue
        stats = json.loads((root / "audit" / "stats.json").read_text(encoding="utf-8"))
        completion = json.loads((root / "audit" / "completion-report.json").read_text(encoding="utf-8"))
        index = json.loads((root / "knowledge" / "knowledge-index.json").read_text(encoding="utf-8"))
        events = read_jsonl(root / "audit" / "events.jsonl")
        adapters = set(stats.get("adapter_counts") or {})
        if adapters != expectation["adapters"]:
            failures.append({"case": name, "check": "adapter_counts", "expected": sorted(expectation["adapters"]), "actual": sorted(adapters)})
        if stats.get("parse_errors") != 0:
            failures.append({"case": name, "check": "parse_errors", "actual": stats.get("parse_errors")})
        for gate in ("frozen_snapshot", "transport_accounted", "parse_clean", "discovery_coverage_complete"):
            if completion.get("gates", {}).get(gate) is not True:
                failures.append({"case": name, "check": "deterministic_gate", "gate": gate, "actual": completion.get("gates", {}).get(gate)})
        if completion.get("status") != "needs_semantic_review":
            failures.append({"case": name, "check": "review_gate", "expected": "needs_semantic_review", "actual": completion.get("status")})
        if index.get("semantic_status") != "draft":
            failures.append({"case": name, "check": "draft_contract", "actual": index.get("semantic_status")})
        if not events:
            failures.append({"case": name, "check": "retained_events", "actual": 0})
        for index, event in enumerate(events):
            missing_fields = sorted(REQUIRED_EVENT_FIELDS - set(event))
            if missing_fields:
                failures.append({"case": name, "check": "event_contract", "event_index": index, "missing_fields": missing_fields})
                break
        case_reports[name] = {
            "adapter_counts": stats.get("adapter_counts"),
            "source_files": stats.get("source_files"),
            "records_seen": stats.get("records_seen"),
            "retained_events": stats.get("retained_events"),
            "excluded_records": stats.get("excluded_records"),
            "duplicates_removed": stats.get("duplicates_removed"),
            "parse_errors": stats.get("parse_errors"),
            "transport_records_unaccounted": stats.get("transport_records_unaccounted"),
            "completion_status": completion.get("status"),
        }

    for name, expectation in E2E_CASES.items():
        root = output_root / name
        required = [
            root / "audit" / "events.jsonl",
            root / "audit" / "semantic-dispositions.jsonl",
            root / "audit" / "completion-report.json",
            root / "audit" / "relationships.json",
            root / "audit" / "relationship-candidates.json",
            root / "audit" / "link-audit.json",
            root / "audit" / "actor-attributions.json",
            root / "audit" / "feedback-signals.json",
            root / "audit" / "rule-evolutions.json",
            root / "audit" / "retrieval-verification.json",
            root / "knowledge" / "knowledge-index.json",
            root / "knowledge" / "knowledge-graph.json",
            root / "review" / "review.json",
        ]
        missing = [_display(path, output_root) for path in required if not path.is_file()]
        if missing:
            failures.append({"case": name, "check": "required_published_files", "missing": missing})
            continue
        completion = json.loads((root / "audit" / "completion-report.json").read_text(encoding="utf-8"))
        index = json.loads((root / "knowledge" / "knowledge-index.json").read_text(encoding="utf-8"))
        events = read_jsonl(root / "audit" / "events.jsonl")
        semantic = read_jsonl(root / "audit" / "semantic-dispositions.jsonl")
        stats = json.loads((root / "audit" / "stats.json").read_text(encoding="utf-8"))
        if set(stats.get("adapter_counts") or {}) != expectation["adapters"]:
            failures.append({"case": name, "check": "adapter_counts", "expected": sorted(expectation["adapters"]), "actual": sorted(stats.get("adapter_counts") or {})})
        if completion.get("status") != "complete" or index.get("semantic_status") != "published":
            failures.append({"case": name, "check": "publication_gate", "completion": completion.get("status"), "index": index.get("semantic_status")})
        if (
            completion.get("retrieval_contract_version") != 2
            or not completion.get("retrieval_suite_sha256")
            or not completion.get("retrieval_verification_sha256")
        ):
            failures.append({"case": name, "check": "retrieval_suite_contract"})
        for gate in (
            "semantic_review_complete",
            "knowledge_graph_complete",
            "published_knowledge",
            "retrieval_related_match",
            "retrieval_unrelated_no_match",
            "retrieval_related_suite",
            "retrieval_hard_negative_suite",
        ):
            if completion.get("gates", {}).get(gate) is not True:
                failures.append({"case": name, "check": "semantic_gate", "gate": gate})
        if len(events) != len(semantic):
            failures.append({"case": name, "check": "semantic_coverage", "events": len(events), "semantic_dispositions": len(semantic)})
        query = query_knowledge(root, "unrelated-portable-smoke-task", max_projects=1, max_related=1)
        if query.get("match_status") != "no_match":
            failures.append({"case": name, "check": "unrelated_query", "actual": query.get("match_status")})
        case_reports[name] = {
            "adapter_counts": stats.get("adapter_counts"),
            "retained_events": len(events),
            "semantic_dispositions": len(semantic),
            "completion_status": completion.get("status"),
            "query_match_status": query.get("match_status"),
        }

    scanned_files = 0
    for path in output_root.rglob("*"):
        if not path.is_file() or path == report_path:
            continue
        scanned_files += 1
        data = path.read_bytes()
        if b"\x00" in data:
            failures.append({"case": "all", "check": "binary_scan", "path": _display(path, output_root)})
            continue
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            failures.append({"case": "all", "check": "binary_scan", "path": _display(path, output_root)})
            continue
        sanitizer = Sanitizer()
        if sanitizer.sanitize_text(text) != text:
            failures.append(
                {
                    "case": "all",
                    "check": "sensitive_scan",
                    "kind": "sanitizer_would_change_artifact",
                    "path": _display(path, output_root),
                }
            )

    report = {
        "report_version": 3,
        "status": "passed" if not failures else "failed",
        "cases": case_reports,
        "artifact_files_scanned": scanned_files,
        "sensitive_and_binary_scan": (
            "passed" if not any(item["check"] in {"sensitive_scan", "binary_scan"} for item in failures) else "failed"
        ),
        "failures": failures,
    }
    output_root.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
