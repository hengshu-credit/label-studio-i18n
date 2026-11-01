"""Fetch pinned upstream and apply this repository's reviewed source patch."""
import json,subprocess,sys
from pathlib import Path
root=Path(__file__).resolve().parents[1];src=root/'upstream';lock=json.loads((root/'upstream.lock.json').read_text())
def git(*args,check=True):return subprocess.run(['git',*args],cwd=src if src.exists() else root,check=check,
                                               stderr=None if check else subprocess.DEVNULL)
if not (src/'.git').exists():
 src.mkdir(exist_ok=True);git('init');git('remote','add','origin',lock['repository'])
 git('fetch','--depth','1','--filter=blob:none','origin',lock['commit'])
 git('sparse-checkout','set','web','label_studio','deploy','licenses','tests')
 git('checkout','--detach',lock['commit'])
head=subprocess.check_output(['git','rev-parse','HEAD'],cwd=src,text=True).strip()
if head!=lock['commit']:raise SystemExit('Upstream checkout differs from lock; use a clean directory.')
patch=root/'patches/localization.patch'
if git('apply','--reverse','--check',str(patch),check=False).returncode:
 if subprocess.check_output(['git','status','--porcelain'],cwd=src,text=True).strip():
  raise SystemExit('Upstream has unrelated local changes; refusing to overwrite them.')
 git('apply','--check',str(patch));git('apply',str(patch))
subprocess.run([sys.executable,str(root/'tools/compile-catalogs.py')],check=True)
subprocess.run([sys.executable,str(root/'tools/restore-links.py')],check=True)
print('Prepared upstream',head)
