const {test,expect}=require('@playwright/test');

test('configure local Transformers model and enable uncertainty sampling',async({page,context})=>{
  await context.addCookies([{name:'django_language',value:'zh-hans',url:process.env.LABEL_STUDIO_URL||'http://127.0.0.1:8080'}]);
  await page.addInitScript(()=>localStorage.setItem('label-studio.language','zh-CN'));
  await page.goto('/user/login/');
  await page.locator('#email').fill('ci@example.invalid');
  await page.locator('#password').fill('ci-only-synthetic-password');
  await page.locator('#login-form button[type=submit]').click();
  await expect(page).not.toHaveURL(/user\/login/);
  await page.goto('/projects/1/settings/');
  await expect(page.locator('input[name="sampling"]')).toHaveValue(/sampling$/);
  await page.getByText('不确定性采样',{exact:true}).click();
  await expect(page.locator('input[name="sampling"]')).toHaveValue('Uncertainty sampling');
  const saving=page.waitForResponse(r=>/\/api\/projects\/1\/?$/.test(new URL(r.url()).pathname)&&r.request().method()==='PATCH');
  await page.getByRole('button',{name:'保存常规设置',exact:true}).click();
  const saved=await saving;
  expect(saved.ok()).toBeTruthy();
  expect(saved.request().postDataJSON().sampling).toBe('Uncertainty sampling');
  expect((await saved.json()).sampling).toBe('Uncertainty sampling');
  await page.reload();
  await expect(page.locator('input[type="radio"][value="Uncertainty sampling"]')).toBeChecked();
  await page.goto('/projects/1/settings/ml-advanced');
  await page.getByRole('button',{name:'添加机器学习模型',exact:true}).click();
  await expect(page.locator('select[name="model_task"]')).toHaveValue('text-classification');
  await page.locator('input[name="title"]').fill('Local Transformers');
  await page.locator('input[name="url"]').fill('http://host.docker.internal:9092');
  await page.locator('input[name="base_model"]').fill('E:\\models\\classifier');
  await page.locator('input[name="model_control"]').fill('category');
  await page.locator('input[name="active_learning"]').check({force:true});
  await page.screenshot({path:'artifacts/active-learning-model.png',fullPage:true,animations:'disabled'});
  // Assert actual form serialization. Model protocol/database are covered separately.
  await page.route('**/api/ml',async route=>{
    if(route.request().method()!=='POST')return route.continue();
    const payload=route.request().postDataJSON();
    expect(payload.base_model).toBe('E:\\models\\classifier');
    expect(payload.model_task).toBe('text-classification');
    expect(payload.model_control).toBe('category');
    expect(payload.active_learning).toBe(true);
    await route.fulfill({status:200,json:{id:999,...payload}});
  });
  await page.getByRole('button',{name:'保存机器学习配置',exact:true}).click();
  await expect(page.locator('input[name="base_model"]')).toHaveCount(0);
});
