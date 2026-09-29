#!/usr/bin/env python3
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import sys
import unicodedata
from hashlib import sha256
from pathlib import Path
from typing import Any

_view_spec = importlib.util.spec_from_file_location("reader_context_units", Path(__file__).with_name("context_units.py"))
_view_module = importlib.util.module_from_spec(_view_spec)
_view_spec.loader.exec_module(_view_module)
selection_options = _view_module.selection_options
unit_view = _view_module.unit_view
validate_units = _view_module.validate_units
RETRIEVAL_ENGINE_VERSION = _view_module.RETRIEVAL_ENGINE_VERSION


# Generic writing/style/voice/identity words are not personal-context intent.
IDENTITY_TASK_RE = re.compile(
    r"我的(?:身份(?!认证|验证|识别)|经历|简历|职业|个人定位|当前方向)|我是谁|"
    r"\bwho am i\b|\bmy (?:current )?(?:personal (?:identity|background)|career|resume|direction)\b|"
    r"\bmy (?:current )?(?:identity|background)\s*[?.!。！？]?\s*$",
    re.IGNORECASE,
)
COLLAB_TASK_RE = re.compile(
    r"我的(?:偏好|文风|口吻|写作风格|表达习惯)|按我(?:的|以往的|平时的)(?:风格|口吻|文风)|"
    r"我们(?:之前|以前|过去|所有|历次)?的?(?:协作|合作)|怎么(?:和我|跟我)配合|"
    r"\bmy (?:(?:writing|communication|personal) (?:style|voice)|preferences)\b|"
    r"\b(?:write|speak) in my (?:style|voice)\b|"
    r"\bour (?:past |previous )?collaboration\b",
    re.IGNORECASE,
)
GENERIC_QUERY_TOKENS = frozenset(
    "the a an and or to of for in on my our me we please continue help write writing "
    "style voice identity project task agent ai update test tests fix code".split()
) | frozenset({
    "我的", "我们", "帮我", "继续", "一下", "这个", "那个", "之前", "现在",
    "项目", "任务", "测试", "更新", "优化", "写作", "风格", "语音", "助手",
})


def base_context_types(task: str, selection: str) -> set[str]:
    choices = {
        "none": set(), "identity": {"identity"},
        "collaboration": {"collaboration"}, "both": {"identity", "collaboration"},
    }
    if selection in choices:
        return choices[selection]
    if selection != "auto":
        raise ValueError("base_context must be auto, none, identity, collaboration or both")
    return ({"identity"} if IDENTITY_TASK_RE.search(task) else set()) | (
        {"collaboration"} if COLLAB_TASK_RE.search(task) else set())


def skipped_query(task: str) -> dict[str, Any]:
    # No registry lookup, filesystem access, or publication claim on this path.
    return {
        "task": task, "match_status": "skipped",
        "skip_reason": "current_context_sufficient",
        "selected_count": 0, "project_matches": 0, "related_documents": 0,
        "documents": [],
    }


PUBLICATION_PREREQUISITE_GATES = (
    "frozen_snapshot",
    "transport_accounted",
    "parse_clean",
    "discovery_coverage_complete",
    "semantic_review_complete",
    "knowledge_graph_complete",
    "published_knowledge",
)
MIN_RELATED_CASES = 2
MIN_HARD_NEGATIVE_CASES = 2
DEFAULT_RETRIEVAL_PROFILE = {"min_score": 4, "max_projects": 3, "max_related": 2}


def platform_family() -> str:
    if os.name == "nt" or sys.platform.startswith("win"):
        return "windows"
    if sys.platform == "darwin":
        return "macos"
    return "linux"


def default_registry_path() -> Path:
    override = os.environ.get("AGENT_KB_REGISTRY")
    if override:
        return Path(override).expanduser()
    home = Path.home()
    if platform_family() == "windows":
        base = Path(os.environ.get("APPDATA") or os.environ.get("LOCALAPPDATA") or home / "AppData" / "Roaming")
    elif platform_family() == "macos":
        base = home / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", home / ".config"))
    return base / "agent-session-knowledge-base" / "locations.json"


def load_registry(path: Path | None = None) -> dict[str, Any]:
    target = (path or default_registry_path()).expanduser()
    if not target.is_file():
        return {"registry_version": 1, "default": None, "knowledge_bases": {}}
    value = json.loads(target.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or value.get("registry_version") != 1 or not isinstance(value.get("knowledge_bases"), dict):
        raise ValueError(f"invalid knowledge-base registry: {target}")
    return value


def resolve_registered(name: str | None, registry_path: Path | None = None) -> Path:
    registry = load_registry(registry_path)
    selected = name or registry.get("default")
    if not selected:
        raise ValueError("no registered default knowledge base; specify --name or --kb")
    entry = registry["knowledge_bases"].get(selected)
    if not isinstance(entry, dict) or not entry.get("path"):
        raise ValueError(f"knowledge base is not registered: {selected}")
    return Path(str(entry["path"])).expanduser()


def knowledge_root(kb: Path) -> Path:
    expanded = kb.expanduser().resolve()
    if (expanded / "knowledge-index.json").is_file():
        return expanded
    if (expanded / "knowledge" / "knowledge-index.json").is_file():
        return expanded / "knowledge"
    raise ValueError(f"knowledge-index.json not found under {expanded}")


def _safe_published_path(root: Path, value: Any, label: str) -> tuple[Path, str]:
    relative = Path(str(value or ""))
    if not relative.parts or relative.is_absolute() or ".." in relative.parts:
        raise ValueError(f"unsafe {label} path: {relative}")
    if relative.parts[0] == "archive":
        raise ValueError(f"{label} path cannot read archived knowledge: {relative}")
    path = (root / relative).resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"{label} path escapes knowledge root: {relative}") from exc
    if not path.is_file():
        raise ValueError(f"{label} file is missing: {relative}")
    return path, relative.as_posix()


def load_validated_graph(root: Path, index: dict[str, Any]) -> dict[str, Any]:
    root = root.expanduser().resolve()
    graph_meta = index.get("graph") if isinstance(index.get("graph"), dict) else {}
    graph_path, _ = _safe_published_path(root, graph_meta.get("path") or "knowledge-graph.json", "graph")
    graph = json.loads(graph_path.read_text(encoding="utf-8"))
    if not isinstance(graph, dict):
        raise ValueError("knowledge graph must be a JSON object")
    if graph.get("semantic_status") != "published" or graph.get("run_id") != index.get("run_id"):
        raise ValueError("knowledge graph status or run_id does not match the published index")
    indexed_paths: set[str] = set()
    for item in index.get("documents") or []:
        if not isinstance(item, dict) or not item.get("path"):
            raise ValueError("knowledge index contains an invalid document entry")
        _, relative = _safe_published_path(root, item["path"], "indexed document")
        if relative in indexed_paths:
            raise ValueError(f"knowledge index contains a duplicate document path: {relative}")
        indexed_paths.add(relative)
    node_by_id: dict[str, dict[str, Any]] = {}
    graph_document_paths: set[str] = set()
    for node in graph.get("nodes") or []:
        if not isinstance(node, dict) or not node.get("id"):
            raise ValueError("knowledge graph contains an invalid node")
        node_id = str(node["id"])
        if node_id in node_by_id:
            raise ValueError(f"knowledge graph contains a duplicate node id: {node_id}")
        node_by_id[node_id] = node
        if node.get("kind") == "document":
            _, relative = _safe_published_path(root, node.get("path"), "graph document")
            if relative not in indexed_paths:
                raise ValueError(f"knowledge graph document is not in the published index: {relative}")
            if relative in graph_document_paths:
                raise ValueError(f"knowledge graph contains a duplicate document path: {relative}")
            graph_document_paths.add(relative)
        elif node.get("document_path"):
            _, relative = _safe_published_path(root, node.get("document_path"), "graph node document")
            if relative not in indexed_paths:
                raise ValueError(f"knowledge graph node references a document outside the published index: {relative}")
    if graph_document_paths != indexed_paths:
        raise ValueError("knowledge graph document nodes do not exactly match the published index")
    for edge in graph.get("edges") or []:
        if not isinstance(edge, dict) or str(edge.get("source")) not in node_by_id or str(edge.get("target")) not in node_by_id:
            raise ValueError("knowledge graph contains an edge with a missing endpoint")
    return graph


def publication_manifest(root: Path, index: dict[str, Any]) -> str:
    load_validated_graph(root, index)
    relative_paths = {"knowledge-index.json", str((index.get("graph") or {}).get("path") or "knowledge-graph.json")}
    relative_paths.update(str(item.get("path")) for item in index.get("documents") or [] if isinstance(item, dict) and item.get("path"))
    files: list[dict[str, Any]] = []
    for relative_value in sorted(relative_paths):
        relative = Path(relative_value)
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError(f"unsafe published knowledge path: {relative}")
        path = (root / relative).resolve()
        try:
            path.relative_to(root)
        except ValueError as exc:
            raise ValueError(f"published knowledge path escapes root: {relative}") from exc
        if not path.is_file():
            raise ValueError(f"published knowledge file is missing: {relative}")
        data = path.read_bytes()
        files.append({"path": relative.as_posix(), "size": len(data), "sha256": sha256(data).hexdigest()})
    payload = json.dumps(files, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return sha256(payload).hexdigest()


def _canonical_sha256(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return sha256(payload).hexdigest()


def verify_retrieval_report(root: Path, completion: dict[str, Any]) -> dict[str, Any]:
    report_path = root.parent / "audit" / "retrieval-verification.json"
    if not report_path.is_file():
        raise ValueError("retrieval suite audit report is missing")
    report = json.loads(report_path.read_text(encoding="utf-8"))
    if not isinstance(report, dict):
        raise ValueError("retrieval suite audit report is invalid")
    expected_digest = str(completion.get("retrieval_verification_sha256") or "")
    if not expected_digest or _canonical_sha256(report) != expected_digest:
        raise ValueError("retrieval suite audit report does not match the verified completion")
    if (
        report.get("report_version") != 2
        or int(report.get("retrieval_contract_version") or 0) not in {2, 3}
        or report.get("retrieval_contract_version") != completion.get("retrieval_contract_version")
        or report.get("run_id") != completion.get("run_id")
        or report.get("status") != "passed"
        or report.get("suite_sha256") != completion.get("retrieval_suite_sha256")
    ):
        raise ValueError("retrieval suite audit metadata is invalid")
    profile = report.get("retrieval_profile")
    if report.get("retrieval_contract_version") == 3 and "retrieval_engine_version" not in report:
        raise ValueError("retrieval contract 3 requires an engine and selection binding")
    if "retrieval_engine_version" in report:
        if (report["retrieval_engine_version"] != RETRIEVAL_ENGINE_VERSION
                or completion.get("retrieval_engine_version") != RETRIEVAL_ENGINE_VERSION
                or selection_options(report.get("selection_options")) != report.get("selection_options")
                or report.get("selection_options") != completion.get("selection_options")):
            raise ValueError("retrieval engine or selection options changed; rerun the suite")
    if (
        not isinstance(profile, dict)
        or set(profile) != set(DEFAULT_RETRIEVAL_PROFILE)
        or any(not isinstance(profile.get(key), int) or isinstance(profile.get(key), bool) for key in DEFAULT_RETRIEVAL_PROFILE)
        or not 0 <= int(profile["min_score"]) <= 10_000
        or not 1 <= int(profile["max_projects"]) <= 20
        or not 0 <= int(profile["max_related"]) <= 20
        or profile != completion.get("retrieval_profile")
    ):
        raise ValueError("retrieval suite profile is invalid or does not match completion")
    checks = report.get("checks") if isinstance(report.get("checks"), dict) else {}
    if (
        (checks.get("retrieval_related_suite") or {}).get("passed") is not True
        or (checks.get("retrieval_hard_negative_suite") or {}).get("passed") is not True
    ):
        raise ValueError("retrieval suite audit checks are incomplete")
    counts = report.get("case_counts") if isinstance(report.get("case_counts"), dict) else {}
    cases = report.get("cases") if isinstance(report.get("cases"), list) else []
    total = int(counts.get("total") or 0)
    related = int(counts.get("related") or 0)
    negatives = int(counts.get("hard_negative") or 0)
    if (
        related < MIN_RELATED_CASES
        or negatives < MIN_HARD_NEGATIVE_CASES
        or total != related + negatives
        or len(cases) != total
        or int(counts.get("passed") or 0) != total
        or int(counts.get("failed") or 0) != 0
        or any(not isinstance(case, dict) or case.get("passed") is not True for case in cases)
        or sum(case.get("kind") == "related" for case in cases if isinstance(case, dict)) != related
        or sum(case.get("kind") == "hard_negative" for case in cases if isinstance(case, dict)) != negatives
    ):
        raise ValueError("retrieval suite audit case accounting is invalid")
    report_manifest = report.get("publication_manifest") if isinstance(report.get("publication_manifest"), dict) else {}
    if report_manifest.get("sha256") != completion.get("publication_manifest_sha256"):
        raise ValueError("retrieval suite audit publication manifest does not match completion")
    return report


def verify_publication(root: Path, index: dict[str, Any]) -> dict[str, Any]:
    if (root.parent / "audit" / "lifecycle-transaction.json").exists():
        raise ValueError("a lifecycle transaction is pending; resume it before reading maintained knowledge")
    completion_path = root.parent / "audit" / "completion-report.json"
    if not completion_path.is_file():
        raise ValueError("legacy-unverified knowledge base: completion report is missing")
    completion = json.loads(completion_path.read_text(encoding="utf-8"))
    if not isinstance(completion, dict):
        raise ValueError("invalid completion report")
    index_run_id = str(index.get("run_id") or "")
    completion_run_id = str(completion.get("run_id") or "")
    if not index_run_id or completion_run_id != index_run_id:
        raise ValueError("completion run_id does not match knowledge-index run_id")
    gates = completion.get("gates") or {}
    missing_prerequisites = [gate for gate in PUBLICATION_PREREQUISITE_GATES if gates.get(gate) is not True]
    if gates.get("unsupported_formats_clear") is not True and gates.get("unsupported_formats_acknowledged") is not True:
        missing_prerequisites.append("unsupported_formats_acknowledged")
    if missing_prerequisites:
        raise ValueError("knowledge publication prerequisite gates are incomplete: " + ", ".join(missing_prerequisites))
    retrieval_contract = int(completion.get("retrieval_contract_version") or 1)
    if retrieval_contract >= 2:
        if gates.get("retrieval_related_suite") is not True or gates.get("retrieval_hard_negative_suite") is not True:
            raise ValueError("retrieval suite gates are not complete")
        if not str(completion.get("retrieval_suite_sha256") or ""):
            raise ValueError("retrieval suite manifest is missing")
        verify_retrieval_report(root, completion)
    elif gates.get("retrieval_related_match") is not True or gates.get("retrieval_unrelated_no_match") is not True:
        raise ValueError("legacy-unverified knowledge base: retrieval verification gates are not complete")
    if completion.get("status") not in {"complete", "complete_with_unsupported_formats"}:
        raise ValueError("knowledge-base completion status is not final")
    expected_manifest = str(completion.get("publication_manifest_sha256") or "")
    if not expected_manifest:
        raise ValueError("legacy-unverified knowledge base: publication manifest is missing")
    if publication_manifest(root, index) != expected_manifest:
        raise ValueError("published knowledge files changed after retrieval verification")
    return completion


def tokenize(text: str) -> list[str]:
    # Unicode lexical matching, not translation or linguistic segmentation.
    normalized = unicodedata.normalize("NFKC", text).casefold()
    tokens: list[str] = []
    fragment = ""
    script = ""

    def flush() -> None:
        if len(fragment) >= 2:
            tokens.append(fragment)
            if script == "cjk" and len(fragment) >= 3:
                tokens.extend(fragment[i : i + 2] for i in range(len(fragment) - 1))

    for char in normalized:
        code = ord(char)
        cjk = (0x3040 <= code <= 0x30FF or 0x3400 <= code <= 0x9FFF
               or 0x20000 <= code <= 0x3134F)
        category = unicodedata.category(char)
        kind = "cjk" if cjk else "word" if category[0] in "LN" else ""
        if fragment and (category[0] == "M" or (script == "word" and char in "_.-")):
            fragment += char
        elif kind:
            if script and script != kind:
                flush()
                fragment = ""
            fragment += char
            script = kind
        else:
            flush()
            fragment, script = "", ""
    flush()
    return tokens


def score(task: str, task_tokens: set[str], document: dict[str, Any]) -> int:
    task_tokens = task_tokens - GENERIC_QUERY_TOKENS
    if not task_tokens:
        return 0
    normalize = lambda value: unicodedata.normalize("NFKC", str(value)).casefold()
    title = normalize(document.get("title") or "")
    aliases = [normalize(item) for item in document.get("aliases") or []]
    keywords = {normalize(item) for item in document.get("keywords") or []}
    value = 4 * len(task_tokens & keywords)
    lowered = normalize(task).strip()
    if lowered and lowered in title:
        value += 20
    if any(lowered and lowered in alias for alias in aliases):
        value += 24
    for token in task_tokens:
        if token in title:
            value += 3
        if any(token in alias for alias in aliases):
            value += 4
    return value


def _safe_document(root: Path, item: dict[str, Any], allowed_paths: set[str], emit_content: bool = False) -> dict[str, Any]:
    path, relative_value = _safe_published_path(root, item["path"], "selected document")
    if relative_value not in allowed_paths:
        raise ValueError(f"selected document is outside the published index: {relative_value}")
    relative = Path(relative_value)
    result = {"path": str(path), "relative_path": relative.as_posix(), "title": item.get("title"), "type": item.get("type")}
    for key in (
        "project_key",
        "relevance_score",
        "relationship",
        "traversal",
        "edge_relation",
        "edge_direction",
        "edge_source_path",
        "edge_target_path",
        "edge_id",
        "confidence",
        "evidence_basis",
        "evidence_event_ids",
    ):
        if key in item and item[key] is not None:
            result[key] = item[key]
    if emit_content:
        result["content"] = path.read_text(encoding="utf-8")
    return result


def query(
    kb: Path,
    task: str,
    max_projects: int | None = None,
    max_related: int | None = None,
    min_score: int | None = None,
    emit_content: bool = False,
    context_sufficient: bool = False,
    base_context: str | None = None,
    view: str | None = None,
    max_facts: int | None = None,
    max_chars: int | None = None,
) -> dict[str, Any]:
    if context_sufficient:
        return skipped_query(task)
    overrides = {key: value for key, value in dict(base_context=base_context, view=view,
                 max_facts=max_facts, max_chars=max_chars).items() if value is not None}
    selection_options(overrides)
    root = knowledge_root(kb)
    index = json.loads((root / "knowledge-index.json").read_text(encoding="utf-8"))
    if index.get("semantic_status") != "published":
        raise ValueError("knowledge is not published; complete review and distill before reading it as maintained knowledge")
    completion = verify_publication(root, index)
    verified_selection = selection_options(completion.get("selection_options"))
    selection = selection_options({**verified_selection, **overrides})
    base_context, view = selection["base_context"], selection["view"]
    allowed_base_types = base_context_types(task, base_context)
    completion_snapshot_sha256 = _canonical_sha256(completion)
    verified_profile = (
        dict(completion.get("retrieval_profile"))
        if int(completion.get("retrieval_contract_version") or 1) >= 2
        else dict(DEFAULT_RETRIEVAL_PROFILE)
    )
    query_profile = {
        "min_score": verified_profile["min_score"] if min_score is None else min_score,
        "max_projects": verified_profile["max_projects"] if max_projects is None else max_projects,
        "max_related": verified_profile["max_related"] if max_related is None else max_related,
    }
    if query_profile["min_score"] < 0 or query_profile["max_projects"] < 0 or query_profile["max_related"] < 0:
        raise ValueError("limits and scores must be non-negative")
    min_score = query_profile["min_score"]
    max_projects = query_profile["max_projects"]
    max_related = query_profile["max_related"]
    documents = [item for item in index.get("documents") or [] if isinstance(item, dict)]
    allowed_paths = {str(item.get("path")) for item in documents if item.get("path")}
    graph = load_validated_graph(root, index)
    selected: list[dict[str, Any]] = []

    def add_type(document_type: str) -> None:
        for item in documents:
            if item.get("type") == document_type and not any(chosen.get("path") == item.get("path") for chosen in selected):
                selected.append(item)
                return

    for document_type in ("identity", "collaboration"):
        if document_type in allowed_base_types:
            add_type(document_type)
    tokens = set(tokenize(task))
    ranked = [(score(task, tokens, item), item) for item in documents if item.get("type") == "project"]
    ranked.sort(key=lambda pair: (-pair[0], str(pair[1].get("title") or ""), str(pair[1].get("path") or "")))
    for value, item in ranked:
        if value <= 0 or value < min_score or sum(chosen.get("type") == "project" for chosen in selected) >= max_projects:
            break
        selected.append({**item, "relevance_score": value})

    if max_related:
        node_by_id = {str(node.get("id")): node for node in graph.get("nodes") or [] if isinstance(node, dict)}
        selected_paths = {str(item.get("path")) for item in selected}
        expansion_paths = {str(item.get("path")) for item in selected if item.get("type") != "evidence"}
        related: list[dict[str, Any]] = []
        for edge in graph.get("edges") or []:
            if not isinstance(edge, dict) or edge.get("status") != "confirmed":
                continue
            source = node_by_id.get(str(edge.get("source")))
            target = node_by_id.get(str(edge.get("target")))
            if not source or not target or source.get("kind") != "document" or target.get("kind") != "document":
                continue
            source_path = str(source.get("path"))
            target_path = str(target.get("path"))
            edge_metadata = {
                "edge_id": edge.get("edge_id"),
                "edge_relation": edge.get("relation"),
                "edge_direction": edge.get("direction"),
                "edge_source_path": source_path,
                "edge_target_path": target_path,
                "confidence": edge.get("confidence"),
                "evidence_basis": list(edge.get("evidence_basis") or []),
                "evidence_event_ids": list(edge.get("evidence_event_ids") or []),
            }
            if source_path in expansion_paths and target_path not in selected_paths:
                related.append(
                    {
                        "path": target_path,
                        "title": target.get("title"),
                        "type": target.get("document_type"),
                        "project_key": target.get("project_key"),
                        "relationship": edge.get("relation"),
                        "traversal": "forward",
                        **edge_metadata,
                    }
                )
            elif target_path in expansion_paths and source_path not in selected_paths:
                related.append(
                    {
                        "path": source_path,
                        "title": source.get("title"),
                        "type": source.get("document_type"),
                        "project_key": source.get("project_key"),
                        "relationship": edge.get("relation") if edge.get("direction") == "symmetric" else f"reverse of {edge.get('relation')}",
                        "traversal": "reverse",
                        **edge_metadata,
                    }
                )
        seen_paths = set(selected_paths)
        indexed_types = {str(item.get("path")): item.get("type") for item in documents}
        for item in related:
            item = {**item, "type": indexed_types.get(item["path"])}
            if item.get("type") in {"identity", "collaboration"} and item["type"] not in allowed_base_types:
                continue
            if len(seen_paths - selected_paths) >= max_related or item["path"] in seen_paths:
                continue
            seen_paths.add(item["path"])
            selected.append(item)

    matched = bool(selected)
    if matched:
        add_type("evidence")
        selected.sort(key=lambda item: item.get("type") != "evidence")
    indexed_by_path = {item["path"]: item for item in documents}
    result_items = []
    for item in selected:
        indexed = indexed_by_path[item["path"]]
        validate_units(indexed)
        result_item = _safe_document(root, item, allowed_paths,
                                    emit_content=emit_content and (view == "documents" or item.get("type") == "evidence"))
        if emit_content and view != "documents" and item.get("type") != "evidence":
            anchor_tokens = set(tokenize(str(indexed.get("title", "")) + " " + " ".join(indexed.get("aliases", []))))
            result_item.update(unit_view(indexed, tokens - GENERIC_QUERY_TOKENS - anchor_tokens, selection))
        result_items.append(result_item)
    project_matches = sum(item.get("type") == "project" and "relevance_score" in item for item in selected)
    match_status = "matched" if matched else "no_match"
    receipt = {
        "receipt_version": 1,
        "run_id": completion.get("run_id"),
        "task_sha256": sha256(task.encode("utf-8")).hexdigest(),
        "match_status": match_status,
        "selected_project_keys": sorted(
            {str(item.get("project_key")) for item in result_items if item.get("project_key")}
        ),
        "selected_document_paths": sorted(str(item.get("relative_path")) for item in result_items if item.get("relative_path")),
        "publication_manifest_sha256": completion.get("publication_manifest_sha256"),
        "query_parameters": query_profile,
        "base_context": base_context,
        "selection_options": selection,
        "retrieval_engine_version": RETRIEVAL_ENGINE_VERSION,
        "returned_content_chars": sum(len(item.get("content", "")) + item.get("returned_chars", 0) for item in result_items),
        "verified_profile_used": (query_profile == verified_profile and selection == verified_selection
                                  and completion.get("retrieval_engine_version") == RETRIEVAL_ENGINE_VERSION),
    }
    if (root.parent / "audit" / "lifecycle-transaction.json").exists():
        raise ValueError("a lifecycle transaction began during this read; retry after it is resumed")
    final_completion = json.loads((root.parent / "audit" / "completion-report.json").read_text(encoding="utf-8"))
    if not isinstance(final_completion, dict) or _canonical_sha256(final_completion) != completion_snapshot_sha256:
        raise ValueError("knowledge-base completion changed during this read; retry against a stable publication")
    return {
        "task": task,
        "knowledge_root": str(root),
        "semantic_status": "published",
        "run_id": completion.get("run_id"),
        "match_status": match_status,
        "selected_count": len(result_items),
        "project_matches": project_matches,
        "related_documents": sum("relationship" in item for item in result_items),
        "documents": result_items,
        "usage_receipt": receipt,
    }


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description="Read a bounded task-relevant set from a reviewed Agent knowledge base.")
    sub = root.add_subparsers(dest="command", required=True)
    list_parser = sub.add_parser("list", help="List local registered knowledge bases.")
    list_parser.add_argument("--config", type=Path)
    query_parser = sub.add_parser("query", help="Select task-relevant reviewed documents and evidence-backed related nodes.")
    target = query_parser.add_mutually_exclusive_group()
    target.add_argument("--name")
    target.add_argument("--kb", type=Path)
    query_parser.add_argument("--config", type=Path)
    query_parser.add_argument("--task", required=True)
    query_parser.add_argument("--max-projects", type=int, help="Override the verified suite profile for this query.")
    query_parser.add_argument("--max-related", type=int, help="Override the verified suite profile for this query.")
    query_parser.add_argument("--min-score", type=int, help="Override the verified suite profile for this query.")
    query_parser.add_argument("--emit-content", action="store_true")
    query_parser.add_argument("--view", choices=("documents", "facts", "current"))
    query_parser.add_argument("--max-facts", type=int)
    query_parser.add_argument("--max-chars", type=int)
    query_parser.add_argument("--base-context", choices=("auto", "none", "identity", "collaboration", "both"),
                              help="Select needed base context; omitted uses the verified suite selection.")
    query_parser.add_argument("--context-sufficient", action="store_true",
                              help="Skip retrieval without opening the registry or knowledge base.")
    return root


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        if args.command == "list":
            target = (args.config or default_registry_path()).expanduser()
            print(json.dumps({"registry": str(target), **load_registry(target)}, ensure_ascii=False, indent=2, sort_keys=True))
            return 0
        if args.context_sufficient:
            print(json.dumps(skipped_query(args.task), ensure_ascii=False, indent=2))
            return 0
        supplied_limits = [value for value in (args.max_projects, args.max_related, args.min_score) if value is not None]
        if supplied_limits and min(supplied_limits) < 0:
            raise ValueError("limits and scores must be non-negative")
        kb = args.kb or resolve_registered(args.name, args.config)
        print(
            json.dumps(
                query(kb, args.task, args.max_projects, args.max_related, args.min_score, args.emit_content,
                      base_context=args.base_context, view=args.view, max_facts=args.max_facts, max_chars=args.max_chars),
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
        )
        return 0
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
