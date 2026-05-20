---
name: code-audit
description: Use this skill when reviewing code for bugs, vulnerabilities, redundant logic, missing functionality, poor maintainability, or risky implementation choices.
---

# Code Audit Skill

## Purpose

Use this skill to inspect code before making changes.

## Review Categories

Check the code in this order:

1. Fatal bugs
2. Runtime errors
3. Logic bugs
4. Security risks
5. Data loss risks
6. Performance issues
7. Duplicate or redundant code
8. Missing validation
9. Poor maintainability
10. Incomplete features

## Output Format

For each issue, provide:

- Severity: Critical / High / Medium / Low
- File
- Location or function
- Problem
- Why it matters
- Suggested fix
- Whether it should be fixed immediately

## Rules

1. Do not modify code unless the user explicitly asks for repair.
2. Prefer concrete findings over vague suggestions.
3. If a problem is only a suspicion, mark it as "needs verification".
4. Prioritize bugs that can break production behavior.
5. After the review, provide a short repair plan.
