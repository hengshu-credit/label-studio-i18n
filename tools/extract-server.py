"""Collect server-owned UI literals; never translate task data or field identifiers."""
import ast,html,json,re
from pathlib import Path
root=Path(__file__).resolve().parents[1];src=root/'upstream';locations=[]
def good(s):return bool(re.search('[A-Za-z]{2}',s)) and not re.match(r'https?://',s) and len(s)<1600
def add(file,start,end,key,kind):
 if good(key):locations.append(dict(file=file.relative_to(src).as_posix(),start=start,end=end,key=key,kind=kind))
for file in (src/'label_studio').rglob('*.html'):
 if '/templates/' not in file.as_posix():continue
 code=file.read_text(encoding='utf-8')
 if not re.search(r'<(?:div|span|html|body|p|h[1-6]|button|input|label|a|option|form|select|ul|li|section|title)\b',code):continue
 protected=[(m.start(),m.end()) for m in re.finditer(r'{%\s*block\s+(?:frontend_settings|[\w]*scripts?[\w]*|[\w]*js)\s*%}.*?{%\s*endblock\s*%}',code,re.S)]
 for m in re.finditer(r'<script\b.*?</script>|<style\b.*?</style>|<pre\b.*?</pre>|<code\b.*?</code>|<!--.*?-->|<[^>]+>|[^<]+',code,re.S|re.I):
  if any(a<=m.start()<b for a,b in protected):continue
  text=m.group()
  if re.match(r'<(?:script|style|pre|code|!--)',text,re.I):continue
  if text.startswith('<'):
   for attr in re.finditer(r'\b(?:aria-label|title|placeholder)=("[^"]*"|\x27[^\x27]*\x27)',text):
    value=attr.group(1)[1:-1]
    if '{' not in value:add(file,m.start()+attr.start(1)+1,m.start()+attr.end(1)-1,html.unescape(value),'html')
  else:
   for part in re.finditer(r'\{\{.*?\}\}|\{%.*?%\}|(?:(?!\{\{|\{%)[\s\S])+',text):
    value=part.group()
    if value.startswith(('{{','{%')):continue
    key=html.unescape(re.sub(r'\s+',' ',value).strip())
    if key and not key.startswith(('curl ', '{', ',', 'all:')):
     lead=len(value)-len(value.lstrip());tail=len(value.rstrip())
     add(file,m.start()+part.start()+lead,m.start()+part.start()+tail,key,'html')
 # Existing Django trans literals also need catalogue entries.
 for m in re.finditer(r'\{%\s*trans(?:late)?\s+(["\x27])(.*?)\1',code):add(file,0,0,m.group(2),'existing')
files=list((src/'label_studio/data_manager').rglob('*.py'))+[src/'label_studio/users/forms.py']
for file in files:
 code=file.read_text(encoding='utf-8');tree=ast.parse(code);lines=code.splitlines(keepends=True)
 def offset(line,col):return sum(map(len,lines[:line-1]))+len(lines[line-1].encode()[:col].decode())
 def literal(node):
  if isinstance(node,ast.Constant) and isinstance(node.value,str):add(file,offset(node.lineno,node.col_offset),offset(node.end_lineno,node.end_col_offset),node.value,'python')
 for node in ast.walk(tree):
  if isinstance(node,ast.Dict):
   for key,value in zip(node.keys,node.values):
    if isinstance(key,ast.Constant) and key.value in ('title','description','label','placeholder','message'):literal(value)
  if isinstance(node,ast.Call):
   for kw in node.keywords:
    if kw.arg in ('label','help_text'):literal(kw.value)
   if isinstance(node.func,ast.Name) and node.func.id in ('ValidationError',):
    for arg in node.args:literal(arg)
extras=['English','简体中文','Language','Log in','Sign up','Sign Up','Email Address','Password','Create Account',
        'How did you hear about Label Studio?','Github','Email or newsletter','Search engine','Friend or coworker','Ad','Other']
inventory=json.loads((root/'artifacts/ui-inventory.json').read_text(encoding='utf-8'))
known={x['key'] for x in inventory}
for key in dict.fromkeys([x['key'] for x in locations]+extras):
 if key not in known:inventory.append(dict(key=key,count=1,files=['django']));known.add(key)
(root/'artifacts/server-locations.json').write_text(json.dumps(locations,ensure_ascii=False,indent=2),encoding='utf-8')
(root/'artifacts/ui-inventory.json').write_text(json.dumps(inventory,ensure_ascii=False,indent=2),encoding='utf-8')
print(dict(server_locations=len(locations),all_strings=len(inventory)))
