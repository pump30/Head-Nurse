# Dispatch-to-HeadNurse Skill — Design

**Date:** 2026-06-10
**Status:** Approved, ready for implementation plan

## Goal

Let the user, on a non-Mac home computer running Claude Code, dispatch tasks to the Mac's HeadNurse agent (and through it, to Claude Code on the Mac) by creating or commenting on issues in the kanban-tasks repo. The calling Claude decides — via reasoning over the recent issue list — whether to **continue an existing thread** (comment on an open issue, which HeadNurse resumes via `--resume`) or **start a new thread** (create a new issue, which HeadNurse picks up as a fresh session).

## Non-goals

- Polling for the Mac's reply and weaving it back into the conversation (the user reads results on GitHub Mobile / web — already a working channel).
- Running on browser-based Claude.ai (this skill assumes shell access for `gh`).
- Multi-repo or multi-Mac dispatch (one home computer → one Mac, one repo).
- Hardening against prompt-injection attacks in user prompts (the calling user is the same person who owns the receiving Mac).
- Detecting whether HeadNurse.app is currently running (issues will queue in `Inbox` and be picked up whenever it next runs — by design).

## User stories

1. *Continuation.* I'm on my home Linux box, talking to Claude. I say "再问一下刚才那个 ECT-12345 的问题…". The skill recognizes there's an open issue about ECT-12345 in the recent list, asks me to confirm "comment on #N?", and posts the comment. HeadNurse on the Mac resumes the original Claude session.
2. *Fresh task.* I say "帮我整理一下昨天的会议笔记". No related issue exists; the skill drafts a title, asks me to confirm "create new issue 'Meeting notes 整理' ?", and creates it. HeadNurse picks it up as a new session.
3. *Wrong guess override.* The skill judges "comment on #7" but I actually want a fresh thread. The confirm step lets me flip to "force new" without re-running the skill.
4. *Pre-flight failures.* My home box doesn't have `gh` installed yet, or I haven't run `gh auth login`. The skill tells me exactly what to run, and exits cleanly without ever touching GitHub.

## Architecture

```
home computer (Claude Code session)
   │  user: "帮我看下 ECT-12345 那个 bug"
   ▼
┌─────────────────────────────────────────────────┐
│  skill: dispatch-to-headnurse                   │
│                                                 │
│  1. recent-issues.sh                            │  → gh issue list (10 newest)
│       returns: JSON array, body & last-comment  │
│                excerpts truncated to 200 chars  │
│                                                 │
│  2. (calling Claude reasons)                    │  → emits decision JSON:
│       prompt template embeds the JSON array     │     {action, issue_number?,
│       and the original user request             │      title?, body, confidence,
│                                                 │      reason}
│                                                 │
│  3. AskUserQuestion confirm                     │  → 3 options: ① accept
│       renders the decision JSON in plain prose  │     ② change ③ cancel
│       default = ① accept                        │
│                                                 │
│  4. send.sh comment <N> | new <title>           │  → gh issue comment / create
│       body always via --body-file - (stdin)     │     prints final issue URL
└─────────────────────────────────────────────────┘
                                                       │
                                                       ▼
                                  github.com/pump30/kanban-tasks
                                                       │
                                  Auto-add workflow → Project · Inbox
                                                       │
                                                       ▼
                                  Mac · HeadNurse.app · poll loop
                                                       │
                                  • new issue        → fresh `claude` subprocess
                                  • comment on #N    → `claude --resume <session>`
```

The skill is intentionally a thin wrapper: data fetch (`recent-issues.sh`), one act of reasoning by the calling Claude (no extra LLM call to a remote service), one human confirmation, one GitHub mutation (`send.sh`). No polling, no return-trip transcript handling. The user gets results the way they always have — on GitHub.

## Components

### `~/.claude/skills/dispatch-to-headnurse/`

```
dispatch-to-headnurse/
├── SKILL.md           — frontmatter + instructions to the calling Claude
├── README.md          — human-facing setup / usage notes
├── recent-issues.sh   — fetch + shape recent issues into JSON
├── send.sh            — post comment or create issue; honors --dry-run
└── config.example.sh  — template; user copies to ~/.config/headnurse/config.sh
```

### `recent-issues.sh`

**Input:** none (reads `HEADNURSE_REPO` from sourced config).
**Output (stdout):** JSON array, max 10 elements, ordered by `updatedAt desc`, no state filter (matches the brainstorming decision: include closed too — they are still useful context for "刚才那个完成了的事情").

```json
[
  {
    "number": 9,
    "title": "End-to-end auto test 141251",
    "state": "CLOSED",
    "updated": "2026-06-10T14:13:20Z",
    "body_excerpt": "请回复一句中文：自动化链路完全打通了。…",
    "last_comment_excerpt": "自动化链路完全打通了。"
  },
  …
]
```

Excerpts are truncated to 200 unicode characters (not bytes) with a trailing `…` if cut. `last_comment_excerpt` is `null` if the issue has no comments.

Implementation note: a single `gh issue list --state all --limit 10 --json number,title,body,updatedAt,state,comments` call returns everything; `jq` shapes the output. Truncation is `gsub` + `length` in jq.

### `send.sh`

Two subcommands:

```
send.sh comment <N>            # body via stdin
send.sh new <title>            # body via stdin
send.sh comment <N> --dry-run  # print the gh command, do not execute
send.sh new <title> --dry-run
```

Body always read from stdin and passed through to `gh` via `--body-file -`. This avoids any shell-quoting bugs around backticks, dollars, or newlines in the user's request.

On success, prints exactly one line to stdout: the issue/comment URL. On failure, propagates `gh`'s stderr verbatim and exits non-zero.

If `GH_USER` is set in config, runs `gh auth switch --user "$GH_USER"` first (no-op if already active). This handles the user's multi-account setup (pump30 for personal, dy1and / I572881 for work) without forcing a global default change.

### `config.example.sh`

```sh
# ~/.config/headnurse/config.sh — copy this file there and edit.
export HEADNURSE_REPO="pump30/kanban-tasks"
# Optional: which `gh` account to use. Omit to use the active one.
# export GH_USER="pump30"
```

### `SKILL.md` — instructions to the calling Claude

The contract has five sections:

1. **Trigger / no-trigger** (in `description:` frontmatter):
   Trigger phrases include "派给 Mac"、"扔给 HeadNurse"、"发到 kanban"、"让我家 Mac 跑"、"dispatch to mac" and obvious paraphrases.
   Do-not-trigger: any task that depends on the home computer's local files (the dispatch is text-only); explicit "在这里跑" / "不要派出去".
2. **Pre-flight**: check `command -v gh`; check `gh auth status`; check `~/.config/headnurse/config.sh` exists. On any miss, give the user the exact command(s) to fix it and stop.
3. **Reason**:
   - Run `recent-issues.sh`, capture JSON.
   - Reason over (recent issues array) + (user's original request) and emit:
     ```
     {
       "action":        "comment" | "new",
       "issue_number":  <int>      (only when action == "comment", else omit),
       "title":         "<≤60 chars CJK>"  (only when action == "new", else omit),
       "body":          "<the full task text, lightly cleaned up>",
       "confidence":    "high" | "low",
       "reason":        "<one short Chinese sentence explaining the choice>"
     }
     ```
   - Heuristics for the calling Claude (in the SKILL.md prompt):
     - "刚才那个/再问一下/接着/继续/同样的事" + a recent open issue with strong topic match → `comment` on that issue.
     - Explicit issue number ("#7", "那个 7 号") → `comment` on it (high confidence).
     - User mentions a topic with no recent match, or recent issues are closed and user changed subject → `new`.
     - Last issue is `CLOSED` and user says "再补一句" → `comment` (HeadNurse's `--resume` works on closed issues; closing only signals completion of one round).
     - When unsure → `new` with `confidence: low` and an explicit `reason`. Better an extra issue than a misrouted comment.
4. **Confirm**: render the decision JSON in human-readable form and call `AskUserQuestion` with three options:
   - "✅ 这样发" (default) → proceed
   - "✏️ 改一下" → second AskUserQuestion to pick: another open issue number, force `new`, or edit the body inline
   - "❌ 取消" → exit
   Show the `reason` and `confidence` so the user has context for the decision.
5. **Send**: run `send.sh` with the final action; print the returned URL plus a one-liner reminder that the result will appear as a comment on that issue (point user at GitHub Mobile / web).

The SKILL.md never includes the user's prompt verbatim — that travels through stdin into `send.sh`, never into a shell argument.

## Data flow

```
user prompt ─────────────────────────┐
                                      ├─→ Claude reasoning ─→ decision JSON ─→ AskUserQuestion ─→ send.sh
recent-issues.sh ──→ JSON array ─────┘                                                                │
                                                                                                       ▼
                                                                                      gh issue create / comment
                                                                                                       │
                                                                                                       ▼
                                                                                                  issue URL → user
```

Trust boundary: **everything the calling Claude sees is data the user already controls or wrote** (their own prompt + their own issue list). There is no inbound text from a third party that becomes Claude instructions, so prompt-injection mitigations are out of scope.

## Error handling

| Scenario | Behavior |
|---|---|
| `gh` not installed | Pre-flight prints `brew install gh` (mac) or `sudo apt install gh` (deb) / `winget install --id GitHub.cli` (win), exits. |
| `gh auth status` not logged in | Pre-flight prints `gh auth login`, exits. |
| `~/.config/headnurse/config.sh` missing | Pre-flight prints `cp ~/.claude/skills/dispatch-to-headnurse/config.example.sh ~/.config/headnurse/config.sh && $EDITOR ~/.config/headnurse/config.sh`, exits. |
| `recent-issues.sh` returns `[]` | Skill proceeds; calling Claude must choose `action: "new"`. |
| TLS / network failure on `gh` | `send.sh` propagates `gh`'s stderr, exits non-zero, **no automatic retry** (avoid duplicate issues). User reruns the skill. |
| Calling Claude emits `comment` with a non-existent `issue_number` | `send.sh` fails with `gh`'s "Could not resolve to an issue" message; user reruns the skill. |
| `confidence: low` but user accepts | Send anyway. Confidence is informational only. |
| User picks "改一下" → "force new" | Skill rebuilds the decision as `{action:"new", title: <auto>, body: <same>}` and goes to send. |
| HeadNurse.app not running on Mac | Skill does not check. Issue queues in `Inbox`; picked up on next agent start. |
| Body contains backticks / `$` / newlines | Always via stdin to `gh --body-file -`. No shell expansion. |
| Two skill invocations within the same second | No locking. Two distinct GitHub mutations, no race. |
| Multi-account `gh` (pump30 / dy1and / I572881) | If `GH_USER` is set, `send.sh` runs `gh auth switch -u "$GH_USER"` first. Otherwise relies on the active account. |

## Testing

No unit tests — this is a shell glue layer plus a prompt fragment; unit-test cost outweighs value. Verification is:

1. **Smoke**: `recent-issues.sh` against the real repo, eyeball the JSON for shape, truncation, no token leakage.
2. **Dry-run**: `send.sh comment 9 --dry-run` and `send.sh new "Title" --dry-run` print the exact `gh` command, do not execute.
3. **End-to-end on home computer (or simulated locally on Mac)** — three cases:
   - *New session*: empty / unrelated recent issues → skill emits `action: new` → user accepts → new issue appears → HeadNurse picks up → fresh session.
   - *Continuation*: a recent open issue exists on topic → skill emits `action: comment` → user accepts → comment appears → HeadNurse `--resume`s the same session.
   - *Override*: skill emits `action: comment`, user picks "改一下" → "force new" → new issue is created instead.
4. **Regression**: after the three cases, check `~/Library/Logs/kanban-agent.log` shows clean `Picking up task #N` for each, and the menu bar status reflects the run.

## Definition of done

- [ ] `~/.claude/skills/dispatch-to-headnurse/` populated with the five files listed.
- [ ] Three end-to-end cases (above) pass on the actual home box.
- [ ] `dry-run` flag works.
- [ ] `recent-issues.sh` truncates correctly and never prints tokens or full repo paths.
- [ ] Spec committed to `Head-Nurse` repo at `docs/superpowers/specs/2026-06-10-dispatch-to-headnurse-skill-design.md`.
- [ ] Implementation plan written next to it (separate task).

## Open questions deferred to implementation

- Whether to push the skill to a separate git repo for versioning, or keep it ad-hoc under `~/.claude/skills/`. Default: ad-hoc; revisit only if a second machine needs it.
- Whether to add a tiny "issue queue length" hint to the confirm step ("⚠ 当前 Inbox 里还有 3 个待跑的任务"). Not in scope for v1.
