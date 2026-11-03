/* Live acceptance on a new synthetic project. Session stays in memory; account preferences are unchanged. */
const fs = require('node:fs');
const path = require('node:path');
const {execFileSync} = require('node:child_process');
const {chromium, expect} = require('@playwright/test');
const zh = require('../catalog/zh-CN.json');
const t = key => zh[key] || key;
const root = path.resolve(__dirname, '..');
const base = 'http://127.0.0.1:8080';
const compose = ['compose', '-p', 'jianzhi-label-studio', '-f', path.resolve(root, '../compose.yaml')];
const header = "import os,sys,json\nsys.path.insert(0,'/label-studio/label_studio')\nos.environ.setdefault('DJANGO_SETTINGS_MODULE','core.settings.label_studio')\nimport django;django.setup()\n";
function django(code) {
  return execFileSync('docker', [...compose, 'exec', '-T', 'label-studio', 'python', '-'], {
    input:header+code, encoding:'utf8', timeout:90000, stdio:['pipe','pipe','pipe'],
  });
}
const auth = JSON.parse(django("from users.models import User\nfrom importlib import import_module\nfrom django.conf import settings\nu=User.objects.filter(is_active=True).order_by('id').first()\ns=import_module(settings.SESSION_ENGINE).SessionStore();s['_auth_user_id']=str(u.pk);s['_auth_user_backend']='django.contrib.auth.backends.ModelBackend';s['_auth_user_hash']=u.get_session_auth_hash();s['last_login']=__import__('time').time();s.set_expiry(3600);s.save()\nprint('AUTH:'+json.dumps({'key':s.session_key,'name':settings.SESSION_COOKIE_NAME}))").split('AUTH:').pop());
let browser;
(async()=>{
  try {
    browser = await chromium.launch({channel:'chrome'});
    const context = await browser.newContext({viewport:{width:1600,height:1000}});
    await context.request.get(base+'/user/login/');
    await context.addCookies([{name:auth.name,value:auth.key,url:base},{name:'django_language',value:'zh-hans',url:base}]);
    await context.addInitScript(()=>localStorage.setItem('label-studio.language','zh-CN'));
    const csrf = (await context.cookies()).find(c=>c.name==='csrftoken').value;
    async function api(url,data,method) {
      const response = await context.request.fetch(base+url,{method:method||(data?'POST':'GET'),headers:{'X-CSRFToken':csrf},...(data?{data}:{})});
      if(!response.ok())throw Error(`${response.status()} ${url}: ${(await response.text()).slice(0,200)}`);
      return response.json();
    }
    const baseline = await api('/api/current-user/hotkeys/');
    const project = await api('/api/projects/',{title:'快捷键适配验收',label_config:'<View><Text name="text" value="$content"/><Choices name="category" toName="text" required="true"><Choice value="yes"/><Choice value="no"/></Choices></View>'});
    const {task_ids:tasks} = await api(`/api/projects/${project.id}/import?return_task_ids=true`,[0,1,2].map(i=>({data:{content:`Hotkey acceptance ${i}`}})));
    await api(`/api/current-user/hotkeys/?project=${project.id}`,{custom_hotkeys:{'annotation:annotation:submit':{key:'ctrl+enter',active:true},'annotation:annotation:skip':{key:'ctrl+space',active:true}}},'PATCH');
    const page = await context.newPage(), errors=[];page.on('pageerror',error=>errors.push(error.message));
    await page.goto(base+'/user/account/hotkeys');await expect(page.getByTestId('hotkey-annotation:annotation:submit')).toBeVisible();
    await page.screenshot({path:path.join(root,'artifacts/hotkeys-settings-live.png'),fullPage:true});
    await page.goto(`${base}/projects/${project.id}/label`);await page.getByRole('button',{name:t('Get next task'),exact:true}).click();
    await expect(page.getByRole('button',{name:t('Submit and next'),exact:true})).toBeEnabled();
    await page.getByRole('checkbox',{name:/^yes/}).check();await page.locator('.labeling-workspace__task-heading').click();await page.keyboard.press('Control+Enter');
    await expect(page.locator('.labeling-workspace__task-heading')).toContainText(`#${tasks[1]}`);
    await expect(page.getByTestId('labeling-editor')).toHaveAttribute('aria-busy','false');await page.getByRole('checkbox',{name:/^no/}).check();await page.locator('.labeling-workspace__task-heading').click();await page.keyboard.press('Control+Space');
    await expect(page.locator('.labeling-workspace__task-heading')).toContainText(`#${tasks[2]}`);
    await expect(page.getByTestId('labeling-editor')).toHaveAttribute('aria-busy','false');
    const first = (await api(`/api/projects/${project.id}/labeling-workspace/?task=${tasks[0]}`)).task;
    const skipped = (await api(`/api/projects/${project.id}/labeling-workspace/?task=${tasks[1]}`)).task;
    expect(first.annotations).toHaveLength(1);expect(first.annotations[0].result[0].value.choices).toEqual(['yes']);
    expect(skipped.annotations).toHaveLength(0);expect(skipped.draft.result[0].value.choices).toEqual(['no']);
    expect(await api('/api/current-user/hotkeys/')).toEqual(baseline);expect(errors).toEqual([]);
    await page.screenshot({path:path.join(root,'artifacts/hotkeys-workspace-live.png'),fullPage:true});
    const report={project:project.id,tasks,submit:true,skip:true,draft_preserved:true,account_preferences_unchanged:true,page_errors:errors};
    fs.writeFileSync(path.join(root,'artifacts/hotkeys-live-acceptance.json'),JSON.stringify(report,null,2));
    console.log(JSON.stringify(report));
  } finally {
    if(browser)await browser.close();
    django(`from django.contrib.sessions.models import Session\nSession.objects.filter(session_key=${JSON.stringify(auth.key)}).delete()`);
  }
})().catch(error=>{console.error(error.stack);process.exitCode=1;});
