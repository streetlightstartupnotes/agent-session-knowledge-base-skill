"""Auditable Agent session knowledge-base rebuilding."""

SCHEMA_VERSION = "1.3"
__version__ = "0.5.0"

# Each entry passed the adapter contract tests and an isolated real-sample run.
VERIFIED_ADAPTERS: set[str] = {
    "codex-jsonl",
    "clacky-json",
    "clacky-chunk",
    "claude-code-jsonl",
    "workbuddy-jsonl",
    "neo-claude-jsonl",
    "cursor-agent-jsonl",
}
