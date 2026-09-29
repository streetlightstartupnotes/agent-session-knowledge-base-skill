from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path
from typing import Any

from .render import tokenize
from .context_units import selection_options, unit_view, validate_units


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
    task_tokens = task_tokens - GENERIC_QUERY_TOKENS
    if not task_tokens:
        return 0
    normalize = lambda value: unicodedata.normalize("NFKC", str(value)).casefold()
    title = normalize(document.get("title") or "")
    aliases = [normalize(item) for item in document.get("aliases") or []]
    keywords = {normalize(item) for item in document.get("keywords") or []}
    score = 4 * len(task_tokens & keywords)
    lowered = normalize(task).strip()
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
    context_sufficient: bool = False,
    base_context: str = "auto",
    view: str = "documents",
    max_facts: int = 6,
    max_chars: int = 6000,
) -> dict[str, Any]:
    if context_sufficient:
        return skipped_query(task)
    allowed_base_types = base_context_types(task, base_context)
    selection = selection_options(dict(base_context=base_context, view=view, max_facts=max_facts, max_chars=max_chars))
    if min(max_projects, max_related, min_score) < 0:
        raise ValueError("limits and scores must be non-negative")
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

    for document_type in ("identity", "collaboration"):
        if document_type in allowed_base_types:
            add_type(document_type)

    project_scores = [(_score(task, task_tokens, item), item) for item in documents if item.get("type") == "project"]
    project_scores.sort(key=lambda pair: (-pair[0], str(pair[1].get("title") or ""), str(pair[1].get("path") or "")))
    for score, item in project_scores:
        if score <= 0 or score < min_score or len([chosen for chosen in selected if chosen.get("type") == "project"]) >= max_projects:
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
            indexed_types = {str(item.get("path")): item.get("type") for item in documents}
            for item in related:
                item = {**item, "type": indexed_types.get(item["path"])}
                if item.get("type") in {"identity", "collaboration"} and item["type"] not in allowed_base_types:
                    continue
                if added >= max_related or item["path"] in seen_paths:
                    continue
                seen_paths.add(item["path"])
                selected.append(item)
                added += 1

    matched = bool(selected)
    if matched:
        add_type("evidence")
        selected.sort(key=lambda item: item.get("type") != "evidence")
    result_items: list[dict[str, Any]] = []
    indexed_by_path = {item["path"]: item for item in documents}
    for item in selected:
        indexed = indexed_by_path[item["path"]]
        validate_units(indexed)
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
        if emit_content and (view == "documents" or item.get("type") == "evidence"):
            entry["content"] = path.read_text(encoding="utf-8")
        elif emit_content:
            anchor_tokens = set(tokenize(str(indexed.get("title", "")) + " " + " ".join(indexed.get("aliases", []))))
            entry.update(unit_view(indexed, task_tokens - GENERIC_QUERY_TOKENS - anchor_tokens, selection))
        result_items.append(entry)
    project_matches = sum(item.get("type") == "project" and "relevance_score" in item for item in selected)
    return {
        "task": task,
        "knowledge_root": str(root),
        "semantic_status": semantic_status,
        "base_context": base_context,
        "selection_options": selection,
        "match_status": "matched" if matched else "no_match",
        "selected_count": len(result_items),
        "project_matches": project_matches,
        "related_documents": sum("relationship" in item for item in result_items),
        "documents": result_items,
    }
