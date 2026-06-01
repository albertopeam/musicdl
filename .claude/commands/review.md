Review the current uncommitted changes before committing.

```bash
git diff 2>&1
git diff --staged 2>&1
git status 2>&1
```

After reading the diff, provide a structured review covering:
1. **Correctness** — any logic errors, off-by-one issues, missing edge cases
2. **Architecture** — any violations of module boundaries (raw SQL outside database.py, cross-package imports, etc.)
3. **Error handling** — any swallowed exceptions, missing console output for failures
4. **Types** — any `Any`, untyped parameters, or missing return types
5. **Tests** — are the changes covered by existing tests, or do new tests need to be added

Be direct: flag real problems clearly, don't pad with praise. If the changes look good, say so briefly and suggest a commit message.

6. **Improvement proposals** — does anything in this diff reveal something outdated or missing in the project's rules, hooks, or commands? Name the specific artifact and the exact change needed. Skip this section entirely if nothing stands out — don't invent improvements to fill space.
