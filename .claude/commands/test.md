Run the unit test suite, read the results, and fix any failures.

```bash
uv run pytest tests/unit/ --cov=src/musicdl --cov-report=term-missing -v 2>&1
```

After running:
- If all tests pass, report the coverage summary and confirm everything is green.
- If any tests fail, read each failure carefully, identify the root cause, fix the code (not the tests unless the test itself is wrong), then re-run to confirm the fix.
- Never skip or delete a failing test to make the suite pass.
