Run ruff and pyright across the entire codebase and fix all findings.

```bash
uv run ruff check src/ tests/ --output-format=concise 2>&1
uv run pyright src/ 2>&1
```

After running:
- Fix all ruff violations in the affected files.
- Fix all pyright errors and warnings in the affected files.
- Re-run both tools to confirm zero findings before finishing.
- Do not suppress warnings with `# noqa` or `# type: ignore` unless there is a genuine false positive — explain why if you do.
