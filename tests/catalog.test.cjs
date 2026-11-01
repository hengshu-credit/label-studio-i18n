const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const root=path.resolve(__dirname,'..');
const source=JSON.parse(fs.readFileSync(path.join(root,'catalog/en.json'),'utf8'));
const chinese=JSON.parse(fs.readFileSync(path.join(root,'catalog/zh-CN.json'),'utf8'));
const slots=s=>(s.match(/\{\{[^}]+\}\}|%\([^)]+\)[a-z]|%[sd]/g)||[]).sort();
test('complete resource coverage and interpolation contracts',()=>{
 assert.ok(Object.keys(source).length>1500);
 for(const [key,value] of Object.entries(source)){
  assert.equal(value,key);assert.ok(Object.hasOwn(chinese,key),key);
  assert.equal(typeof chinese[key],'string',key);
  assert.ok(chinese[key].trim()||key==='s',key);
  assert.deepEqual(slots(chinese[key]),slots(key),key);
 }
});
test(`critical workflow terminology (${process.env.TEST_LOCALE||'zh-CN'})`,()=>{
 const language=process.env.TEST_LOCALE||'zh-CN',catalog=language==='en'?source:chinese;
 const cases={'Create Project':'创建项目','Import':'导入','Export':'导出','Submit current annotation':'提交当前标注',
  'Predictions':'预测','Annotations':'标注','Retrieve Predictions':'获取预测','Delete Project':'删除项目',
  'Log in':'登录','Email Address':'电子邮箱','Password':'密码','My Account':'我的账户'};
 for(const [en,zh] of Object.entries(cases))assert.equal(catalog[en],language==='en'?en:zh);
});
test('published files are source and configuration only',()=>{
 const ignore=fs.readFileSync(path.join(root,'.gitignore'),'utf8');
 for(const name of ['/upstream/','/artifacts/','.env','**/*.sqlite*','**/data/'])assert.ok(ignore.includes(name));
 const lock=JSON.parse(fs.readFileSync(path.join(root,'upstream.lock.json'),'utf8'));
 assert.match(lock.commit,/^[a-f0-9]{40}$/);
 assert.equal(lock.repository,'https://github.com/HumanSignal/label-studio.git');
});
