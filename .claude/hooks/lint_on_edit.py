#!/usr/bin/env python3
"""PostToolUse hook: run ruff + pyright on edited Python files, feed findings back as context."""
import json
import subprocess
import sys
from pathlib import Path


def main() -> None:
    data = json.load(sys.stdin)
    path = data.get("tool_input", {}).get("file_path", "")

    if not path.endswith(".py"):
        return

    if not Path(path).exists():
        return

    findings: list[str] = []

    # --- ruff (style + common warnings, informational) ---
    ruff = subprocess.run(
        ["python3", "-m", "uv", "run", "ruff", "check", path, "--output-format=concise"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if ruff.stdout.strip():
        findings.append(f"ruff:\n{ruff.stdout.strip()}")

    # --- pyright (type checking, informational) ---
    pyright = subprocess.run(
        ["python3", "-m", "uv", "run", "pyright", path, "--outputjson"],
        capture_output=True,
        text=True,
        timeout=60,
    )
    if pyright.stdout.strip():
        try:
            result = json.loads(pyright.stdout)
            diagnostics = result.get("generalDiagnostics", [])
            errors = [d for d in diagnostics if d.get("severity") in ("error", "warning")]
            if errors:
                lines = []
                for d in errors:
                    rng = d.get("range", {})
                    row = rng.get("start", {}).get("line", 0) + 1
                    msg = d.get("message", "")
                    sev = d.get("severity", "")
                    lines.append(f"  {Path(path).name}:{row} [{sev}] {msg}")
                findings.append("pyright:\n" + "\n".join(lines))
        except json.JSONDecodeError:
            if pyright.stdout.strip():
                findings.append(f"pyright:\n{pyright.stdout.strip()}")

    if findings:
        context = "Linting findings for " + Path(path).name + ":\n\n" + "\n\n".join(findings)
        print(json.dumps({
            "hookSpecificOutput": {
                "hookEventName": "PostToolUse",
                "additionalContext": context,
            }
        }))


if __name__ == "__main__":
    main()
