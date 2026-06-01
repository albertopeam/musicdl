#!/usr/bin/env python3
"""Hook: block Read/Write/Edit on .env secret files."""
import json
import re
import sys

data = json.load(sys.stdin)
inp = data.get("tool_input", {})
path = inp.get("file_path", "") or inp.get("path", "") or inp.get("new_path", "")

if re.search(r"(^|/)\.env(\.local|\.production|\.staging|\.development|\.test)?$", path):
    print(json.dumps({
        "decision": "block",
        "reason": (
            ".env files are protected — API keys must stay private on this machine. "
            "Use .env.example as a reference instead."
        ),
    }))
