Perform a retrospective on this session to improve the project's Claude tooling.

**Step 1 — Read all existing artifacts**

```bash
cat CLAUDE.md
cat .claude/rules/architecture.md
cat .claude/rules/testing.md
cat .claude/commands/improve.md
cat .claude/commands/review.md
cat .claude/commands/test.md
cat .claude/commands/lint.md
cat .claude/hooks/guard_env.py
cat .claude/hooks/lint_on_edit.py
cat .claude/settings.local.json
```

Also read the memory index and any memory files that look relevant:
```bash
cat /Users/alberto/.claude/projects/-Users-alberto-Documents-claude-music-downloader/memory/MEMORY.md
```

**Step 2 — Review what happened this session**

Look at the git diff to understand what changed, and recall from context:
```bash
git diff HEAD 2>/dev/null || git diff 2>/dev/null
git log --oneline -10 2>/dev/null
```

Build a mental list of:
- Errors encountered and how they were fixed
- External API or CLI changes discovered (flag renames, class renames, etc.)
- Patterns that kept recurring
- Workarounds added to the code
- Things that would have been caught earlier with a better rule or hook
- Decisions made that future sessions should know about

**Step 3 — Propose and apply improvements**

For each artifact, apply changes if something from the session warrants it. Don't change artifacts just to fill space — only update when there is a concrete learning.

| Artifact | Update when... |
|---|---|
| `CLAUDE.md` | A stack decision changed, a new constraint was discovered, or an instruction proved wrong |
| `.claude/rules/architecture.md` | A module boundary was violated and needed fixing, or a new architectural constraint emerged |
| `.claude/rules/testing.md` | A testing approach failed or a better pattern was found |
| `.claude/commands/` | A workflow was repeated manually that a command could automate, or a command's instructions proved incomplete |
| `.claude/hooks/` | Something needed to be blocked or automated that isn't covered yet |
| `.claude/settings.local.json` | A new hook matcher or permission is needed |
| Memory | A decision, constraint, or user preference was revealed that future sessions should know |

For each change:
1. State what was learned (the WHY)
2. Apply the change directly to the file
3. One-line summary of what changed

**Step 4 — Report**

End with a brief summary: which artifacts were updated and why. If nothing warranted a change, say so explicitly — that is also a valid outcome.
