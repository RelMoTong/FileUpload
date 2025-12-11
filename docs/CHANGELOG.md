# Changelog (concise)

## v3.1.1
- DiskCleanupDialog: async scan/delete workers, recycle-bin option (send2trash fallback), full i18n coverage, fixed window sizing with scroll area.
- Version single-source via `src.__version__`; PyInstaller spec reads it for output names.

## v3.1.0
- SMB/FTP modes refactor, FTP server toggle, password visibility toggle.
- Dep checks on startup; modular UI/worker split; packaging script retained for backward compatibility.

## v3.0.x
- Modularized project structure; trimmed legacy entry to wrapper `pyqt_app.py`.
- UI polish, logging/permissions improvements.

## v2.x (summary)
- Introduced protocol options, permissions, auto-backup; early disk-clean notes and performance reports.

Notes
- See `docs/README.md` for current architecture/usage; older scattered docs have been merged/removed.
