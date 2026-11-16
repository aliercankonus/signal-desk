---
name: token-efficiency
description: Rules for keeping token use low in this repo — targeted reads, filtered command output, narrow test runs, short replies. Use on every task, especially when reading code, logs, test output or VPS data.
---

# Token efficiency

Context is the main cost. Every file read, command output and reply stays in the conversation and is re-sent on every turn.

## Reading code
- Search before reading: `grep -rn "symbol" src/` or Glob, then Read only the relevant range with `offset`/`limit`.
- Never read whole large files to find one function. `src/signal_desk/collector/binance_collector.py` and `research/baselines.py` are the big ones.
- Do not re-read a file after editing it; the edit tool already confirmed the change.
- Never open raw market data (`/data/raw`, `*.jsonl` captures) directly. Use `signal_desk.tools.audit_raw` or a one-line `wc -l` / `head -c 500`.

## Commands
- Always trim output: `| tail -20` for test runs, `| grep -E "ERROR|FAIL|Traceback"` for logs, `git diff --stat` before a full diff.
- Tests: run the narrowest path first (`pytest -q tests/collector`), the full suite once before opening a PR.
- Use `-q` flags and avoid verbose modes. Redirect long output to a file in the scratchpad and grep it.
- Hostinger `vps_docker_logs` returns up to 300 lines; ask only when needed and summarise, don't paste.

## Working style
- One task per session. Start a new session for an unrelated task instead of continuing a long one.
- Put durable notes in the PR description or `docs/ROADMAP.md`, not in chat, so the next session reads them cheaply.
- Don't start subagents for work you can do with a few targeted searches; each one re-reads context.
- Replies: lead with the result, skip restating the request, no long summaries of what the diff already shows.
- Pick the model for the job: a smaller model for routine edits, test fixes and status checks; the largest model for design and research decisions.

## Keep the always-loaded context small
- `CLAUDE.md` stays under ~40 lines. Move detail into skills, which load only when used.
- Add a skill only when it will be used repeatedly; delete skills that aren't.
