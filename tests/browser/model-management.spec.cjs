const {test,expect}=require('@playwright/test');
test.beforeEach(async({page,context})=>{
 await context.addCookies([{name:'django_language',value:'zh-hans',url:process.env.LABEL_STUDIO_URL||'http://127.0.0.1:8080'}]);
 await page.addInitScript(()=>localStorage.setItem('label-studio.language','zh-CN'));
 await page.goto('/user/login/');await page.locator('#email').fill('ci@example.invalid');await page.locator('#password').fill('ci-only-synthetic-password');await page.locator('#login-form button[type=submit]').click();await expect(page).not.toHaveURL(/user\/login/);
});
const pending=page=>page.waitForResponse(r=>r.url().endsWith('/workflow')&&r.request().method()==='POST');
test('cloud reuse and removal affect only the destination project',async({page})=>{
 await page.goto('/projects/1/settings/ml');await expect(page).toHaveURL(/learning\?tab=models/);
 const panel=page.getByTestId('learning-workspace');await panel.getByRole('button',{name:'添加模型',exact:true}).click();
 await panel.getByLabel('模型名称',{exact:true}).fill('CI reusable cloud');await panel.getByLabel('API 密钥',{exact:true}).fill('synthetic-ci-key');
 let saved=pending(page);await panel.getByRole('button',{name:'保存模型',exact:true}).click();let response=await saved;expect(response.ok()).toBeTruthy();const source=await response.json();expect(source.key_configured).toBeTruthy();expect(source).not.toHaveProperty('api_key');
 await panel.getByTestId(`model-${source.id}`).getByRole('button',{name:'编辑配置'}).click();await expect(panel.getByLabel('API 密钥',{exact:true})).toHaveValue('');
 const csrf=await page.evaluate(()=>decodeURIComponent(document.cookie.split('; ').find(x=>x.startsWith('csrftoken=')).slice(10)));
 const config=await (await page.request.get('/api/projects/1/')).json();const created=await page.request.post('/api/projects/',{headers:{'X-CSRFToken':csrf},data:{title:'CI reuse destination',label_config:config.label_config}});expect(created.ok()).toBeTruthy();const project=await created.json();
 await page.goto(`/projects/${project.id}/settings/learning`);await panel.getByRole('button',{name:'复用其他项目模型'}).click();
 await panel.locator('.model-reuse-row').filter({hasText:'CI reusable cloud'}).getByRole('button',{name:'选择这个模型'}).click();await panel.getByLabel('在本项目显示的名称').fill('CI cloud copy');
 saved=pending(page);await panel.getByRole('button',{name:'添加到当前项目'}).click();response=await saved;expect(response.ok()).toBeTruthy();const copy=await response.json();expect(copy.key_configured).toBeTruthy();expect(copy.id).not.toBe(source.id);
 const card=panel.getByTestId(`model-${copy.id}`);await expect(card.getByRole('heading',{name:'CI cloud copy'})).toBeVisible();
 page.once('dialog',async d=>{expect(d.message()).toContain('其他项目');await d.accept();});await card.getByRole('button',{name:'从本项目移除'}).click();await expect(card).toHaveCount(0);
 const unchanged=await (await page.request.get('/api/ml/projects/1/workflow')).json();expect(unchanged.providers.find(p=>p.id===source.id).key_configured).toBeTruthy();
 await page.goto('/projects/1/settings/predictions');await expect(page).toHaveURL(/learning\?tab=prelabel/);await expect(panel.getByText('已有预标注结果',{exact:true})).toBeVisible();
});
test('unified Transformers form submits trained task model options',async({page})=>{
 await page.goto('/projects/1/settings/learning');const panel=page.getByTestId('learning-workspace');await panel.getByRole('button',{name:'添加模型',exact:true}).click();
 await panel.getByLabel('模型类型').selectOption('transformers');await panel.getByLabel('模型名称',{exact:true}).fill('CI trained classifier');await panel.getByLabel('模型目录或 Hugging Face ID').fill('E:\\models\\classifier');await panel.getByLabel('加载方式').selectOption('trained');
 await page.route('**/workflow',async route=>{if(route.request().method()!=='POST')return route.continue();const body=route.request().postDataJSON();expect(body.action).toBe('local_model');expect(body.initialize_head).toBe(false);expect(body.base_model).toBe('E:\\models\\classifier');expect(body.model_task).toBe('text-classification');await route.fulfill({status:400,json:{detail:'Synthetic local form reached server'}});});
 await panel.getByRole('button',{name:'检查连接并保存模型'}).click();await expect(panel.getByRole('alert')).toContainText('Synthetic local form reached server');await page.screenshot({path:'artifacts/model-form.png',fullPage:true,animations:'disabled'});
});
