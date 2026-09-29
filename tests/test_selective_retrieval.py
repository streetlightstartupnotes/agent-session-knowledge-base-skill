from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from test_retrieval_gate import READER, _published_kb as _unverified_kb
from session_kb import query as rebuilder
from session_kb.render import tokenize as index_tokenize
from session_kb.cli import make_parser, run
from session_kb.verification import verify_retrieval


def _published_kb(root):
    kb = _unverified_kb(root)
    result = verify_retrieval(kb, "alpha engine", "quartz nebula", expected_project_key="project:alpha")
    if result["status"] != "passed":
        raise AssertionError(result)
    return kb


class SelectiveRetrievalTests(unittest.TestCase):
    def test_technical_recall_does_not_attach_biography(self):
        with tempfile.TemporaryDirectory() as directory:
            kb = _published_kb(Path(directory))
            for task in ("Recall my background worker retry decision for alpha engine",
                         "Recall my identity provider decision for alpha engine"):
                for reader in (rebuilder.query_knowledge, READER.query):
                    result = reader(kb, task)
                    self.assertEqual(result["project_matches"], 1)
                    self.assertNotIn("identity", {d["type"] for d in result["documents"]})

    def test_explicit_base_context_is_language_independent_and_recorded(self):
        with tempfile.TemporaryDirectory() as directory:
            kb = _published_kb(Path(directory))
            for selection in ("none", "identity", "collaboration", "both"):
                expected = {"identity", "collaboration"} if selection == "both" else (
                    set() if selection == "none" else {selection})
                for reader in (rebuilder.query_knowledge, READER.query):
                    result = reader(kb, "私の経歴", base_context=selection)
                    actual = {d["type"] for d in result["documents"]} - {"evidence"}
                    self.assertEqual(actual, expected)
                    if reader is READER.query:
                        self.assertEqual(result["usage_receipt"]["base_context"], selection)
                        self.assertFalse(result["usage_receipt"]["verified_profile_used"])
            self.assertTrue(READER.query(kb, "alpha engine")["usage_receipt"]["verified_profile_used"])

    def test_graph_cannot_reintroduce_unrequested_base_context(self):
        with tempfile.TemporaryDirectory() as directory:
            kb = _unverified_kb(Path(directory))
            path = kb / "knowledge" / "knowledge-graph.json"
            graph = json.loads(path.read_text())
            # A graph label must not widen the canonical index document's scope.
            next(n for n in graph["nodes"] if n["id"] == "doc:01-identity-and-current-direction.md")["document_type"] = "project"
            graph["edges"].insert(0, {
                "edge_id": "edge-alpha-identity", "source": "doc:projects/alpha.md",
                "target": "doc:01-identity-and-current-direction.md", "relation": "related-to",
                "direction": "symmetric", "status": "confirmed",
            })
            path.write_text(json.dumps(graph))
            self.assertEqual(verify_retrieval(kb, "alpha engine", "quartz nebula")["status"], "passed")
            for reader in (rebuilder.query_knowledge, READER.query):
                for selection in ("auto", "none"):
                    result = reader(kb, "alpha engine", base_context=selection, max_related=1)
                    self.assertEqual(result["related_documents"], 1)
                    self.assertNotIn("identity", {d["type"] for d in result["documents"]})
                    self.assertTrue(any(d.get("project_key") == "project:beta" for d in result["documents"]))

    def test_unicode_queries_match_real_published_index(self):
        names = ("إعدادات التلسكوب", "настройки телескопа", "망원경 설정", "望遠鏡の設定",
                 "réglages du télescope", "दूरबीन विन्यास")
        for name in names:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                kb = _unverified_kb(Path(directory))
                path = kb / "knowledge" / "knowledge-index.json"
                index = json.loads(path.read_text())
                project = next(d for d in index["documents"] if d.get("project_key") == "project:alpha")
                project["title"] = name
                project["keywords"] += index_tokenize(name)
                path.write_text(json.dumps(index, ensure_ascii=False))
                self.assertEqual(verify_retrieval(kb, "alpha engine", "quartz nebula")["status"], "passed")
                for reader in (rebuilder.query_knowledge, READER.query):
                    result = reader(kb, name, base_context="none")
                    self.assertEqual(result["project_matches"], 1)
                    self.assertTrue(any(d.get("project_key") == "project:alpha" for d in result["documents"]))

    def test_unicode_normalization_and_identifier_boundaries(self):
        for text in ("alpha-engine.v2", "项目Alpha引擎", "cafe\u0301", "Straße", "望遠鏡の設定", "दूरबीन"):
            self.assertEqual(index_tokenize(text), READER.tokenize(text))
            self.assertTrue(index_tokenize(text))
        self.assertIn("alpha", index_tokenize("项目Alpha引擎"))
        for scorer in (rebuilder._score, READER.score):
            self.assertGreaterEqual(scorer("cafe\u0301", set(index_tokenize("cafe\u0301")),
                                          {"title": "CAFÉ"}), 20)
            self.assertGreaterEqual(scorer("alpha", {"alpha"}, {"title": "ＡＬＰＨＡ"}), 20)

    def test_base_context_validation_and_cli_forwarding(self):
        for reader in (rebuilder.query_knowledge, READER.query):
            with self.assertRaisesRegex(ValueError, "base_context"):
                reader(Path("/not/a/real/kb"), "alpha", base_context="invalid")
        with tempfile.TemporaryDirectory() as directory:
            kb = _published_kb(Path(directory))
            argv = ["query", "--kb", str(kb), "--task", "who am I", "--base-context", "none"]
            with contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(READER.main(argv), 0)
            self.assertEqual(json.loads(output.getvalue())["documents"], [])
            with contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(run(make_parser().parse_args(argv)), 0)
            self.assertEqual(json.loads(output.getvalue())["documents"], [])

    def test_generic_tasks_return_zero_documents_in_both_readers(self):
        tasks = (
            "Fix this CSS style", "Install a voice tool", "Explain identity providers",
            "帮我润色这段写作", "继续", "continue", "write", "style", "voice",
            "请解释身份认证", "我的身份认证失败了", "这个表达式怎么计算", "test project",
            "fix my voice recorder", "check my style.css",
        )
        with tempfile.TemporaryDirectory() as directory:
            kb = _published_kb(Path(directory))
            for task in tasks:
                for reader in (rebuilder.query_knowledge, READER.query):
                    with self.subTest(task=task, reader=reader.__name__):
                        result = reader(kb, task, emit_content=True)
                        self.assertEqual(result["match_status"], "no_match")
                        self.assertEqual(result["documents"], [])

    def test_explicit_personal_requests_remain_available(self):
        cases = (
            ("who am I", "identity"),
            ("review my current identity", "identity"),
            ("我的经历", "identity"),
            ("我的写作风格", "collaboration"),
            ("use my writing voice", "collaboration"),
            ("我们所有的协作情况", "collaboration"),
        )
        with tempfile.TemporaryDirectory() as directory:
            kb = _published_kb(Path(directory))
            for task, expected in cases:
                for reader in (rebuilder.query_knowledge, READER.query):
                    with self.subTest(task=task, reader=reader.__name__):
                        result = reader(kb, task)
                        self.assertEqual(result["match_status"], "matched")
                        self.assertIn(expected, {item["type"] for item in result["documents"]})
                        self.assertEqual(result["documents"][0]["type"], "evidence")

    def test_named_project_still_matches(self):
        with tempfile.TemporaryDirectory() as directory:
            kb = _published_kb(Path(directory))
            for reader in (rebuilder.query_knowledge, READER.query):
                result = reader(kb, "continue alpha engine")
                self.assertEqual(result["project_matches"], 1)
                self.assertTrue(any(d.get("project_key") == "project:alpha" for d in result["documents"]))

    def test_skip_does_not_read_files_or_resolve_registry(self):
        for reader in (rebuilder.query_knowledge, READER.query):
            with patch.object(Path, "read_text", side_effect=AssertionError("private read")):
                result = reader(Path("/not/a/real/kb"), "continue alpha", context_sufficient=True)
                self.assertEqual(result["match_status"], "skipped")
                self.assertEqual(result["documents"], [])
                self.assertNotIn("usage_receipt", result)
        with patch.object(READER, "resolve_registered", side_effect=AssertionError("registry read")):
            with contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(READER.main(["query", "--task", "continue", "--context-sufficient"]), 0)
            self.assertIn('"skipped"', output.getvalue())
        args = make_parser().parse_args([
            "query", "--kb", "/not/a/real/kb", "--task", "continue", "--context-sufficient",
        ])
        with contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(run(args), 0)
        self.assertIn('"skipped"', output.getvalue())

    def test_generic_tokens_cannot_match_generic_aliases(self):
        document = {"title": "Continue writing", "aliases": ["style"], "keywords": ["continue", "style"]}
        for score in (rebuilder._score, READER.score):
            self.assertEqual(score("continue", {"continue"}, document), 0)
            self.assertEqual(score("style", {"style"}, document), 0)

    def test_zero_threshold_does_not_select_zero_score(self):
        with tempfile.TemporaryDirectory() as directory:
            kb = _published_kb(Path(directory))
            for reader in (rebuilder.query_knowledge, READER.query):
                self.assertEqual(reader(kb, "continue", min_score=0)["documents"], [])

    def test_two_installable_implementations_keep_intent_and_scoring_in_sync(self):
        self.assertEqual(rebuilder.IDENTITY_TASK_RE.pattern, READER.IDENTITY_TASK_RE.pattern)
        self.assertEqual(rebuilder.COLLAB_TASK_RE.pattern, READER.COLLAB_TASK_RE.pattern)
        self.assertEqual(rebuilder.GENERIC_QUERY_TOKENS, READER.GENERIC_QUERY_TOKENS)
        document = {"title": "Alpha", "aliases": ["foundation engine"], "keywords": ["alpha", "continue"]}
        for task in ("continue", "alpha engine", "foundation engine", "voice"):
            tokens = set(READER.tokenize(task))
            self.assertEqual(rebuilder._score(task, tokens, document), READER.score(task, tokens, document))


if __name__ == "__main__":
    unittest.main()
