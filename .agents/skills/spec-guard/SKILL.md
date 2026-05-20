---
name: spec-guard
description: Use this skill when modifying an existing software project based on SPEC.md, requirements, database schema, or user-provided change requests. It prevents uncontrolled rewrites and requires a plan before edits.
---

# SPEC Guard Skill

## Purpose

Use this skill when the user asks Codex to modify an existing project, especially when the project has SPEC.md, README, database schema, business logic, or existing UI structure.

## Core Rules

1. First inspect the repository structure, SPEC.md, README, package/config files, database schema, and relevant source files.
2. Do not modify code immediately.
3. First output an implementation plan.
4. Wait for explicit user approval such as APPROVE, APPLY, or "开始修改" before editing files.
5. Do not rewrite the whole project unless explicitly requested.
6. Preserve the existing framework, folder structure, data model, and business logic.
7. Make small, reviewable changes.
8. After changes, output:
   - changed files
   - what changed
   - why it changed
   - risks
   - test steps
   - remaining issues

## Forbidden Behavior

- Do not delete large parts of the project without explaining why.
- Do not replace the current framework.
- Do not invent requirements not present in SPEC.md or the user's request.
- Do not silently change database fields or business rules.
