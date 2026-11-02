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

test('translation settings own engines and keys while the panel stays minimal',async({page,context})=>{
  await login(page,context);
  await page.goto('/user/account/translation');
  const settings=page.getByTestId('translation-settings');
  await expect(settings).toBeVisible();
  await expect(settings.getByLabel('翻译引擎')).toHaveValue('azure');
  await settings.getByLabel('API Key',{exact:true}).fill('synthetic-ui-key-only');
  await settings.getByLabel('Azure 区域').fill('eastasia');
  const saving=page.waitForResponse(r=>r.url().includes('/translation-options/')&&r.request().method()==='POST');
  await settings.getByRole('button',{name:'保存 API 设置',exact:true}).click();
  const response=await saving;
  expect(response.status()).toBe(200);expect(await response.text()).not.toContain('synthetic-ui-key-only');
  await expect(settings.getByLabel('API Key',{exact:true})).toHaveValue('');
  expect(await page.evaluate(()=>JSON.stringify(localStorage))).not.toContain('synthetic-ui-key-only');
  await page.screenshot({path:'artifacts/translation-settings.png',fullPage:true});
  await page.goto('/projects/1/data?task=1');
  await page.locator('[id="翻译-draggable"]').getByText('翻译',{exact:true}).click();
  const panel=page.getByTestId('translation-panel');
  await expect(panel).toBeVisible();
  await expect(panel).toHaveCSS('display','flex');
  await expect(panel.getByLabel('翻译引擎')).toHaveCount(0);
  await expect(panel.getByLabel('API Key',{exact:true})).toHaveCount(0);
  await expect(panel.locator('details')).toHaveCount(0);
  await expect(panel).not.toContainText('本地每月字符上限');
  await expect(panel.getByLabel('原文语言')).toHaveValue('auto');
  await expect(panel.getByLabel('目标语言')).toHaveValue('zh');
  await expect(panel.getByLabel('原文',{exact:true})).toHaveValue('Acme Bank transferred 2500 THB.');
  await expect(panel.getByRole('button',{name:'翻译当前文本',exact:true})).toBeEnabled();
  const before=await (await page.request.get('/api/tasks/1/')).json();
  await page.route('**/api/tasks/1/translate/',async route=>{
    expect(route.request().postDataJSON()).toEqual({engine:'azure',source:'auto',target:'zh',field:'content'});
    await route.fulfill({json:{task_id:1,translation:'合成银行已转账 2500 泰铢。',engine:'azure',detected_source:'en',target:'zh',cached:false}});
  });
  await panel.getByRole('button',{name:'翻译当前文本',exact:true}).click();
  await expect(panel.getByLabel('翻译结果')).toHaveValue('合成银行已转账 2500 泰铢。');
  await expect(page.getByText('Acme Bank',{exact:false}).first()).toBeVisible();
  await page.setViewportSize({width:1440,height:1080});
  await panel.getByLabel('原文语言').scrollIntoViewIfNeeded();
  await page.screenshot({path:'artifacts/translation-panel.png',fullPage:true});
  await panel.getByLabel('目标语言').selectOption('en');
  await expect(panel.getByLabel('翻译结果')).toHaveValue('');
  await panel.getByLabel('原文语言').selectOption('th');
  // Settings opened separately must update an already-mounted translation panel.
  const settingsPage=await context.newPage();
  await settingsPage.goto('/user/account/translation');
  const separate=settingsPage.getByTestId('translation-settings');
  await separate.getByLabel('翻译引擎').selectOption('google');
  await expect(panel.getByRole('button',{name:'翻译当前文本',exact:true})).toBeDisabled();
  await expect(panel.getByRole('link',{name:'前往设置配置翻译'})).toHaveAttribute('href','/user/account/translation');
  await separate.getByLabel('翻译引擎').selectOption('baidu');
  await expect(separate.getByLabel('App ID',{exact:true})).toBeVisible();
  await expect(separate.getByLabel('密钥',{exact:true})).toBeVisible();
  await expect(separate.getByLabel('API Key',{exact:true})).toHaveCount(0);
  await settingsPage.reload();
  await expect(separate.getByLabel('翻译引擎')).toHaveValue('baidu');
  await page.reload();
  await expect(panel.getByLabel('原文语言')).toHaveValue('th');
  await expect(panel.getByLabel('目标语言')).toHaveValue('en');
  // Removing a key in settings must disable translation without exposing credentials.
  await separate.getByLabel('翻译引擎').selectOption('azure');
  await expect(panel.getByRole('button',{name:'翻译当前文本',exact:true})).toBeEnabled();
  await separate.getByRole('button',{name:'移除已保存密钥',exact:true}).click();
  await expect(separate.getByRole('button',{name:'移除已保存密钥',exact:true})).toHaveCount(0);
  await expect(panel.getByRole('button',{name:'翻译当前文本',exact:true})).toBeDisabled();
  const after=await (await page.request.get('/api/tasks/1/')).json();
  expect(after.data).toEqual(before.data);expect(after.annotations).toEqual(before.annotations);
  await settingsPage.close();
});

test('translation settings keep API controls owner-only and support English',async({page,context})=>{
  await login(page,context);
  await page.addInitScript(()=>localStorage.setItem('label-studio.language','en'));
  await page.route('**/api/tasks/translation-options/',async route=>{
    const response=await route.fetch();
    await route.fulfill({json:{...await response.json(),can_configure:false}});
  });
  await page.goto('/user/account/translation');
  const settings=page.getByTestId('translation-settings');
  await expect(settings.getByLabel('Translation engine')).toBeEnabled();
  await expect(settings.getByLabel('API Key',{exact:true})).toHaveCount(0);
  await expect(settings.getByRole('button',{name:'Save API settings',exact:true})).toHaveCount(0);
  await expect(settings.getByRole('status')).toHaveText('Ask the organization owner to configure this translation engine.');
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
