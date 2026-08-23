#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import unicodedata
from hashlib import sha256
from pathlib import Path
from typing import Any


IDENTITY_TASK_RE = re.compile(r"身份|是谁|个人定位|当前方向|经历|简历|who am i|identity|career|current direction", re.IGNORECASE)
COLLAB_TASK_RE = re.compile(r"协作|偏好|表达|文风|写作|口吻|禁区|怎么配合|collabor|preference|writing|voice|style", re.IGNORECASE)


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


def verify_publication(root: Path, index: dict[str, Any]) -> dict[str, Any]:
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
    if gates.get("published_knowledge") is not True:
        raise ValueError("knowledge publication gate is not complete")
    if gates.get("retrieval_related_match") is not True or gates.get("retrieval_unrelated_no_match") is not True:
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
    lowered = unicodedata.normalize("NFKC", text).lower()
    tokens = re.findall(r"[a-z0-9][a-z0-9_.-]{1,}|[\u3400-\u9fff]{2,}", lowered)
    expanded: list[str] = []
    for token in tokens:
        expanded.append(token)
        if re.fullmatch(r"[\u3400-\u9fff]{3,}", token):
            expanded.extend(token[index : index + 2] for index in range(len(token) - 1))
    return expanded


def score(task: str, task_tokens: set[str], document: dict[str, Any]) -> int:
    title = str(document.get("title") or "").lower()
    aliases = [str(item).lower() for item in document.get("aliases") or []]
    keywords = set(str(item).lower() for item in document.get("keywords") or [])
    value = 4 * len(task_tokens & keywords)
    lowered = task.lower().strip()
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
    max_projects: int = 3,
    max_related: int = 2,
    min_score: int = 4,
    emit_content: bool = False,
) -> dict[str, Any]:
    root = knowledge_root(kb)
    index = json.loads((root / "knowledge-index.json").read_text(encoding="utf-8"))
    if index.get("semantic_status") != "published":
        raise ValueError("knowledge is not published; complete review and distill before reading it as maintained knowledge")
    completion = verify_publication(root, index)
    documents = [item for item in index.get("documents") or [] if isinstance(item, dict)]
    allowed_paths = {str(item.get("path")) for item in documents if item.get("path")}
    graph = load_validated_graph(root, index)
    selected: list[dict[str, Any]] = []

    def add_type(document_type: str) -> None:
        for item in documents:
            if item.get("type") == document_type and not any(chosen.get("path") == item.get("path") for chosen in selected):
                selected.append(item)
                return

    add_type("evidence")
    if IDENTITY_TASK_RE.search(task):
        add_type("identity")
    if COLLAB_TASK_RE.search(task):
        add_type("collaboration")
    tokens = set(tokenize(task))
    ranked = [(score(task, tokens, item), item) for item in documents if item.get("type") == "project"]
    ranked.sort(key=lambda pair: (-pair[0], str(pair[1].get("title") or ""), str(pair[1].get("path") or "")))
    for value, item in ranked:
        if value < min_score or sum(chosen.get("type") == "project" for chosen in selected) >= max_projects:
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
        for item in related:
            if len(seen_paths - selected_paths) >= max_related or item["path"] in seen_paths:
                continue
            seen_paths.add(item["path"])
            selected.append(item)

    result_items = [_safe_document(root, item, allowed_paths, emit_content=emit_content) for item in selected]
    project_matches = sum(item.get("type") == "project" and "relevance_score" in item for item in selected)
    return {
        "task": task,
        "knowledge_root": str(root),
        "semantic_status": "published",
        "run_id": completion.get("run_id"),
        "match_status": "matched" if project_matches or IDENTITY_TASK_RE.search(task) or COLLAB_TASK_RE.search(task) else "no_match",
        "selected_count": len(result_items),
        "project_matches": project_matches,
        "related_documents": sum("relationship" in item for item in result_items),
        "documents": result_items,
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
    query_parser.add_argument("--max-projects", type=int, default=3)
    query_parser.add_argument("--max-related", type=int, default=2)
    query_parser.add_argument("--min-score", type=int, default=4)
    query_parser.add_argument("--emit-content", action="store_true")
    return root


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        if args.command == "list":
            target = (args.config or default_registry_path()).expanduser()
            print(json.dumps({"registry": str(target), **load_registry(target)}, ensure_ascii=False, indent=2, sort_keys=True))
            return 0
        if min(args.max_projects, args.max_related, args.min_score) < 0:
            raise ValueError("limits and scores must be non-negative")
        kb = args.kb or resolve_registered(args.name, args.config)
        print(
            json.dumps(
                query(kb, args.task, args.max_projects, args.max_related, args.min_score, args.emit_content),
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
