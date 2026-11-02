const {test,expect}=require('@playwright/test');

test('Youdao credentials stay in settings and translate through the minimal panel',async({page,context})=>{
  await context.addCookies([{name:'django_language',value:'zh-hans',url:process.env.LABEL_STUDIO_URL||'http://127.0.0.1:8080'}]);
  await page.addInitScript(()=>localStorage.setItem('label-studio.language','zh-CN'));
  await page.goto('/user/login/');
  await page.locator('#email').fill('ci@example.invalid');
  await page.locator('#password').fill('ci-only-synthetic-password');
  await page.locator('#login-form button[type=submit]').click();
  await expect(page).not.toHaveURL(/user\/login/);
  await page.goto('/user/account/translation');
  const settings=page.getByTestId('translation-settings');
  await settings.getByLabel('翻译引擎').selectOption('youdao');
  await expect(settings.getByLabel('翻译引擎').locator('option:checked')).toContainText('有道文本翻译');
  const appId=settings.getByLabel('应用 ID（appKey）',{exact:true});
  const secret=settings.getByLabel('应用密钥',{exact:true});
  await expect(secret).toHaveAttribute('type','password');
  await expect(settings.getByLabel('本地每月字符上限')).toHaveValue('500000');
  await expect(settings.getByLabel('API Key',{exact:true})).toHaveCount(0);
  await appId.fill('synthetic-youdao-id');
  await secret.fill('synthetic-youdao-secret');
  async function save(){
    const saved=page.waitForResponse(r=>r.url().includes('/translation-options/')&&r.request().method()==='POST');
    await settings.getByRole('button',{name:'保存 API 设置',exact:true}).click();
    const response=await saved;
    expect(response.ok()).toBeTruthy();
    expect(await response.text()).not.toContain('synthetic-');
  }
  await save();
  await expect(appId).toHaveValue('');
  await expect(secret).toHaveValue('');
  await page.reload();
  await expect(settings.getByLabel('翻译引擎')).toHaveValue('youdao');
  await expect(appId).toHaveValue('');
  await expect(secret).toHaveValue('');
  // Blank credentials retain their saved values when only the local cap changes.
  await settings.getByLabel('本地每月字符上限').fill('100000');
  await save();
  await expect(settings.getByRole('button',{name:'移除已保存密钥',exact:true})).toBeVisible();
  expect(await page.evaluate(()=>JSON.stringify(localStorage))).not.toContain('synthetic-');
  await page.setViewportSize({width:1440,height:1080});
  await page.screenshot({path:'artifacts/youdao-settings.png',fullPage:true});
  const before=await (await page.request.get('/api/tasks/1/')).json();
  await page.goto('/projects/1/data?task=1');
  await page.locator('[id="翻译-draggable"]').getByText('翻译',{exact:true}).click();
  const panel=page.getByTestId('translation-panel');
  await expect(panel.getByLabel('翻译引擎')).toHaveCount(0);
  await expect(panel.getByLabel('应用密钥',{exact:true})).toHaveCount(0);
  await expect(panel.getByLabel('原文',{exact:true})).toHaveValue(before.data.content);
  await panel.getByLabel('目标语言').selectOption('zh-TW');
  await page.route('**/api/tasks/1/translate/',async route=>{
    const payload=route.request().postDataJSON();
    expect(payload.engine).toBe('youdao');
    expect(payload.source).toBe('auto');
    expect(payload.field).toBe('content');
    if(payload.target==='en'){
      await route.fulfill({status:503,json:{detail:'Translation credentials or API permissions are invalid.'}});
    }else{
      expect(payload.target).toBe('zh-TW');
      await route.fulfill({json:{task_id:1,translation:'有道合成譯文',engine:'youdao',detected_source:'en',target:'zh-TW',cached:false}});
    }
  });
  await panel.getByRole('button',{name:'翻译当前文本',exact:true}).click();
  await expect(panel.getByLabel('翻译结果')).toHaveValue('有道合成譯文');
  await page.screenshot({path:'artifacts/youdao-panel.png',fullPage:true});
  await panel.getByLabel('目标语言').selectOption('en');
  await expect(panel.getByLabel('翻译结果')).toHaveValue('');
  await panel.getByRole('button',{name:'翻译当前文本',exact:true}).click();
  await expect(panel.getByRole('alert')).toHaveText(require('../../catalog/zh-CN.json')['Translation credentials or API permissions are invalid.']);
  const after=await (await page.request.get('/api/tasks/1/')).json();
  expect(after.data).toEqual(before.data);expect(after.annotations).toEqual(before.annotations);
  await page.goto('/user/account/translation');
  const clearing=page.waitForResponse(r=>r.url().includes('/translation-options/')&&r.request().method()==='POST');
  await settings.getByRole('button',{name:'移除已保存密钥',exact:true}).click();
  expect((await clearing).ok()).toBeTruthy();
  await expect(settings.getByRole('button',{name:'移除已保存密钥',exact:true})).toHaveCount(0);
  await page.goto('/projects/1/data?task=1');
  await expect(panel.getByRole('button',{name:'翻译当前文本',exact:true})).toBeDisabled();
  await expect(panel.getByRole('link',{name:'前往设置配置翻译'})).toBeVisible();
});
