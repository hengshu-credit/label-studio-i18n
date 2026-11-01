const {test,expect}=require('@playwright/test');

async function login(page,context){
  await context.addCookies([{name:'django_language',value:'zh-hans',url:process.env.LABEL_STUDIO_URL||'http://127.0.0.1:8080'}]);
  await page.addInitScript(()=>localStorage.setItem('label-studio.language','zh-CN'));
  await page.goto('/user/login/');
  await page.locator('#email').fill('ci@example.invalid');
  await page.locator('#password').fill('ci-only-synthetic-password');
  await page.locator('#login-form button[type=submit]').click();
  await expect(page).not.toHaveURL(/user\/login/);
}

test('translation defaults, credentials, engine/language switching and reference result',async({page,context})=>{
  await login(page,context);
  await page.goto('/projects/1/data?task=1');
  // The editor also renders a hidden drag ghost with the same tab text.
  await page.locator('[id="翻译-draggable"]').getByText('翻译',{exact:true}).click();
  const panel=page.getByTestId('translation-panel');
  await expect(panel).toBeVisible();
  await expect(panel).toHaveCSS('display','flex');
  await expect(panel.getByLabel('翻译引擎')).toHaveValue('azure');
  await expect(panel.getByLabel('原文语言')).toHaveValue('auto');
  await expect(panel.getByLabel('目标语言')).toHaveValue('zh');
  await expect(panel.getByRole('button',{name:'翻译当前文本',exact:true})).toBeDisabled();
  await panel.locator('summary').click();
  await panel.getByLabel('API Key',{exact:true}).fill('synthetic-ui-key-only');
  await panel.getByLabel('Azure 区域').fill('eastasia');
  const saving=page.waitForResponse(r=>r.url().includes('/translation-options/')&&r.request().method()==='POST');
  await panel.getByRole('button',{name:'保存 API 设置',exact:true}).click();
  const response=await saving;
  expect(response.status()).toBe(200);expect(await response.text()).not.toContain('synthetic-ui-key-only');
  await expect(panel.getByLabel('API Key',{exact:true})).toHaveValue('');
  await expect(panel.getByRole('button',{name:'翻译当前文本',exact:true})).toBeEnabled();
  const before=await (await page.request.get('/api/tasks/1/')).json();
  await page.route('**/api/tasks/1/translate/',async route=>{
    expect(route.request().postDataJSON()).toEqual({engine:'azure',source:'auto',target:'zh'});
    await route.fulfill({json:{task_id:1,translation:'合成银行已转账 2500 泰铢。',engine:'azure',detected_source:'en',target:'zh',cached:false}});
  });
  await panel.getByRole('button',{name:'翻译当前文本',exact:true}).click();
  await expect(panel.getByLabel('翻译结果')).toHaveValue('合成银行已转账 2500 泰铢。');
  await expect(page.getByText('Acme Bank',{exact:false}).first()).toBeVisible();
  await panel.getByLabel('目标语言').selectOption('en');
  await expect(panel.getByLabel('翻译结果')).toHaveCount(0);
  await panel.getByLabel('原文语言').selectOption('th');
  await panel.getByLabel('翻译引擎').selectOption('google');
  await expect(panel.getByLabel('原文语言')).toHaveValue('th');
  await expect(panel.getByLabel('目标语言')).toHaveValue('en');
  await expect(panel.getByLabel('API Key',{exact:true})).toHaveValue('');
  await panel.getByLabel('翻译引擎').selectOption('baidu');
  await expect(panel.getByLabel('App ID',{exact:true})).toBeVisible();
  await expect(panel.getByLabel('密钥',{exact:true})).toBeVisible();
  await page.reload();
  await expect(panel.getByLabel('翻译引擎')).toHaveValue('baidu');
  await expect(panel.getByLabel('原文语言')).toHaveValue('th');
  await expect(panel.getByLabel('目标语言')).toHaveValue('en');
  const after=await (await page.request.get('/api/tasks/1/')).json();
  expect(after.data).toEqual(before.data);expect(after.annotations).toEqual(before.annotations);
  await page.screenshot({path:'artifacts/translation-panel.png',fullPage:true});
});

test('selected live model survives save and reload',async({page,context})=>{
  await login(page,context);
  await page.goto('/projects/1/settings/annotation');
  const selector=page.getByTestId('prelabel-model-selector');
  await expect(selector).toBeEnabled();
  await expect(selector.locator('option')).toContainText(['请选择模型或预测','CI Classification','CI Entities','synthetic-ci']);
  await selector.selectOption('CI Entities');
  let saved=page.waitForResponse(r=>/\/api\/projects\/1\/?$/.test(new URL(r.url()).pathname)&&r.request().method()==='PATCH');
  await page.getByRole('button',{name:'保存标注设置',exact:true}).click();
  expect((await saved).ok()).toBeTruthy();
  await page.reload();
  await expect(selector).toHaveValue('CI Entities');
  await page.getByRole('button',{name:'刷新模型',exact:true}).click();
  await expect(selector).toBeEnabled();await expect(selector).toHaveValue('CI Entities');
  await page.screenshot({path:'artifacts/model-selection.png',fullPage:true});
  // Restore the stored fixture version before another test opens its task.
  await selector.selectOption('synthetic-ci');
  saved=page.waitForResponse(r=>/\/api\/projects\/1\/?$/.test(new URL(r.url()).pathname)&&r.request().method()==='PATCH');
  await page.getByRole('button',{name:'保存标注设置',exact:true}).click();expect((await saved).ok()).toBeTruthy();
});
