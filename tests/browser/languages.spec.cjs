const {test,expect}=require('@playwright/test');
for(const language of ['en','zh-hans']){
 test(`login, application and account language: ${language}`,async({page,context})=>{
  await context.addCookies([{name:'django_language',value:language,url:'http://127.0.0.1:8080'}]);
  await page.addInitScript(lang=>localStorage.setItem('label-studio.language',lang==='en'?'en':'zh-CN'),language);
  await page.goto('/user/login/');
  await expect(page.locator('h2')).toHaveText(language==='en'?'Log in':'登录');
  await page.locator('#email').fill('ci@example.invalid');
  await page.locator('#password').fill('ci-only-synthetic-password');
  await page.locator('#login-form button[type=submit]').click();
  await expect(page).not.toHaveURL(/user\/login/);
  await page.goto('/user/account/language');
  await expect(page.getByTestId('language-selector')).toContainText(language==='en'?'English':'简体中文');
  await page.reload();
  await expect(page.getByTestId('language-selector')).toContainText(language==='en'?'English':'简体中文');
  await page.goto('/projects');
  const createProject = page.getByRole('button',{name:language==='en'?'Create new project':'创建新项目',exact:true});
  await expect(createProject).toBeVisible();
  await expect(createProject).toHaveText(language==='en'?'Create':'创建');
  await page.goto('/projects/1/data?task=1');
  await expect(page.getByRole('button',{name:language==='en'?'Submit current annotation':'提交当前标注',exact:true})).toBeVisible();
  await expect(page.getByText('Customer category',{exact:false}).first()).toBeVisible();
  await expect(page.getByText('Customer bank',{exact:false}).first()).toBeVisible();
  await expect(page.getByText('Acme Bank',{exact:false}).first()).toBeVisible();
 });
}
test('language switch persists across full reload and server-rendered login',async({page})=>{
 await page.goto('/user/login/');
 await page.locator('#email').fill('ci@example.invalid');await page.locator('#password').fill('ci-only-synthetic-password');
 await page.locator('#login-form button[type=submit]').click();await expect(page).not.toHaveURL(/user\/login/);
 await page.goto('/user/account/language');
 await page.getByTestId('language-selector').click();await page.locator('[cmdk-item]').filter({hasText:'简体中文'}).click();
 await expect(page.getByTestId('language-selector')).toContainText('简体中文');
 await page.reload();await expect(page.getByTestId('language-selector')).toContainText('简体中文');
 await page.goto('/logout/');await page.goto('/user/login/');
 await expect(page.locator('h2')).toHaveText('登录');
});
