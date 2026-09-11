"""Read-only inspection of archived v3.5.1; never imports application code."""
import dis
import hashlib
import io
from pathlib import Path
import types
import sys
import zipfile
from PyInstaller.archive.readers import CArchiveReader

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / '.review_v351' 
OUT.mkdir(exist_ok=True)
package = ROOT / 'dist/ImageUploadTool_v3.5.1.zip'
with zipfile.ZipFile(package) as archive:
    member = next(n for n in archive.namelist() if n.endswith('.exe'))
    exe = archive.read(member)
(OUT / 'baseline.exe').write_bytes(exe)
reader = CArchiveReader(str(OUT / 'baseline.exe'))
pyz = reader.open_embedded_archive(next(n for n in reader.toc if n.endswith('.pyz')))
inventory = []
def visit(code, module):
    inventory.append(f'{module}\t{code.co_qualname}\t{code.co_firstlineno}\t{len(code.co_code)}\t{",".join(code.co_names)}')
    if len(sys.argv) > 1 and any(code.co_qualname == arg for arg in sys.argv[1:]):
        print('\nMODULE', module, 'FUNCTION', code.co_qualname)
        dis.dis(code, depth=0)
    for c in code.co_consts:
        if isinstance(c, types.CodeType):
            visit(c, module)
for name in sorted(pyz.toc):
    if not name.startswith('src.'):
        continue
    code = pyz.extract(name)
    if not isinstance(code, types.CodeType):
        continue
    visit(code, name)
    stream = io.StringIO()
    dis.dis(code, file=stream)
    (OUT / (name + '.dis.txt')).write_text(stream.getvalue(), encoding='utf-8')
(OUT / 'inventory.tsv').write_text('\n'.join(inventory), encoding='utf-8')
print('EXE SHA256', hashlib.sha256(exe).hexdigest())
print('Code objects:', len(inventory))
print('Output:', OUT)
