"""Compile committed JSON resources to frontend JSON and standard Django gettext files."""
import json,struct
from pathlib import Path
root=Path(__file__).resolve().parents[1]
catalog=json.loads((root/'catalog/zh-CN.json').read_text(encoding='utf-8'))
dest=root/'upstream/web/libs/app-common/src/i18n/locales/ui-zh-CN.json'
dest.parent.mkdir(parents=True,exist_ok=True);dest.write_text(json.dumps(catalog,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
header='Content-Type: text/plain; charset=UTF-8\nLanguage: zh_Hans\nPlural-Forms: nplurals=1; plural=0;\n'
messages={'':header,**catalog};keys=sorted(messages);ids=b'';values=b'';id_offsets=[];value_offsets=[]
for key in keys:
 a=key.encode();b=messages[key].encode();id_offsets.append((len(a),len(ids)));value_offsets.append((len(b),len(values)));ids+=a+b'\0';values+=b+b'\0'
n=len(keys);start=28+16*n
mo=struct.pack('<7I',0x950412de,0,n,28,28+8*n,0,0)+b''.join(struct.pack('<2I',size,start+pos) for size,pos in id_offsets)+b''.join(struct.pack('<2I',size,start+len(ids)+pos) for size,pos in value_offsets)+ids+values
dest=root/'upstream/label_studio/locale/zh_Hans/LC_MESSAGES';dest.mkdir(parents=True,exist_ok=True)
(dest/'django.mo').write_bytes(mo)
(dest/'django.po').write_text('msgid ""\nmsgstr '+json.dumps(header)+'\n\n'+'\n\n'.join('msgid '+json.dumps(k,ensure_ascii=False)+'\nmsgstr '+json.dumps(v,ensure_ascii=False) for k,v in sorted(catalog.items()))+'\n',encoding='utf-8')
print('Compiled',len(catalog),'translations')
