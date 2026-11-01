const fs=require('node:fs'),path=require('node:path'),parser=require('@babel/parser');
const root=path.resolve(__dirname,'../upstream/web');let count=0;
function walk(dir){for(const ent of fs.readdirSync(dir,{withFileTypes:true})){
 const file=path.join(dir,ent.name);
 if(ent.isDirectory()){if(!/node_modules|\.git|dist/.test(ent.name))walk(file);continue;}
 if(!/\.[jt]sx?$/.test(file)||/\.d\.ts$/.test(file))continue;
 try{parser.parse(fs.readFileSync(file,'utf8'),{sourceType:'unambiguous',plugins:['jsx',...(/\.tsx?$/.test(file)?['typescript']:[]),'decorators-legacy']});count++;}
 catch(e){throw Error(`${file}: ${e.message}`);}
}}
walk(path.join(root,'apps/labelstudio/src'));walk(path.join(root,'libs'));
console.log('Parsed source files:',count);
