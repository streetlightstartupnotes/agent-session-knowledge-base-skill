from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .render import tokenize


IDENTITY_TASK_RE = re.compile(r"身份|是谁|个人定位|当前方向|经历|简历|who am i|identity|career|current direction", re.IGNORECASE)
COLLAB_TASK_RE = re.compile(r"协作|偏好|表达|文风|写作|口吻|禁区|怎么配合|collabor|preference|writing|voice|style", re.IGNORECASE)


def _knowledge_root(kb: Path) -> Path:
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
    """Load the exact indexed graph and bind every traversable document to the index."""

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


def _score(task: str, task_tokens: set[str], document: dict[str, Any]) -> int:
    title = str(document.get("title") or "").lower()
    aliases = [str(item).lower() for item in document.get("aliases") or []]
    keywords = set(str(item).lower() for item in document.get("keywords") or [])
    score = 4 * len(task_tokens & keywords)
    lowered = task.lower().strip()
    if lowered and lowered in title:
        score += 20
    if any(lowered and lowered in alias for alias in aliases):
        score += 24
    for token in task_tokens:
        if token in title:
            score += 3
        if any(token in alias for alias in aliases):
            score += 4
    return score


def query_knowledge(
    kb: Path,
    task: str,
    max_projects: int = 3,
    emit_content: bool = False,
    allow_draft: bool = False,
    min_score: int = 4,
    max_related: int = 2,
) -> dict[str, Any]:
    root = _knowledge_root(kb)
    index = json.loads((root / "knowledge-index.json").read_text(encoding="utf-8"))
    semantic_status = str(index.get("semantic_status") or "draft")
    if semantic_status != "published" and not allow_draft:
        raise ValueError("knowledge is not published; complete project review and run distill, or use --allow-draft only for evidence review")
    documents = index.get("documents") or []
    graph = load_validated_graph(root, index) if semantic_status == "published" else None
    task_tokens = set(tokenize(task))
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

    project_scores = [(_score(task, task_tokens, item), item) for item in documents if item.get("type") == "project"]
    project_scores.sort(key=lambda pair: (-pair[0], str(pair[1].get("title") or ""), str(pair[1].get("path") or "")))
    for score, item in project_scores:
        if score < min_score or len([chosen for chosen in selected if chosen.get("type") == "project"]) >= max_projects:
            break
        selected.append({**item, "relevance_score": score})

    if semantic_status == "published" and max_related:
        if graph is not None:
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
            added = 0
            for item in related:
                if added >= max_related or item["path"] in seen_paths:
                    continue
                seen_paths.add(item["path"])
                selected.append(item)
                added += 1

    result_items: list[dict[str, Any]] = []
    for item in selected:
        path, _ = _safe_published_path(root, item["path"], "selected document")
        entry = {
            "path": str(path),
            "title": item.get("title"),
            "type": item.get("type"),
        }
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
                entry[key] = item[key]
        if emit_content:
            entry["content"] = path.read_text(encoding="utf-8")
        result_items.append(entry)
    project_matches = sum(item.get("type") == "project" and "relevance_score" in item for item in selected)
    return {
        "task": task,
        "knowledge_root": str(root),
        "semantic_status": semantic_status,
        "match_status": "matched" if project_matches or IDENTITY_TASK_RE.search(task) or COLLAB_TASK_RE.search(task) else "no_match",
        "selected_count": len(result_items),
        "project_matches": project_matches,
        "related_documents": sum("relationship" in item for item in result_items),
        "documents": result_items,
    }
