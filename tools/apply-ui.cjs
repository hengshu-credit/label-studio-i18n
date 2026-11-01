const fs=require('node:fs'),path=require('node:path');
const root=path.resolve(__dirname,'..'),src=path.join(root,'upstream/web');
const catalog=JSON.parse(fs.readFileSync(path.join(root,'catalog/zh-CN.json'),'utf8'));
const overrides=JSON.parse(fs.readFileSync(path.join(root,'catalog/reviewed.json'),'utf8'));
Object.assign(catalog,overrides);
const locations=JSON.parse(fs.readFileSync(path.join(root,'artifacts/ui-locations.json'),'utf8'));
const grouped=new Map();for(const x of locations){if(catalog[x.key]===undefined)throw Error(`Missing translation: ${x.key}`);if(!grouped.has(x.file))grouped.set(x.file,[]);grouped.get(x.file).push(x);}
for(const [file,rows] of grouped){
 const target=path.join(src,file),backup=path.join(root,'artifacts/original-ui',file);
 fs.mkdirSync(path.dirname(backup),{recursive:true});if(!fs.existsSync(backup))fs.copyFileSync(target,backup);
 let code=fs.readFileSync(backup,'utf8');
 // A template and literals inside its expressions can both be discovered. Rewriting
 // both with original byte ranges corrupts the source; keep only outermost ranges.
 const outer=rows.filter(r=>!rows.some(other=>other!==r&&other.start<=r.start&&other.end>=r.end&&(other.start<r.start||other.end>r.end)));
 for(const row of outer.sort((a,b)=>b.start-a.start)){
  const params=row.expressions.length?`, {${row.expressions.map((e,i)=>`value${i}: (${e})`).join(',')}}`:'';
  let expr=`__uiT(${JSON.stringify(row.key)}${params})`;
  if(row.kind==='jsx'){
   const original=code.slice(row.start,row.end);
   expr=(/^\s/.test(original)?'{" "}':'')+`{${expr}}`+(/\s$/.test(original)?'{" "}':'');
  }else if(row.kind==='attribute')expr=`{${expr}}`;
  code=code.slice(0,row.start)+expr+code.slice(row.end);
 }
 code='import { uiT as __uiT } from "@humansignal/app-common/i18n";\n'+code;
 fs.writeFileSync(target,code);
}
const dest=path.join(src,'libs/app-common/src/i18n/locales/ui-zh-CN.json');
fs.writeFileSync(dest,JSON.stringify(catalog,null,2)+'\n');
fs.writeFileSync(path.join(root,'catalog/zh-CN.json'),JSON.stringify(catalog,null,2)+'\n');
console.log(`Localized ${locations.length} UI literals in ${grouped.size} files`);
