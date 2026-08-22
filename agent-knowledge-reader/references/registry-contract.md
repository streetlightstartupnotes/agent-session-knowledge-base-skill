# Local knowledge-base registry

The reader and rebuilder share registry version 1:

```json
{
  "registry_version": 1,
  "default": "personal",
  "knowledge_bases": {
    "personal": {
      "path": "/user-selected/private/location",
      "registered_at": "ISO-8601 timestamp",
      "run_id": "published run id"
    }
  }
}
```

The platform default is `%APPDATA%/agent-session-knowledge-base/locations.json` on Windows, `~/Library/Application Support/agent-session-knowledge-base/locations.json` on macOS, and `${XDG_CONFIG_HOME:-~/.config}/agent-session-knowledge-base/locations.json` on Linux. `AGENT_KB_REGISTRY` overrides it.

The registry is private local state. Never include it, generated knowledge, raw sessions, or audit events in the public Skill repository. Registration proves only where a published knowledge base is stored; it does not grant permission to edit it.
