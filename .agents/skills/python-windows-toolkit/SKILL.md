---
name: python-windows-toolkit
description: Use this skill when developing, debugging, packaging, or improving Python tools for Windows, especially Tkinter GUI tools, file processing scripts, Office automation, logging, and PyInstaller packaging.
---

# Python Windows Toolkit Skill

## Purpose

Use this skill for Windows-focused Python tools.

Typical cases:

- Tkinter desktop tools
- file cleanup scripts
- image upload tools
- Word / Excel / PDF automation
- config.json based tools
- PyInstaller packaging
- Chinese path compatibility
- Windows startup or service behavior

## Required Practices

1. Support Windows paths and Chinese filenames.
2. Use pathlib where possible.
3. Avoid blocking the GUI thread.
4. Use background threads for long-running tasks.
5. Add clear error messages.
6. Log important operations to a logs folder.
7. Never fail silently.
8. Check whether files are occupied before overwriting.
9. Keep config in config.json when settings need persistence.
10. For PyInstaller, handle resource paths using sys._MEIPASS or an equivalent safe method.

## Output Requirements

When modifying code, provide:

- changed files
- key changes
- how to run
- how to test
- packaging command if relevant
- known limitations

## GUI Rules

- Keep the UI simple and readable.
- Use clear buttons such as Start, Pause, Test, Open Log, Open Output Folder.
- Show progress or status for long tasks.
- Never freeze the interface during file copy, upload, export, or network checks.
