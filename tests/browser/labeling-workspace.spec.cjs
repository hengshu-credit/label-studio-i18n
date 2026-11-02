const {test,expect}=require('@playwright/test');
const zh=require('../../catalog/zh-CN.json');const t=k=>zh[k]||k;
const XML='<View><Text name="text" value="$content"/><Choices name="category" toName="text" required="true"><Choice value="yes"/><Choice value="no"/></Choices></View>';
const result=v=>[{from_name:'category',to_name:'text',type:'choices',value:{choices:[v]}}];
const NER_XML='<View><Text name="text" value="$content"/><Choices name="category" toName="text" required="true"><Choice value="yes"/><Choice value="no"/></Choices><Labels name="entities" toName="text"><Label value="Bank"/><Label value="Amount"/><Label value="Account"/></Labels></View>';
async function selectEntityText(page,text){await page.locator('.lsf-htx-richtext').scrollIntoViewIfNeeded();const box=await page.locator('.lsf-htx-richtext').evaluate((body,text)=>{const doc=body.ownerDocument,walker=doc.createTreeWalker(body,NodeFilter.SHOW_TEXT);let node;while(node=walker.nextNode()){const start=node.textContent.indexOf(text);if(start<0)continue;const range=doc.createRange();range.setStart(node,start);range.setEnd(node,start+text.length);const rects=Array.from(range.getClientRects());return {x1:rects[0].left,y1:rects[0].top+rects[0].height/2,x2:rects.at(-1).right,y2:rects.at(-1).top+rects.at(-1).height/2};}throw Error('Selection text missing');},text);await page.mouse.move(box.x1,box.y1);await page.mouse.down();await page.mouse.move(box.x2,box.y2,{steps:8});await page.mouse.up();}
let csrf;
test.beforeEach(async({page,context})=>{
 await context.addCookies([{name:'django_language',value:'zh-hans',url:process.env.LABEL_STUDIO_URL||'http://127.0.0.1:8080'}]);await page.addInitScript(()=>localStorage.setItem('label-studio.language','zh-CN'));
 await page.goto('/user/login/');await page.locator('#email').fill('ci@example.invalid');await page.locator('#password').fill('ci-only-synthetic-password');await page.locator('#login-form button[type=submit]').click();await expect(page).not.toHaveURL(/user\/login/);
 csrf=await page.evaluate(()=>decodeURIComponent(document.cookie.split('; ').find(x=>x.startsWith('csrftoken=')).slice(10)));
});
async function request(page,url,data,method){const r=await page.request.fetch(url,{method:method||(data?'POST':'GET'),headers:{'X-CSRFToken':csrf},...(data?{data}:{})});expect(r.ok(),await r.text()).toBeTruthy();return r.json();}
async function fixture(page,title){const p=await request(page,'/api/projects/',{title,label_config:XML});const imported=await request(page,`/api/projects/${p.id}/import?return_task_ids=true`,[0,1,2].map(i=>({data:{content:`Queue example ${i}`},predictions:[{model_version:'queue-test',score:[.8,.1,.5][i],result:result('yes')}]})));await request(page,`/api/projects/${p.id}/`,{model_version:'queue-test'},'PATCH');return {p,ids:imported.task_ids};}
const endpoint=p=>`/api/projects/${p.id}/labeling-workspace/`;
const button=(panel,key)=>panel.getByRole('button',{name:t(key),exact:true});
async function submitAndInspect(page,panel,prediction){await button(panel,'More submission options').click();await page.getByRole('menuitem',{name:t(prediction?'Accept and inspect':'Submit and inspect'),exact:true}).click();}

test('prediction acceptance, editable draft, refresh and human submission stay distinct',async({page})=>{
 const {p,ids}=await fixture(page,'Human confirmation fixture');await page.goto(`/projects/${p.id}/label`);const panel=page.getByTestId('labeling-workspace');
 await button(panel,'Get next task').click();await expect(panel.getByTestId('annotation-origin')).toContainText(t('Working draft — not submitted'));await panel.locator('[data-testid^=suggestion-]').first().click();await expect(panel.getByTestId('annotation-origin')).toContainText(t('Model suggestion — awaiting human acceptance'));
 await expect(panel.getByRole('checkbox',{name:/^yes/})).toBeVisible();let current=await request(page,endpoint(p)+`?task=${ids[0]}`);expect(current.task.annotations).toHaveLength(0);
 await submitAndInspect(page,panel,true);await expect(panel.getByTestId('annotation-origin')).toContainText(t('Submitted human annotation'));
 current=await request(page,endpoint(p)+`?task=${ids[0]}`);expect(current.task.annotations).toHaveLength(1);expect(current.task.annotations[0].parent_prediction).toBe(current.task.predictions[0].id);
 const retry=await request(page,endpoint(p),{action:'accept',task:ids[0],prediction:current.task.predictions[0].id,input_hash:current.task.input_hash});expect(retry.reused).toBeTruthy();
 await button(panel,'Get next task').click();await expect(panel).toContainText(`Queue example 1`);await expect(panel.getByTestId('annotation-origin')).toContainText(t('Working draft — not submitted'));
 await panel.getByRole('checkbox',{name:/^no/}).check();await button(panel,'Save draft').click();await expect(panel).toContainText(t('Draft saved — not a submitted annotation'));
 current=await request(page,endpoint(p)+`?task=${ids[1]}`);expect(current.task.annotations).toHaveLength(0);expect(current.task.draft.result[0].value.choices).toEqual(['no']);
 await page.reload();await expect(panel.getByRole('checkbox',{name:/^no/})).toBeVisible();if(await button(panel,'Resume my saved draft').isVisible()&&!await button(panel,'Submit and next').isVisible())await button(panel,'Resume my saved draft').click();await expect(panel.getByRole('checkbox',{name:/^no/})).toBeChecked();
 await submitAndInspect(page,panel,false);await expect(panel.getByTestId('annotation-origin')).toContainText(t('Submitted human annotation'));
 current=await request(page,endpoint(p)+`?task=${ids[1]}`);expect(current.task.annotations).toHaveLength(1);expect(current.task.annotations[0].result[0].value.choices).toEqual(['no']);expect(current.task.draft).toBeNull();
 await page.evaluate(()=>window.scrollTo(0,0));await page.screenshot({path:'artifacts/labeling-human-confirmation.png',fullPage:true,animations:'disabled'});
 await button(panel,'Get next task').click();await expect(panel).toContainText('Queue example 2');await button(panel,'Skip this task').click();await expect(panel.getByTestId('labeling-editor')).toHaveCount(0);
 await page.goto(`/projects/${p.id}/data?task=${ids[0]}`);await expect(page.getByTestId('annotation-origin-badge').filter({hasText:t('Preannotation')})).toBeVisible();await expect(page.getByTestId('annotation-origin-badge').filter({hasText:t('Human annotation')})).toBeVisible();
 expect((await request(page,endpoint(p)+`?task=${ids[0]}`)).task.annotations).toHaveLength(1);
});

test('configured uncertainty sampling and manual assignment drive the dedicated page',async({page})=>{
 const {p,ids}=await fixture(page,'Distribution fixture');await page.goto(`/projects/${p.id}/label`);const panel=page.getByTestId('labeling-workspace');
 await button(panel,'Task distribution settings').click();await panel.getByLabel(t('Task Sampling'),{exact:true}).selectOption('Uncertainty sampling');await panel.getByLabel(t('Reservation time (minutes)'),{exact:true}).fill('5');
 await button(panel,'Save distribution settings').click();await expect.poll(async()=>(await request(page,endpoint(p))).sampling).toBe('Uncertainty sampling');await button(panel,'Task distribution settings').click();
 await button(panel,'Get next task').click();await expect(panel).toContainText('Queue example 1');await button(panel,'Save draft and release task').click();
 await button(panel,'Task distribution settings').click();await panel.getByLabel(t('Task assignment mode'),{exact:true}).selectOption('manual');await button(panel,'Save distribution settings').click();await expect.poll(async()=>(await request(page,endpoint(p))).mode).toBe('manual');
 await panel.getByText(t('Assign tasks to members'),{exact:true}).click();await panel.getByLabel(t('Task IDs (comma separated)'),{exact:true}).fill(String(ids[2]));await panel.locator('fieldset input[type=checkbox]').first().check();await button(panel,'Assign selected tasks').click();await expect(panel).toContainText(t('Tasks assigned'));
 await button(panel,'Task distribution settings').click();await button(panel,'Get next task').click();await expect(panel).toContainText('Queue example 2');
 await page.setViewportSize({width:1000,height:900});expect(await page.evaluate(()=>document.documentElement.scrollWidth<=document.documentElement.clientWidth+4)).toBeTruthy();await page.evaluate(()=>window.scrollTo(0,0));const acceptBox=await button(panel,'Submit and next').boundingBox();expect(acceptBox.y+acceptBox.height).toBeLessThanOrEqual(900);await page.screenshot({path:'artifacts/labeling-assigned-task.png',fullPage:true,animations:'disabled'});
 await button(panel,'Save draft and release task').click();await page.getByRole('link',{name:'Distribution fixture',exact:true}).click();await page.getByRole('link',{name:t('Settings'),exact:true}).click();await expect(page.locator('input[name="sampling"]')).toHaveValue('Uncertainty sampling');
});

test('project settings and SPA route transitions never send an undefined project key',async({page})=>{
 const {p}=await fixture(page,'Route regression fixture');const pageErrors=[];page.on('pageerror',e=>pageErrors.push(e.message));
 await page.route('**/api/projects/*',async route=>{if(route.request().method()==='GET'&&/\/api\/projects\/\d+\/?$/.test(new URL(route.request().url()).pathname))await new Promise(r=>setTimeout(r,250));await route.continue();});
 for(const id of [p.id,1,p.id]){
  await page.goto(`/projects/${id}/settings/`);await expect(page.getByRole('button',{name:'保存常规设置',exact:true})).toBeVisible();
  for(const label of ['标注','模型与预标注','常规']){await page.getByRole('link',{name:label,exact:true}).click();await expect(page.getByText("Can't find key `pk`",{exact:false})).toHaveCount(0);}
  await page.getByRole('button',{name:'保存常规设置',exact:true}).click();
 }
 await page.goto(`/projects/${p.id}/data/export`);await expect(page.getByText(t('Export data'),{exact:true})).toBeVisible();await expect(page.getByText("Can't find key `pk`",{exact:false})).toHaveCount(0);
 await page.goto(`/projects/${p.id}/label`);await expect(page.getByTestId('labeling-workspace')).toBeVisible();expect(pageErrors).toEqual([]);
});


test('native submit menu advances the stream, opens history and exits after saving',async({page})=>{
 const {p,ids}=await fixture(page,'Native label stream fixture');await page.goto(`/projects/${p.id}/label`);const panel=page.getByTestId('labeling-workspace');
 await button(panel,'Get next task').click();await button(panel,'Submit and next').click();await expect(panel.locator('.labeling-workspace__task-heading')).toContainText(`#${ids[1]}`);
 let first=await request(page,endpoint(p)+`?task=${ids[0]}`);expect(first.task.annotations).toHaveLength(1);expect((await request(page,endpoint(p)+`?task=${ids[1]}`)).task.annotations).toHaveLength(0);
 await button(panel,'Annotation history').click();await page.getByRole('menuitem',{name:new RegExp(String(ids[0]))}).click();await expect(panel.getByTestId('annotation-origin')).toContainText(t('Submitted human annotation'));await expect(button(panel,'Annotation history')).toBeVisible();
 await button(panel,'Edit this annotation').click();await panel.getByRole('checkbox',{name:/^no/}).check();await button(panel,'More submission options').click();await page.getByRole('menuitem',{name:t('Submit and exit'),exact:true}).click();await expect(page).toHaveURL(new RegExp(`/projects/${p.id}/data`));
 const saved=await request(page,endpoint(p)+`?task=${ids[0]}`);expect(saved.task.annotations).toHaveLength(1);expect(saved.task.annotations[0].id).toBe(first.task.annotations[0].id);expect(saved.task.annotations[0].result[0].value.choices).toEqual(['no']);
});

test('workspace adds, relabels and updates text entities as a human annotation',async({page})=>{
 const p=await request(page,'/api/projects/',{title:'Entity editing regression',label_config:NER_XML});
 const imported=await request(page,`/api/projects/${p.id}/import?return_task_ids=true`,[{data:{sample_id:'ner-1',content:'Bank approved 50 THB for account 1234567890.'},predictions:[{model_version:'ner-fixture',result:[...result('yes'),{id:'bank',from_name:'entities',to_name:'text',type:'labels',value:{start:0,end:4,text:'Bank',labels:['Bank']}}]}]}]);const id=imported.task_ids[0];
 await page.goto(`/projects/${p.id}/label`);const panel=page.getByTestId('labeling-workspace');await button(panel,'Get next task').click();await expect(panel.getByTestId('annotation-origin')).toContainText(t('Working draft — not submitted'));const canvas=await panel.getByTestId('labeling-editor').boundingBox();expect(canvas.y).toBeLessThan(200);expect(canvas.height).toBeGreaterThan(380);
 await panel.locator('.lsf-label').filter({hasText:'Account'}).click();await selectEntityText(page,'1234567890');await expect(page.locator('.lsf-htx-richtext .htx-highlight').filter({hasText:'1234567890'})).toBeVisible();
 await page.locator('.lsf-htx-richtext .htx-highlight').filter({hasText:'Bank'}).click();await panel.locator('.lsf-label').filter({hasText:'Amount'}).click();await panel.getByRole('checkbox',{name:/^no/}).check();await submitAndInspect(page,panel,false);
 await expect(panel.getByTestId('annotation-origin')).toContainText(t('Submitted human annotation'));let saved=(await request(page,endpoint(p)+`?task=${id}`)).task;expect(saved.annotations).toHaveLength(1);expect(saved.annotations[0].result.find(r=>r.value.text==='1234567890')?.value.labels).toEqual(['Account']);expect(saved.annotations[0].result.find(r=>r.value.text==='Bank')?.value.labels).toEqual(['Amount']);expect(saved.predictions[0].result.find(r=>r.value.text==='Bank').value.labels).toEqual(['Bank']);const annotation=saved.annotations[0].id;
 await button(panel,'Edit this annotation').click();await expect(panel.getByTestId('annotation-origin')).toContainText(t('Working draft — not submitted'));await expect(button(panel,'Submit and next')).toBeEnabled();await panel.locator('.lsf-label').filter({hasText:'Amount'}).click();await selectEntityText(page,'50 THB');await expect(page.locator('.lsf-htx-richtext .htx-highlight').filter({hasText:'50 THB'})).toBeVisible();await page.locator('.lsf-htx-richtext .htx-highlight').filter({hasText:'Bank'}).click();await page.keyboard.press('Backspace');await expect(page.locator('.lsf-htx-richtext .htx-highlight').filter({hasText:'Bank'})).toHaveCount(0);await submitAndInspect(page,panel,false);
 await expect(panel.getByTestId('annotation-origin')).toContainText(t('Submitted human annotation'));saved=(await request(page,endpoint(p)+`?task=${id}`)).task;expect(saved.annotations).toHaveLength(1);expect(saved.annotations[0].id).toBe(annotation);expect(saved.annotations[0].result.find(r=>r.value.text==='50 THB')?.value.labels).toEqual(['Amount']);expect(saved.annotations[0].result.some(r=>r.value.text==='Bank')).toBeFalsy();
});

test('default sample columns show model evidence in the requested order after reload',async({page})=>{
 const p=await request(page,'/api/projects/',{title:'Default columns fixture',label_config:XML});await request(page,`/api/projects/${p.id}/import?return_task_ids=true`,[{data:{sample_id:'sample-1',content:'Visible source text',internal_metadata:'hidden by default'},predictions:[{model_version:'columns-v1',score:.7,result:result('yes')}]}]);await page.goto(`/projects/${p.id}/data`);
 const expected=['id','data.sample_id','data.content','predictions_model_versions','predictions_results','predictions_score','total_annotations','cancelled_annotations','total_predictions','annotators','completed_at'].map(x=>'tasks:'+x);
 const order=()=>page.locator('[data-id^="tasks:"]').evaluateAll(es=>es.map(e=>e.getAttribute('data-id')));
 await expect.poll(order).toEqual(expected);await expect(page.getByText('internal_metadata',{exact:true})).toHaveCount(0);await page.reload();await expect.poll(order).toEqual(expected);await page.screenshot({path:'artifacts/default-sample-columns.png',fullPage:true});
});
