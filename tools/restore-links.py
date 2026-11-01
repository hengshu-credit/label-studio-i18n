"""Restore upstream Git symlinks after Windows checkouts that materialize link stubs."""
from pathlib import Path
import os,subprocess
src=Path(__file__).resolve().parents[1]/'upstream'
for line in subprocess.check_output(['git','ls-tree','-r','HEAD'],cwd=src,text=True).splitlines():
 meta,name=line.split('\t',1)
 if not meta.startswith('120000 '):continue
 p=src/name
 if not p.exists() or p.is_symlink():continue
 target=p.read_text().strip()
 if not (p.parent/target).resolve().is_relative_to(src.resolve()):raise RuntimeError('Symlink escapes source tree: '+name)
 temporary=p.with_name(p.name+'.i18n-link')
 os.symlink(target,temporary,target_is_directory=(p.parent/target).is_dir())
 os.replace(temporary,p)
 print('Restored',name)
