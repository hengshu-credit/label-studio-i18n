import ast,json,struct
from pathlib import Path
root=Path(__file__).resolve().parents[1];src=root/'upstream'
catalog=json.loads((root/'catalog/zh-CN.json').read_text(encoding='utf-8'))
catalog.update(json.loads((root/'catalog/reviewed.json').read_text(encoding='utf-8')))
locations=json.loads((root/'artifacts/server-locations.json').read_text(encoding='utf-8'))
grouped={}
for row in locations:
 if row['key'] not in catalog:raise ValueError('Missing server translation: '+row['key'])
 if row['kind']!='existing':grouped.setdefault(row['file'],[]).append(row)
for file,rows in grouped.items():
 target=src/file;backup=root/'artifacts/original-server'/file
 backup.parent.mkdir(parents=True,exist_ok=True)
 if not backup.exists():backup.write_bytes(target.read_bytes())
 code=backup.read_text(encoding='utf-8')
 for row in sorted({(r['start'],r['end']):r for r in rows}.values(),key=lambda r:r['start'],reverse=True):
  literal=json.dumps(row['key'],ensure_ascii=False)
  replacement='ui_gettext('+literal+')' if row['kind']=='python' else '{% translate '+literal+' %}'
  code=code[:row['start']]+replacement+code[row['end']:]
 if file.endswith('.py'):
  tree=ast.parse(code);line=0
  for n in tree.body:
   if (isinstance(n,ast.Expr) and isinstance(n.value,ast.Constant) and isinstance(n.value.value,str)) or (isinstance(n,ast.ImportFrom) and n.module=='__future__'):line=n.end_lineno
   else:break
  lines=code.splitlines(keepends=True);lines.insert(line,'from django.utils.translation import gettext_lazy as ui_gettext\n');code=''.join(lines);ast.parse(code)
 elif '{% load i18n %}' not in code:
  import re
  extend=re.search(r'{%\s*extends\b.*?%}',code)
  at=extend.end() if extend else 0;code=code[:at]+'\n{% load i18n %}\n'+code[at:]
 target.write_text(code,encoding='utf-8')

def compile_catalog(messages):
 messages={'':'Content-Type: text/plain; charset=UTF-8\nLanguage: zh_Hans\nPlural-Forms: nplurals=1; plural=0;\n',**messages}
 keys=sorted(messages);ids=b'';values=b'';id_offsets=[];value_offsets=[]
 for key in keys:
  a=key.encode();b=messages[key].encode();id_offsets.append((len(a),len(ids)));value_offsets.append((len(b),len(values)));ids+=a+b'\0';values+=b+b'\0'
 n=len(keys);start=28+16*n
 return (struct.pack('<7I',0x950412de,0,n,28,28+8*n,0,0)+b''.join(struct.pack('<2I',size,start+pos) for size,pos in id_offsets)+
         b''.join(struct.pack('<2I',size,start+len(ids)+pos) for size,pos in value_offsets)+ids+values)
dest=src/'label_studio/locale/zh_Hans/LC_MESSAGES';dest.mkdir(parents=True,exist_ok=True)
(dest/'django.mo').write_bytes(compile_catalog(catalog))
header='msgid ""\nmsgstr ""\n"Content-Type: text/plain; charset=UTF-8\\n"\n"Language: zh_Hans\\n"\n"Plural-Forms: nplurals=1; plural=0;\\n"\n\n'
(dest/'django.po').write_text(header+'\n\n'.join('msgid '+json.dumps(k,ensure_ascii=False)+'\nmsgstr '+json.dumps(v,ensure_ascii=False) for k,v in sorted(catalog.items()))+'\n',encoding='utf-8')
print('Localized server files:',len(grouped),'catalog entries:',len(catalog))
