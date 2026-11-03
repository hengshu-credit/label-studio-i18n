const {test, expect} = require('@playwright/test');
const zh = require('../../catalog/zh-CN.json');
const t = key => zh[key] || key;
const XML = '<View><Text name="text" value="$content"/><Choices name="category" toName="text" required="true"><Choice value="yes"/><Choice value="no"/></Choices><Labels name="entities" toName="text"><Label value="Bank"/></Labels></View>';
const prediction = [{from_name:'category',to_name:'text',type:'choices',value:{choices:['yes']}}, {id:'bank',from_name:'entities',to_name:'text',type:'labels',value:{start:0,end:4,text:'Bank',labels:['Bank']}}];
const button = (page,key) => page.getByRole('button',{name:t(key),exact:true});
let csrf, original;
async function api(page,url,data,method) {
  const r=await page.request.fetch(url,{method:method||(data?'POST':'GET'),headers:{'X-CSRFToken':csrf},...(data?{data}:{})});
  expect(r.ok(),await r.text()).toBeTruthy(); return r.json();
}
const hotkeys='/api/current-user/hotkeys/';
const binding=(key,active=true)=>({key,active});
async function fixture(page) {
  const p=await api(page,'/api/projects/',{title:'Hotkey regression fixture',label_config:XML});
  const {task_ids:ids}=await api(page,`/api/projects/${p.id}/import?return_task_ids=true`,[0,1,2].map(i=>({data:{content:`Bank example ${i}`},predictions:[{model_version:'hotkey-fixture',result:prediction}]})));
  return {p,ids,url:`/api/projects/${p.id}/labeling-workspace/`};
}
async function open(page,p) {
  await page.goto(`/projects/${p.id}/label`); await button(page,'Get next task').click();
  await expect(button(page,'Submit and next')).toBeEnabled();
}
async function defocus(page) {
  await expect(page.getByTestId('labeling-editor')).toHaveAttribute('aria-busy','false');
  await page.locator('.labeling-workspace__task-heading').click();
}
test.beforeEach(async({page,context})=>{
  await context.addCookies([{name:'django_language',value:'zh-hans',url:process.env.LABEL_STUDIO_URL||'http://127.0.0.1:8080'}]);
  await page.addInitScript(()=>localStorage.setItem('label-studio.language','zh-CN'));
  await page.goto('/user/login/');await page.locator('#email').fill('ci@example.invalid');await page.locator('#password').fill('ci-only-synthetic-password');await page.locator('#login-form button[type=submit]').click();await expect(page).not.toHaveURL(/user\/login/);
  csrf=await page.evaluate(()=>decodeURIComponent(document.cookie.split('; ').find(x=>x.startsWith('csrftoken=')).slice(10)));
  original=await api(page,hotkeys);await api(page,hotkeys,{custom_hotkeys:{}},'PATCH');
});
test.afterEach(async({page})=>{if(original)await api(page,hotkeys,{custom_hotkeys:original.custom_hotkeys},'PATCH');});

test('workspace default submit/skip use the current workflow once and preserve drafts',async({page})=>{
  const {p,ids,url}=await fixture(page);await open(page,p);
  await page.locator('.lsf-htx-richtext .htx-highlight').click();await page.keyboard.press('Backspace');
  await expect(page.locator('.lsf-htx-richtext .htx-highlight')).toHaveCount(0);
  await page.keyboard.press('Control+z');await expect(page.locator('.lsf-htx-richtext .htx-highlight')).toHaveCount(1);
  await page.keyboard.press('Control+Shift+z');await expect(page.locator('.lsf-htx-richtext .htx-highlight')).toHaveCount(0);
  await defocus(page);await page.keyboard.press('Control+Enter');
  await expect(page.locator('.labeling-workspace__task-heading')).toContainText(`#${ids[1]}`);
  const first=(await api(page,url+`?task=${ids[0]}`)).task;expect(first.annotations).toHaveLength(1);expect(first.annotations[0].result.some(r=>r.type==='labels')).toBeFalsy();
  await page.getByRole('checkbox',{name:/^no/}).check();await defocus(page);await page.keyboard.press('Control+Space');
  await expect(page.locator('.labeling-workspace__task-heading')).toContainText(`#${ids[2]}`);
  const skipped=(await api(page,url+`?task=${ids[1]}`)).task;expect(skipped.draft.result.find(r=>r.type==='choices').value.choices).toEqual(['no']);
  expect(skipped.annotations).toHaveLength(0);
  await button(page,'Task distribution settings').click();await page.getByText(t('Assign tasks to members'),{exact:true}).click();
  await page.getByLabel(t('Task IDs (comma separated)'),{exact:true}).fill('123');
  await page.keyboard.press('Control+Enter');await page.keyboard.press('Control+Space');
  expect((await api(page,url+`?task=${ids[2]}`)).task.annotations).toHaveLength(0);
  await button(page,'Task distribution settings').click();await defocus(page);
  const submissions=[];page.on('request',r=>{if(r.url().endsWith(url)&&r.method()==='POST'&&r.postDataJSON()?.action==='submit')submissions.push(r);});
  await page.keyboard.down('Control');await page.keyboard.down('Enter');await page.keyboard.down('Enter');await page.keyboard.up('Enter');await page.keyboard.up('Control');
  await expect(page.getByTestId('labeling-editor')).toHaveCount(0);expect(submissions).toHaveLength(1);
});

test('account remapping, project overrides, disabled tooltips and readonly sources work together',async({page})=>{
  const errors=[];page.on('pageerror',e=>errors.push(e.message));
  await api(page,hotkeys,{custom_hotkeys:{'annotation:annotation:submit':binding('alt+enter'),'annotation:annotation:skip':binding('alt+space'),'annotation:annotation:undo':binding('ctrl+z',false),'regions:region:delete':binding('alt+backspace')}},'PATCH');
  const {p,ids,url}=await fixture(page);
  await api(page,hotkeys+`?project=${p.id}`,{custom_hotkeys:{'annotation:annotation:submit':binding('ctrl+shift+enter')}},'PATCH');
  await open(page,p);await defocus(page);await page.keyboard.press('Alt+Enter');expect((await api(page,url+`?task=${ids[0]}`)).task.annotations).toHaveLength(0);
  await page.locator('.lsf-htx-richtext .htx-highlight').click();await page.keyboard.press('Alt+Backspace');await expect(page.locator('.lsf-htx-richtext .htx-highlight')).toHaveCount(0);
  await page.keyboard.press('Control+z');await expect(page.locator('.lsf-htx-richtext .htx-highlight')).toHaveCount(0);
  await defocus(page);await page.keyboard.press('Control+Shift+Enter');await expect(page.locator('.labeling-workspace__task-heading')).toContainText(`#${ids[1]}`);
  await page.locator('[data-testid^=suggestion-]').first().click();await expect(button(page,'Accept and next')).toBeEnabled();
  await page.locator('.lsf-htx-richtext .htx-highlight').click();await page.keyboard.press('Alt+Backspace');
  await expect(page.locator('.lsf-htx-richtext .htx-highlight')).toHaveCount(1);
  await defocus(page);await page.keyboard.press('Control+Shift+Enter');await expect(page.locator('.labeling-workspace__task-heading')).toContainText(`#${ids[2]}`);
  const accepted=(await api(page,url+`?task=${ids[1]}`)).task;expect(accepted.annotations[0].parent_prediction).toBe(accepted.predictions[0].id);
  await page.goto(`/projects/${p.id}/label?task=${ids[0]}`);await expect(button(page,'Edit this annotation')).toBeEnabled();await defocus(page);await page.keyboard.press('Control+Shift+Enter');expect((await api(page,url+`?task=${ids[0]}`)).task.annotations).toHaveLength(1);
  await button(page,'Edit this annotation').click();await page.getByRole('checkbox',{name:/^no/}).check();await defocus(page);await page.keyboard.press('Control+Shift+Enter');await expect(page.locator('.labeling-workspace__task-heading')).toContainText(`#${ids[2]}`);
  expect((await api(page,url+`?task=${ids[0]}`)).task.annotations).toHaveLength(1);
  const other=await fixture(page);await open(page,other.p);await defocus(page);await page.keyboard.press('Alt+Enter');await expect(page.locator('.labeling-workspace__task-heading')).toContainText(`#${other.ids[1]}`);
  await defocus(page);await page.keyboard.press('Alt+Space');await expect(page.locator('.labeling-workspace__task-heading')).toContainText(`#${other.ids[2]}`);
  expect(errors).toEqual([]);
});

test('settings record space, arrows and shifted punctuation in usable form and persist them',async({page})=>{
  await page.goto('/user/account/hotkeys');
  for(const [id,keys,expected] of [
    ['annotation:annotation:skip','Control+Space','ctrl+space'],
    ['data_manager:dm.focus-next','Shift+ArrowDown','shift+down'],
    ['data_manager:dm.toggle-bulk-sidebar-minimization','Shift+Period','shift+.'],
    ['annotation:annotation:reject-remove','Control+Shift+Digit1','ctrl+shift+1'],
  ]) {
    const row=page.getByTestId('hotkey-'+id);await row.locator('button').last().click();
    await row.getByRole('button',{name:t('Click to record keyboard shortcut')}).press(keys);
    await button(row,'Apply').click();await expect(row.locator('kbd')).toHaveText(expected.split('+'));
  }
  // Modify a default so the section has a persisted change.
  const submit=page.getByTestId('hotkey-annotation:annotation:submit');await submit.locator('button').last().click();
  await submit.getByRole('button',{name:t('Click to record keyboard shortcut')}).press('Alt+Enter');await button(submit,'Apply').click();
  await button(page,'Save').filter({visible:true}).first().click();
  await expect.poll(async()=>(await api(page,hotkeys)).custom_hotkeys['annotation:annotation:submit']?.key).toBe('alt+enter');
  await page.reload();await expect(page.getByTestId('hotkey-annotation:annotation:submit').locator('kbd')).toHaveText(['alt','enter']);
  const {p,ids}=await fixture(page);await open(page,p);await defocus(page);await page.keyboard.press('Alt+Enter');
  await expect(page.locator('.labeling-workspace__task-heading')).toContainText(`#${ids[1]}`);
});

test('editor master switch stays disabled after refresh',async({page})=>{
  const {p,ids,url}=await fixture(page);await open(page,p);
  await page.evaluate(()=>localStorage.setItem('labelStudio:settings',JSON.stringify({...JSON.parse(localStorage.getItem('labelStudio:settings')||'{}'),enableHotkeys:false})));
  await page.reload();await expect(button(page,'Submit and next')).toBeEnabled();await defocus(page);await page.keyboard.press('Control+Enter');
  expect((await api(page,url+`?task=${ids[0]}`)).task.annotations).toHaveLength(0);
  await page.locator('.lsf-htx-richtext .htx-highlight').click();await page.keyboard.press('Backspace');await expect(page.locator('.lsf-htx-richtext .htx-highlight')).toHaveCount(1);
  await page.evaluate(()=>localStorage.setItem('labelStudio:settings',JSON.stringify({...JSON.parse(localStorage.getItem('labelStudio:settings')),enableHotkeys:true})));
  await page.reload();await expect(button(page,'Submit and next')).toBeEnabled();await defocus(page);await page.keyboard.press('Control+Enter');
  await expect(page.locator('.labeling-workspace__task-heading')).toContainText(`#${ids[1]}`);
});

test('data manager navigation works while text labeling is open',async({page})=>{
  const {p,ids}=await fixture(page);await page.goto(`/projects/${p.id}/data?task=${ids[0]}`);
  await expect(page.locator('.lsf-htx-richtext')).toContainText('Bank example 0');await page.locator('.lsf-htx-richtext').click();
  await page.keyboard.press('Shift+ArrowUp');await expect(page.locator('.lsf-htx-richtext')).toContainText('Bank example 0');
  await page.keyboard.press('Shift+ArrowDown');
  await expect(page.locator('.lsf-htx-richtext')).toContainText('Bank example 1');
  await page.locator('.lsf-htx-richtext').click();await page.keyboard.press('Shift+ArrowLeft');
  await expect(page.locator('.lsf-htx-richtext')).toHaveCount(0);
  await page.keyboard.press('Shift+ArrowRight');await expect(page.locator('.lsf-htx-richtext')).toContainText('Bank example 1');
  await page.locator('.lsf-htx-richtext').click();await page.keyboard.press('Shift+ArrowDown');await expect(page.locator('.lsf-htx-richtext')).toContainText('Bank example 2');
  await page.keyboard.press('Shift+ArrowDown');await expect(page.locator('.lsf-htx-richtext')).toContainText('Bank example 2');
});
