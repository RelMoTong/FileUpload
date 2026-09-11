"""Execute selected original bytecode functions with isolated files/fakes."""
import json
import os
from pathlib import Path
import queue
import tempfile
import time
import types
from unittest.mock import Mock
from PyInstaller.archive.readers import CArchiveReader

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / '.review_v351'
reader = CArchiveReader(str(OUT / 'baseline.exe'))
pyz = reader.open_embedded_archive(next(n for n in reader.toc if n.endswith('.pyz')))

def find(code, name):
    if code.co_qualname == name:
        return code
    for c in code.co_consts:
        if isinstance(c, types.CodeType):
            result = find(c, name)
            if result is not None:
                return result

def function(module, name, **globals_):
    return types.FunctionType(find(pyz.extract(module), name), globals_)

results = []
def record(name, **values):
    results.append(dict(name=name, **values))

with tempfile.TemporaryDirectory(prefix='probe-', dir=OUT) as temp:
    root = Path(temp)
    source = root / 'source'
    target = root / 'target'
    source.mkdir()
    target.mkdir()
    src = source / 'image.jpg'
    dst = target / 'image.jpg'
    src.write_bytes(b'complete source data')
    dst.write_bytes(b'part')
    worker = types.SimpleNamespace(
        retry_queue={str(src): {'count': 1, 'next': 0}},
        _running=True, _paused=False, log=Mock(), retry_count=3,
        source=str(source), target=str(target), backup='', upload_protocol='smb',
        _safe_path_operation=lambda f, *args, **kw: f(*args),
        _upload_file_by_protocol=Mock(), archive_queue=queue.Queue(),
    )
    function('src.workers.upload_worker', 'UploadWorker._process_retry_queue', os=os, time=time)(worker)
    assert not worker.retry_queue and not worker._upload_file_by_protocol.called
    record('retry_discards_partial_target', queue_remaining=len(worker.retry_queue),
           transfer_called=worker._upload_file_by_protocol.called,
           source_size=src.stat().st_size, target_size=dst.stat().st_size)

    nested = source / 'output'
    nested.mkdir()
    request = types.SimpleNamespace(source=str(source), target=str(nested), backup='', enable_backup=False)
    errors = function('src.services.upload_service', 'UploadService.validate_request',
                      os=os, UploadValidationResult=lambda errors: errors)(request)
    assert not errors
    record('nested_target_accepted', errors=list(errors))

    requested = {}
    worker = types.SimpleNamespace(_safe_path_operation=lambda f, **kw: requested.update(kw) or kw['default'])
    paths = function('src.workers.upload_worker', 'UploadWorker._get_image_files', os=os)(worker)
    assert paths == [] and requested['timeout'] == 5.0
    record('scan_timeout_returns_empty', timeout=requested['timeout'], result=paths)

    victim = root / 'changed.jpg'
    victim.write_bytes(b'old')
    item = types.SimpleNamespace(path=str(victim), size=3, mtime=victim.stat().st_mtime)
    victim.write_bytes(b'new content written after scan')
    worker = types.SimpleNamespace(request=types.SimpleNamespace(files=(item,), use_trash=False),
                                   _cancelled=False, event=Mock(), finished=Mock())
    function('src.services.cleanup_service', '_DeleteWorker.run', os=os,
             trash_supported=lambda: True)(worker)
    assert not victim.exists()
    record('manual_deletes_replaced_file', replaced_file_deleted=not victim.exists())

    # Only this archived module is executed; it imports standard-library modules.
    env = {'__name__': 'archived_resume'}
    exec(pyz.extract('src.core.resume_manager'), env)
    manager = env['ResumeManager'](root / 'resume-test')
    manager.MIN_RESUME_SIZE = 1
    uploader = env['ResumableFileUploader'](manager)
    def fail_rename(*args):
        raise OSError('injected rename failure')
    env['os'] = types.SimpleNamespace(**{name: getattr(os, name) for name in dir(os)})
    env['os'].rename = fail_rename
    success, error = uploader.upload_with_resume(str(src), str(dst))
    assert not success and not dst.exists() and src.exists()
    record('resume_commit_loses_old_target', success=success, old_target_exists=dst.exists(),
           source_exists=src.exists(), error=error)

    # Original queued path contains no source identity: a newer source is deleted.
    src.write_bytes(b'new image after prior upload')
    worker = types.SimpleNamespace(_running=True, archive_queue=queue.Queue(),
                                  enable_backup=False, backup='', log=Mock())
    worker.archive_queue.put((str(src), ''))
    worker._log_event = lambda *args, **kw: setattr(worker, '_running', False)
    function('src.workers.upload_worker', 'UploadWorker._archive_worker',
             os=os, queue=queue)(worker)
    assert not src.exists()
    record('archive_deletes_new_generation', new_source_deleted=not src.exists())

(OUT / 'probe_results.json').write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding='utf-8')
print(json.dumps(results, ensure_ascii=False, indent=2))
