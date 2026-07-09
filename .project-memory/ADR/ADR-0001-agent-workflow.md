# ADR-0001: Four-Agent Workflow and Artifact Locking

## Status
Accepted (PR 0001)

## Context
The dessmonitor project needs a structured, auditable workflow for repository
changes. A single-agent approach lacks review gates and can lead to unchecked
changes. A four-agent workflow provides separation of concerns and ensures
each change is planned, reviewed, implemented, and verified.

## Decision
We adopt a four-agent sequential workflow:

| Step | Agent           | Artifact Produced              |
|------|-----------------|--------------------------------|
| 1    | plan            | PLAN.md                        |
| 2    | plan-review     | PLAN_REVIEW.yaml               |
| 3    | coder           | CODER_REPORT.txt               |
| 4    | precommit-review| PRECOMMIT_REVIEW.yaml          |

### Key Rules
1. **Sequential execution:** No agent may proceed unless the previous artifact
   is approved.
2. **Artifact locking:** PLAN.md and PLAN_REVIEW.yaml are LOCKED after the
   planning commit. Coder and precommit-review agents must NOT edit these files.
3. **Precondition checks:** Every agent must run `git rev-parse --verify HEAD`,
   `git branch --show-current`, and `git status --short` before acting.
4. **Scope boundaries:** Each agent operates within explicit ALLOWED FILES and
   FORBIDDEN FILES boundaries defined in the task prompt.

## Consequences
- Changes are auditable through the artifact trail.
- Planning artifacts are protected from accidental modification.
- The workflow adds overhead but ensures quality gates.
- All agents must be trained on the standard.

## Compliance
- AGENT_STANDARD.txt defines agent roles and locked artifact rules.
- ORCHESTRATOR_STANDARD.txt defines prompt generation rules.
- Task prompts must enforce file boundaries.
