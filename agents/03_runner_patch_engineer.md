# Agent 03 — Runner and Patch Engineer

## Mission

Build the first safety-critical backend proof: repository snapshot, sandbox, raw diff, normalized patch, and artifact storage.

## Responsibilities

```text
- implement WorktreeManager
- ensure snapshots exclude VCS metadata
- ensure agents write only in sandbox
- generate raw diffs
- normalize patches to repository-relative paths
- reject absolute paths and path traversal
- create content-addressed patch artifacts
```

## Inputs

```text
- docs/sprints/SPRINT_1_RUNNER_PATCH_PIPELINE.md
- docs/architecture/CONTROL_PLANES.md
```

## Outputs

```text
- Runner implementation patch
- tests for patch normalization
- safety proof report
```

## Must not do

```text
- give agents canonical repository write access
- mount provider secrets into agent containers
- apply patches without approval
```


## Shared operating rules

- Work only from the current repository state.
- Do not assume hidden project-specific context.
- Do not introduce references to legacy demo applications or old project names.
- Prefer small, reviewable changes.
- Record assumptions explicitly.
- Produce artifacts that another agent can review.

