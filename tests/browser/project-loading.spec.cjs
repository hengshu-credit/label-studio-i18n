const {test,expect}=require('@playwright/test');
const zh=require('../../catalog/zh-CN.json'),t=k=>zh[k]||k;
const XML='<View><Text name="text" value="$content"/><Choices name="category" toName="text"><Choice value="yes"/></Choices></View>';
let csrf;
test.beforeEach(async({page,context})=>{await context.addCookies([{name:'django_language',value:'zh-hans',url:process.env.LABEL_STUDIO_URL||'http://127.0.0.1:8080'}]);await page.addInitScript(()=>localStorage.setItem('label-studio.language','zh-CN'));await page.goto('/user/login/');await page.locator('#email').fill('ci@example.invalid');await page.locator('#password').fill('ci-only-synthetic-password');await page.locator('#login-form button[type=submit]').click();await expect(page).not.toHaveURL(/user\/login/);csrf=await page.evaluate(()=>decodeURIComponent(document.cookie.split('; ').find(x=>x.startsWith('csrftoken=')).slice(10)));});
async function api(page,url,data){const r=await page.request.fetch(url,{method:data?'POST':'GET',headers:{'X-CSRFToken':csrf},...(data?{data}:{})});expect(r.ok(),await r.text()).toBeTruthy();return r.json();}
async function fixture(page){const p=await api(page,'/api/projects/',{title:'Project loading fixture',label_config:XML});await api(page,`/api/projects/${p.id}/import`,Array.from({length:120},(_,i)=>({data:{sample_id:`row-${i}`,content:`Sample ${i}`}})));return p;}

test('cold project load waits for auth once, shows progress and avoids queue scans in the explorer',async({page})=>{
 const p=await fixture(page);let release,releaseStats;const gate=new Promise(r=>release=r),statsGate=new Promise(r=>releaseStats=r);const bootstraps=[],states=[];
 await page.route('**/api/current-user/whoami',async route=>{await new Promise(r=>setTimeout(r,200));await route.continue();});
 await page.route(`**/api/projects/${p.id}*`,async route=>{const url=new URL(route.request().url());if(route.request().method()==='GET'&&new RegExp(`/api/projects/${p.id}/?$`).test(url.pathname)){bootstraps.push(url);await gate;}await route.continue();});
 await page.route('**/api/dm/project*',async route=>{if(!new URL(route.request().url()).searchParams.has('metadata_only'))await statsGate;await route.continue();});
 page.on('request',req=>{const url=new URL(req.url());if(url.pathname.replace(/\/$/,'')==='/api/dm/project')states.push(url);});
 try{await page.goto(`/projects/${p.id}/data`);await expect(page.getByTestId('project-loading')).toBeVisible();await expect.poll(()=>bootstraps.length).toBe(1);release();await expect(page.getByTestId('table-row').first()).toBeVisible();await expect(page.getByTestId('project-loading')).toHaveCount(0);expect(bootstraps).toHaveLength(1);expect(bootstraps[0].searchParams.get('metadata_only')).toBe('true');expect(states.some(u=>u.searchParams.get('lightweight')==='true')).toBeTruthy();expect(await page.getByTestId('table-row').count()).toBeLessThan(120);expect(states.some(u=>u.searchParams.get('metadata_only')==='true')).toBeTruthy();releaseStats();
  await page.getByRole('link',{name:t('Settings'),exact:true}).click();await expect(page.locator('input[name="title"]')).toHaveValue(p.title);await page.goBack();await expect(page.getByTestId('table-row').first()).toBeVisible();
 }finally{release();releaseStats();await page.unrouteAll({behavior:"wait"});}
});

test('project cards remain usable while their statistics are loading',async({page})=>{
 const p=await fixture(page);let release;const gate=new Promise(r=>release=r);
 await page.route('**/api/projects?*',async route=>{const url=new URL(route.request().url());if(url.searchParams.get('include')?.includes('task_number'))await gate;await route.continue();});
 try{await page.goto('/projects');const card=page.locator(`a[href="/projects/${p.id}/data"]`);await expect(card).toBeVisible();await expect(card.locator('[aria-busy="true"]')).toBeVisible();await expect(card).toContainText('— / —');await card.click();await expect(page.getByTestId('table-row').first()).toBeVisible();}finally{release();}
});

test('label stream still loads the exact user queue',async({page})=>{
 const p=await fixture(page);const response=page.waitForResponse(r=>new URL(r.url()).pathname.replace(/\/$/,'')==='/api/dm/project'&&r.request().method()==='GET');await page.goto(`/projects/${p.id}/data?labeling=1`);const r=await response;expect(r.ok()).toBeTruthy();const data=await r.json();expect(data.queue_total).toBe(120);expect(data.queue_done).toBe(0);await expect(page.getByRole('checkbox',{name:/^yes/})).toBeVisible();
});
