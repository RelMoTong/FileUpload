Docs Overview
=============

This repository keeps documentation intentionally lean. Use this file for day‑to‑day reference, and `CHANGELOG.md` for version history.

What this app does
------------------
- GUI uploader (PySide6/PyQt5) with single-instance guard and dependency checks.
- DiskCleanupDialog: choose folders & file types, scan/delete asynchronously, optional recycle-bin deletion (send2trash), auto-clean thresholds.
- Internationalization: `zh_CN` / `en_US` via `src/core/i18n.py`; UI text should always come from translation keys.
- Packaging: PyInstaller spec `ImageUploadTool_v3.1.1.spec` reads `src.__version__` and outputs `dist/ImageUploadTool_v<version>/`.

Entry points
------------
- Runtime: `python -m src.main` (or packaged exe).
- Packaging: `pyinstaller ImageUploadTool_v3.1.1.spec --clean` (outputs to `dist/`).

Key modules
-----------
- `src/main.py`: dependency check (PySide6/PyQt5 required, pyftpdlib optional), single-instance (QLocalSocket + QSharedMemory), app bootstrap.
- `src/core/i18n.py`: translation store, language switching, listener callbacks; add new text as `disk_cleanup_*`/feature-specific keys.
- `src/ui/widgets.py`: custom widgets; DiskCleanupDialog uses QThread workers for scan/delete, respects i18n, supports recycle-bin via send2trash fallback.
- `ImageUploadTool_v3.1.1.spec`: bundles assets/config/version; hiddenimports list covers Qt modules and project packages.

Internationalization tips
-------------------------
- Import `t`/`tr` from `src.core.i18n` and avoid hardcoded text.
- Add keys to `TRANSLATIONS` with both `zh_CN` and `en_US`; reuse short keys for units (`unit_day`, `unit_second`, `word_yes/no`).

Housekeeping
------------
- Keep `docs/README.md` and `docs/CHANGELOG.md`; older scattered docs have been removed/merged.
- Generated artifacts: keep latest `dist/ImageUploadTool_v<version>/`; older `dist-*` can be archived outside the repo.
