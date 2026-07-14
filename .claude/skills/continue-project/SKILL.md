---

name: continue-project
description: Continue the edge weapon detection project from an approved implementation-plan task. Use when starting a fresh Claude Code session to inspect project state, validate the requested task, implement it, run tests, and stop before the next task.
argument-hint: "<task-id> [optional instructions]"
disable-model-invocation: true

allowed-tools:

* Read
* Glob
* Grep
* Edit
* Write

# .NET restore, build, test, run, package inspection, and user-secrets

* Bash(dotnet restore *)
* Bash(dotnet build *)
* Bash(dotnet test *)
* Bash(dotnet run *)
* Bash(dotnet tool restore *)
* Bash(dotnet tool list *)
* Bash(dotnet ef *)
* Bash(dotnet list *)
* Bash(dotnet sln *)
* Bash(dotnet user-secrets *)

# Repository inspection

* Bash(git status *)
* Bash(git diff *)
* Bash(git log *)
* Bash(git branch *)
* Bash(git ls-files *)
* Bash(git add --dry-run *)

# Safe filesystem and code inspection commands

* Bash(pwd)
* Bash(ls *)
* Bash(dir *)
* Bash(tree *)
* Bash(find *)
* Bash(where *)
* Bash(which *)
* Bash(type *)
* Bash(cat *)
* Bash(head *)
* Bash(tail *)
* Bash(Get-ChildItem *)
* Bash(Get-Content *)
* Bash(Select-String *)
* Bash(Test-Path *)

# Local API and health verification

* Bash(Invoke-RestMethod http://localhost:*)
* Bash(Invoke-WebRequest http://localhost:*)
* Bash(curl http://localhost:*)

# SQL Server verification used by approved implementation tasks

* Bash(sqlcmd *)

# Process inspection and controlled termination of locally started development processes

* Bash(Get-Process *)
* Bash(Stop-Process *)

# Package and vulnerability inspection

* Bash(dotnet list * package *)

---

# Continue Project Task

Continue the Edge-Based Weapon Detection and Centralized Monitoring System project.

Requested task and optional instructions:

`$ARGUMENTS`

## Authoritative repository documents

Read these before making changes:

* `CLAUDE.md`
* `README.md`
* `docs/foundation/project-charter.md`
* `docs/foundation/vision.md`
* `docs/foundation/software-requirements-specification.md`
* `docs/foundation/engineering-principles.md`
* `docs/foundation/development-workflow.md`
* `docs/architecture/software-architecture-document.md`
* applicable files under `specs/features/`
* applicable files under `specs/implementation-plans/`

Do not reconstruct project decisions from previous conversations when repository documents are available.

## Step 1 — Resolve the requested task

Locate the requested task ID in the approved implementation plan.

Report briefly:

* task title;
* feature specifications and acceptance criteria it realizes;
* dependencies;
* files or components likely to be affected;
* required verification;
* explicitly excluded work.

If the task is missing, already completed, blocked by an unfinished dependency, or inconsistent with a frozen document, stop and report the exact issue.

Do not create a duplicate implementation-plan file for an existing task.

## Step 2 — Inspect current repository state

Inspect:

* relevant source files;
* project references;
* existing migrations;
* existing tests;
* Git status;
* latest completed-task evidence available in repository documentation.

Do not treat build artifacts or previous conversation history as authoritative project state.

## Step 3 — Execute only the requested task

Implement only the requested task.

Rules:

* preserve the frozen SRS and Architecture;
* follow the applicable Feature Specifications;
* preserve project dependency direction;
* avoid unrelated refactoring;
* do not begin the next task;
* do not add speculative future functionality;
* do not weaken security or testing requirements;
* do not commit secrets, generated credentials, `bin/`, `obj/`, database files, or local configuration.

Resolve ordinary low-level implementation choices yourself when they are already within the approved design.

Stop for clarification only when a genuine contradiction or missing decision makes implementation unsafe.

## Step 4 — Verify

Run all checks required by the task and implementation plan, including as applicable:

* restore;
* build;
* unit tests;
* integration tests;
* migrations;
* schema verification;
* API health check;
* package vulnerability inspection;
* Git status and staged-file inspection.

Fix failures caused by the requested task before reporting completion.

Do not hide warnings through suppression unless explicitly approved.

## Step 5 — Final report

Report:

1. Task ID and title.
2. Files created, modified, or removed.
3. Behavior implemented.
4. Important implementation decisions.
5. Commands executed.
6. Build result.
7. Test result.
8. Migration or database result, when applicable.
9. Security and package-vulnerability result.
10. Confirmation that excluded work was not implemented.
11. Blockers or known limitations.
12. Next task ID, without starting it.

Stop after the requested task.
