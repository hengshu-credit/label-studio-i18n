const {test,expect}=require('@playwright/test');const zh=require('../../catalog/zh-CN.json');const t=k=>zh[k]||k;
const xml='<View><Text name="text" value="$content"/><Choices name="category" toName="text" required="true"><Choice value="yes"/><Choice value="no"/></Choices></View>';
const result=label=>[{from_name:'category',to_name:'text',type:'choices',value:{choices:[label]}}];
let csrf;
test.beforeEach(async({page,context})=>{await context.addCookies([{name:'django_language',value:'zh-hans',url:process.env.LABEL_STUDIO_URL||'http://127.0.0.1:8080'}]);await page.addInitScript(()=>localStorage.setItem('label-studio.language','zh-CN'));await page.goto('/user/login/');await page.locator('#email').fill('ci@example.invalid');await page.locator('#password').fill('ci-only-synthetic-password');await page.locator('#login-form button[type=submit]').click();await expect(page).not.toHaveURL(/user\/login/);csrf=await page.evaluate(()=>decodeURIComponent(document.cookie.split('; ').find(x=>x.startsWith('csrftoken=')).slice(10)));});
async function api(page,url,data){const r=await page.request.fetch(url,{method:data?'POST':'GET',headers:{'X-CSRFToken':csrf},...(data?{data}:{})});expect(r.ok(),await r.text()).toBeTruthy();return r.json();}
async function fixture(page){const p=await api(page,'/api/projects/',{title:'Single sample fixture',label_config:xml});const imported=await api(page,`/api/projects/${p.id}/import?return_task_ids=true`,[{data:{content:'First isolated sample'}},{data:{content:'Second isolated sample'}}]);const provider=await api(page,`/api/ml/projects/${p.id}/workflow`,{action:'provider',name:'Configured sample model',kind:'openai',model:'synthetic',api_key:'synthetic-not-a-real-key'});return {p,tasks:imported.task_ids,provider};}
async function mockInference(page,p,task,provider){let prediction;await page.route(`**/api/projects/${p.id}/labeling-workspace/**`,async route=>{const req=route.request(),url=new URL(req.url());
 if(req.method()==='POST'&&req.postDataJSON().action==='preannotate'){const body=req.postDataJSON();expect(body.task).toBe(task);expect(body.model).toBe(`provider:${provider.id}`);prediction=await api(page,'/api/predictions/',{task,project:p.id,model_version:'sample-model-v1',score:.82,result:result('yes')});return route.fulfill({status:201,json:{id:900001,status:'queued'}});}
 if(url.searchParams.has('prediction_run'))return route.fulfill({status:200,json:{id:900001,status:'completed',prediction:{...prediction,model_name:provider.name}}});
 return route.continue();
});}
const btn=(scope,key)=>scope.getByRole('button',{name:t(key),exact:true});

test('classic editor preannotates one sample and closes/restores result tabs without deleting data',async({page})=>{
 const {p,tasks,provider}=await fixture(page);await mockInference(page,p,tasks[0],provider);await page.goto(`/projects/${p.id}/data?task=${tasks[0]}`);
 const toolbar=page.getByTestId('legacy-sample-actions');await expect(toolbar.getByLabel(t('Preannotation model'))).toHaveValue(`provider:${provider.id}`);await btn(toolbar,'Preannotate this sample').click();await expect(toolbar).toContainText(t('Prediction ready. Review it before submitting a human annotation.'));
 await expect(page.getByRole('checkbox',{name:/^yes/})).toBeChecked();let stored=(await api(page,`/api/projects/${p.id}/labeling-workspace/?task=${tasks[0]}`)).task;expect(stored.predictions).toHaveLength(1);expect(stored.annotations).toHaveLength(0);
 for(let i=0;i<6&&await page.getByTestId('annotation-tab-close').count();i++){await page.getByTestId('annotation-tab-close').first().click();}
 await expect(page.getByTestId('annotation-tab-close')).toHaveCount(0);await expect(page.getByText(t('All result tabs are closed'),{exact:true})).toBeVisible();stored=(await api(page,`/api/projects/${p.id}/labeling-workspace/?task=${tasks[0]}`)).task;expect(stored.predictions).toHaveLength(1);expect(stored.annotations).toHaveLength(0);
 await toolbar.getByRole('button',{name:new RegExp(t('Restore closed tabs'))}).click();await expect(page.getByTestId('annotation-tab-close')).not.toHaveCount(0);
 await btn(toolbar,'Annotate this sample manually').click();await expect(page.getByRole('checkbox',{name:/^yes/})).not.toBeChecked();await expect(page.getByRole('checkbox',{name:/^no/})).not.toBeChecked();await page.getByRole('checkbox',{name:/^no/}).check();
 const draftTab=page.locator('.lsf-annotation-button').filter({has:page.getByTestId('annotation-origin-badge').filter({hasText:t('Draft')})}).filter({has:page.locator('[data-testid="annotation-tab-close"]')}).last();await draftTab.getByTestId('annotation-tab-close').click();await expect(draftTab).toHaveCount(0);
 const drafts=await api(page,`/api/tasks/${tasks[0]}/drafts`);expect((Array.isArray(drafts)?drafts:drafts.results).some(d=>d.result.some(r=>r.value?.choices?.includes('no')))).toBeTruthy();
 await toolbar.getByRole('button',{name:new RegExp(t('Restore closed tabs'))}).click();const restoredDraft=page.locator('.lsf-annotation-button').filter({has:page.getByTestId('annotation-origin-badge').filter({hasText:t('Draft')})});await expect(restoredDraft).toHaveCount(1);await restoredDraft.click();await expect(page.getByRole('checkbox',{name:/^no/})).toBeChecked();await page.screenshot({path:'artifacts/sample-tabs-restored.png',fullPage:true,animations:'disabled'});
});

test('dedicated workspace offers per-sample preannotation followed by human acceptance',async({page})=>{
 const {p,tasks,provider}=await fixture(page);await mockInference(page,p,tasks[0],provider);await page.goto(`/projects/${p.id}/label?task=${tasks[0]}`);const panel=page.getByTestId('labeling-workspace');
 await expect(panel.getByTestId('annotation-origin')).toContainText(t('Unannotated sample — choose preannotation or manual labeling'));await btn(panel,'Preannotate this sample').click();await expect(panel.getByTestId('annotation-origin')).toContainText(t('Model suggestion — awaiting human acceptance'));await expect(panel.getByRole('checkbox',{name:/^yes/})).toBeChecked();
 expect((await api(page,`/api/projects/${p.id}/labeling-workspace/?task=${tasks[0]}`)).task.annotations).toHaveLength(0);
 await btn(panel,'More submission options').click();await page.getByRole('menuitem',{name:t('Accept and inspect'),exact:true}).click();await expect(panel.getByTestId('annotation-origin')).toContainText(t('Submitted human annotation'));
 const saved=(await api(page,`/api/projects/${p.id}/labeling-workspace/?task=${tasks[0]}`)).task;expect(saved.annotations).toHaveLength(1);expect(saved.annotations[0].parent_prediction).toBe(saved.predictions[0].id);
});

test('classic editor can submit a blank-start manual annotation without a prediction',async({page})=>{
 const {p,tasks}=await fixture(page);await page.goto(`/projects/${p.id}/data?task=${tasks[1]}`);const toolbar=page.getByTestId('legacy-sample-actions');await btn(toolbar,'Annotate this sample manually').click();await page.getByRole('checkbox',{name:/^no/}).check();await page.getByRole('button',{name:'提交当前标注',exact:true}).click();
 await expect.poll(async()=>(await api(page,`/api/projects/${p.id}/labeling-workspace/?task=${tasks[1]}`)).task.annotations.length).toBe(1);const saved=(await api(page,`/api/projects/${p.id}/labeling-workspace/?task=${tasks[1]}`)).task;expect(saved.predictions).toHaveLength(0);expect(saved.annotations[0].result[0].value.choices).toEqual(['no']);
});

test('classic editor accepts the generated prediction as a traced human annotation',async({page})=>{
 const {p,tasks,provider}=await fixture(page);await mockInference(page,p,tasks[0],provider);await page.goto(`/projects/${p.id}/data?task=${tasks[0]}`);const toolbar=page.getByTestId('legacy-sample-actions');
 await btn(toolbar,'Preannotate this sample').click();await expect(toolbar).toContainText(t('Prediction ready. Review it before submitting a human annotation.'));await btn(toolbar,'Accept prediction and submit').click();
 await expect.poll(async()=>(await api(page,`/api/projects/${p.id}/labeling-workspace/?task=${tasks[0]}`)).task.annotations.length).toBe(1);
 const saved=(await api(page,`/api/projects/${p.id}/labeling-workspace/?task=${tasks[0]}`)).task;expect(saved.annotations[0].result[0].value.choices).toEqual(['yes']);expect(saved.annotations[0].parent_prediction).toBe(saved.predictions[0].id);
 await expect(page.getByTestId('annotation-tab-close').first()).toBeVisible();
});
