#!/usr/bin/env python3
import sys


if sys.version_info < (3, 9):
    raise SystemExit("agent-session-knowledge-rebuilder requires Python 3.9 or newer.")

from session_kb.cli import main


if __name__ == "__main__":
    raise SystemExit(main())
