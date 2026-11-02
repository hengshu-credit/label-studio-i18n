const {test,expect}=require('@playwright/test');
const zh=require('../../catalog/zh-CN.json');const t=k=>zh[k]||k;
const XML='<View><Text name="text" value="$content"/><Choices name="category" toName="text" required="true"><Choice value="yes"/><Choice value="no"/></Choices></View>';
const result=v=>[{from_name:'category',to_name:'text',type:'choices',value:{choices:[v]}}];
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

test('prediction acceptance, editable draft, refresh and human submission stay distinct',async({page})=>{
 const {p,ids}=await fixture(page,'Human confirmation fixture');await page.goto(`/projects/${p.id}/label`);const panel=page.getByTestId('labeling-workspace');
 await button(panel,'Get next task').click();await expect(panel.getByTestId('annotation-origin')).toContainText(t('Model suggestion — awaiting human acceptance'));
 await expect(panel.getByRole('checkbox',{name:/^yes/})).toBeVisible();let current=await request(page,endpoint(p)+`?task=${ids[0]}`);expect(current.task.annotations).toHaveLength(0);
 await button(panel,'Accept prediction as human annotation').click();await expect(panel.getByTestId('annotation-origin')).toContainText(t('Submitted human annotation'));
 current=await request(page,endpoint(p)+`?task=${ids[0]}`);expect(current.task.annotations).toHaveLength(1);expect(current.task.annotations[0].parent_prediction).toBe(current.task.predictions[0].id);
 const retry=await request(page,endpoint(p),{action:'accept',task:ids[0],prediction:current.task.predictions[0].id,input_hash:current.task.input_hash});expect(retry.reused).toBeTruthy();
 await button(panel,'Get next task').click();await expect(panel).toContainText(`Queue example 1`);await button(panel,'Edit prediction before submitting').click();await expect(panel.getByTestId('annotation-origin')).toContainText(t('Working draft — not submitted'));
 await panel.getByRole('checkbox',{name:/^no/}).check();await button(panel,'Save draft').click();await expect(panel).toContainText(t('Draft saved — not a submitted annotation'));
 current=await request(page,endpoint(p)+`?task=${ids[1]}`);expect(current.task.annotations).toHaveLength(0);expect(current.task.draft.result[0].value.choices).toEqual(['no']);
 await page.reload();await expect(panel.getByRole('checkbox',{name:/^no/})).toBeVisible();if(await button(panel,'Resume my saved draft').isVisible()&&!await button(panel,'Submit human annotation').isVisible())await button(panel,'Resume my saved draft').click();await expect(panel.getByRole('checkbox',{name:/^no/})).toBeChecked();
 await button(panel,'Submit human annotation').click();await expect(panel.getByTestId('annotation-origin')).toContainText(t('Submitted human annotation'));
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
 await page.setViewportSize({width:1000,height:900});expect(await page.evaluate(()=>document.documentElement.scrollWidth<=document.documentElement.clientWidth+4)).toBeTruthy();await page.evaluate(()=>window.scrollTo(0,0));const acceptBox=await button(panel,'Accept prediction as human annotation').boundingBox();expect(acceptBox.y+acceptBox.height).toBeLessThanOrEqual(900);await page.screenshot({path:'artifacts/labeling-assigned-task.png',fullPage:true,animations:'disabled'});
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
