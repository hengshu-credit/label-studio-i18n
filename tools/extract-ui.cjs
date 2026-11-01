const fs = require('node:fs');
const path = require('node:path');
const parser = require('@babel/parser');
const traverse = require('@babel/traverse').default;
const root = path.resolve(__dirname, '../upstream/web');
const roots = ['apps/labelstudio/src', 'libs/app-common/src', 'libs/datamanager/src', 'libs/editor/src', 'libs/ui/src', 'libs/core/src'];
const prefix=process.env.UI_AUDIT_PREFIX||'';
const props = new Set(['label','title','description','placeholder','tooltip','help','helpText','emptyText','message','aria-label','ariaLabel','confirmText','cancelText','okText','submitText','buttonText']);
const entries = new Map();
props.add('header');props.add('selectedLabel');
function walk(dir) { return fs.readdirSync(dir,{withFileTypes:true}).flatMap(e=>e.isDirectory()?(/__tests__|__mocks__|node_modules|i18n|assets|generated/.test(e.name)?[]:walk(path.join(dir,e.name))):(/\.[jt]sx?$/.test(e.name)&&!/(?:test|spec|stories|story)\.[jt]sx?$|\.d\.ts$/.test(e.name)?[path.join(dir,e.name)]:[])); }
function visible(p) {
  const q=p.parentPath;
  if(q.isJSXAttribute()) return props.has(q.node.name.name);
  if(q.isObjectProperty()) return p.key==='value' && !q.node.computed && props.has(q.node.key.name||q.node.key.value);
  if(q.isJSXExpressionContainer()) return q.parentPath.isJSXElement() || (q.parentPath.isJSXAttribute()&&props.has(q.parentPath.node.name.name));
  if(q.isConditionalExpression() && p.key!=='test') return visible(q);
  if(q.isLogicalExpression() && p.key==='right') return visible(q);
  if(q.isTemplateLiteral() && p.listKey==='expressions') return visible(q);
  if(q.isAssignmentExpression() && p.key==='right' && q.node.left.type==='MemberExpression') return props.has(q.node.left.property.name);
  if(q.isArrowFunctionExpression() && p.key==='body') return visible(q);
  if(q.isReturnStatement()) {const f=p.getFunctionParent();return !!f && (f.isObjectMethod()?props.has(f.node.key.name):visible(f));}
  if(q.isCallExpression() && p.listKey==='arguments') {
    const c=q.node.callee, n=c.type==='Identifier'?c.name:c.type==='MemberExpression'&&c.object.type==='Identifier'?`${c.object.name}.${c.property.name}`:'';
    return /^(alert|confirm)$|^(toast|message|notification)\.(success|error|warning|info)$/i.test(n);
  }
  return false;
}
function good(text) {return (text==='s'||/[A-Za-z]{2}/.test(text.replace(/\{\{.*?\}\}/g,'')))&&!/^https?:|^icon:|^\/?[\w./-]+\.(?:svg|png|js|ts|css|json)$/.test(text)&&text.length<1600;}
const locations=[];
for(const file of roots.flatMap(d=>walk(path.join(root,d)))) {
 const code=fs.readFileSync(file,'utf8'); let ast;
 try { ast=parser.parse(code,{sourceType:'unambiguous',plugins:['jsx',...(file.endsWith('.ts')||file.endsWith('.tsx')?['typescript']:[]),'decorators-legacy']}); }
 catch(e){throw new Error(`${file}: ${e.message}`);}
 function add(p,key,kind,expressions=[]) {
  if(!good(key))return;
  const rel=path.relative(root,file).replaceAll('\\','/');
  const loc={file:rel,start:p.node.start,end:p.node.end,line:p.node.loc.start.line,key,kind,expressions};
  locations.push(loc);const old=entries.get(key)||{key,count:0,files:[]};old.count++;if(!old.files.includes(rel))old.files.push(rel);entries.set(key,old);
 }
 traverse(ast,{
  JSXText(p){if(p.findParent(q=>q.isJSXElement()&&['style','script','code','pre','CodeBlock'].includes(q.node.openingElement.name.name)))return;const key=p.node.value.replace(/\s+/g,' ').trim();add(p,key,'jsx');},
  StringLiteral(p){if(visible(p))add(p,p.node.value,p.parentPath.isJSXAttribute()?'attribute':'literal');},
  TemplateLiteral(p){if(visible(p)){let key='';p.node.quasis.forEach((q,i)=>{key+=q.value.cooked;if(i<p.node.expressions.length)key+=`{{value${i}}}`;});add(p,key,'template',p.node.expressions.map(e=>code.slice(e.start,e.end)));}}
 });
}
fs.mkdirSync(path.resolve(__dirname,'../artifacts'),{recursive:true});
fs.writeFileSync(path.resolve(__dirname,`../artifacts/${prefix}ui-locations.json`),JSON.stringify(locations,null,2));
const inventory=[...entries.values()].sort((a,b)=>b.count-a.count||a.key.localeCompare(b.key));
fs.writeFileSync(path.resolve(__dirname,`../artifacts/${prefix}ui-inventory.json`),JSON.stringify(inventory,null,2));
console.log(JSON.stringify({strings:inventory.length,locations:locations.length,files:new Set(locations.map(x=>x.file)).size}));
