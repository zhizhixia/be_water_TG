/** 用独立Chrome验证本地原型，仅写入截图与验证记录。 */
'use strict';
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const {pathToFileURL} = require('node:url');
const {chromium} = require('D:/Hermes Agent/hermes-agent/node_modules/playwright');
const root = __dirname;
const shots = path.join(root,'screenshots');
fs.mkdirSync(shots,{recursive:true});
const report = [];

/** 在真实DOM中检查可见文字、对比度、控件和页面溢出。 */
async function inspect(page,variant,width,view) {
  await page.waitForTimeout(190);
  const result = await page.evaluate(() => {
    /** 提取浏览器计算后的颜色通道。 */
    function rgb(text){return (text.match(/[\d.]+/g)||[]).map(Number);}
    /** 按相对亮度公式换算颜色。 */
    function luminance(channels){return channels.slice(0,3).map(n=>n/255).map(n=>n<=.04045?n/12.92:((n+.055)/1.055)**2.4).reduce((sum,n,i)=>sum+n*[.2126,.7152,.0722][i],0);}
    /** 沿祖先寻找实际的不透明背景。 */
    function background(el){for(let p=el;p;p=p.parentElement){const c=rgb(getComputedStyle(p).backgroundColor);if(c.length===3||c[3]===1)return c;}return [255,255,255];}
    const issues=[],ratios=[];
    for(const el of document.querySelectorAll('body *')){
      if(!el.checkVisibility({checkOpacity:true,checkVisibilityCSS:true}))continue;
      const style=getComputedStyle(el),box=el.getBoundingClientRect();
      if(['BUTTON','SELECT','INPUT','TEXTAREA'].includes(el.tagName)&&el.type!=='checkbox'&&box.width>0){if(box.width<43.5||box.height<43.5)issues.push({kind:'触控目标',text:el.textContent.trim().slice(0,20)||el.getAttribute('aria-label'),width:box.width,height:box.height});}
      const text=[...el.childNodes].filter(n=>n.nodeType===Node.TEXT_NODE).map(n=>n.textContent).join('').trim();
      if(!text||['STYLE','SCRIPT','OPTION'].includes(el.tagName))continue;
      const font=parseFloat(style.fontSize);if(font<12)issues.push({kind:'字号',text:text.slice(0,30),font});
      const fg=rgb(style.color),bg=background(el);if(fg.length<3)continue;
      const a=luminance(fg),b=luminance(bg),ratio=(Math.max(a,b)+.05)/(Math.min(a,b)+.05);
      if(!el.disabled){ratios.push(ratio);if(ratio<4.5)issues.push({kind:'文字对比度',text:text.slice(0,30),ratio:Number(ratio.toFixed(2))});}
    }
    return {width:innerWidth,scrollWidth:document.documentElement.scrollWidth,minContrast:Number(Math.min(...ratios).toFixed(2)),issues};
  });
  assert.ok(result.scrollWidth<=width,`${variant}/${width}/${view} 横向溢出: ${result.scrollWidth}`);
  assert.deepEqual(result.issues,[],`${variant}/${width}/${view}: ${JSON.stringify(result.issues)}`);
  return {variant,width,view,...result};
}

/** 手机使用明确的页面选择框，桌面使用导航按钮。 */
async function chooseView(page,name){if(page.viewportSize().width<=760){await page.locator("#mobile-view").selectOption(name);}else{await page.locator(`.navigation [data-view="${name}"]`).click();}}

/** 检查一次完整的运行、校验与未知结果恢复演示。 */
async function interactions(page) {
  const nav = name => chooseView(page,name);
  await page.locator('[data-action="start"]').click();
  await page.keyboard.press('Escape');
  assert.equal(await page.locator('#engine-state').textContent(),'已停止');
  await page.locator('[data-action="start"]').click();
  await page.locator('#dialog-confirm').click();
  assert.match(await page.locator('#engine-state').textContent(),/运行中/);
  await page.locator('[data-action="pause"]').click();
  assert.match(await page.locator('#engine-state').textContent(),/已暂停/);
  await page.locator('[data-action="pause"]').click();
  assert.match(await page.locator('#engine-state').textContent(),/运行中/);
  await page.locator('[data-action="stop"]').click();
  await page.locator('#dialog-confirm').click();
  assert.equal(await page.locator('#engine-state').textContent(),'已停止');
  await nav('groups');
  const first=page.getByRole('switch').first();
  const before=await first.isChecked();await first.click();assert.notEqual(await first.isChecked(),before);
  await page.locator('#group-search').fill('打卡');
  assert.equal(await page.locator('.group-editor:visible').count(),1);
  await page.locator('#group-search').fill('');
  assert.equal(await page.locator('.group-editor:visible').count(),3);
  await nav('chat');
  await page.locator('[name="daily_limit"]').fill('0');
  await page.locator('#chat-form button[type="submit"]').click();
  assert.equal(await page.locator('#chat-form .form-error').isVisible(),true);
  await page.locator('[name="daily_limit"]').fill('45');
  await page.locator('#chat-form button[type="submit"]').click();
  assert.equal(await page.locator('#chat-form .form-error').isVisible(),false);
  await nav('content');
  await page.locator('#library-text').fill('');
  await page.locator('#library-form button[type="submit"]').click();
  assert.equal(await page.locator('#library-form .form-error').isVisible(),true);
  await page.locator('#library-text').fill('演示消息，逗号保留\n第二条演示消息');
  await page.locator('#library-form button[type="submit"]').click();
  assert.equal(await page.locator('#library-count').textContent(),'2 条');
  await nav('overview');
  await page.locator('#scenario').selectOption('unknown');
  assert.equal(await page.locator('#attention-unknown').isVisible(),true);
  await page.locator('[data-review]').click();await page.locator('#dialog-confirm').click();
  assert.equal(await page.locator('#attention-unknown').isVisible(),true);
  await page.locator('[data-recover]').click();await page.locator('#dialog-confirm').click();
  assert.equal(await page.locator('#dialog-error').isVisible(),true);
  await page.locator('#confirm-phrase').fill('确认恢复');await page.locator('#dialog-confirm').click();
  assert.equal(await page.locator('#attention-unknown').isVisible(),false);
  await page.locator('#scenario').selectOption('offline');
  assert.equal(await page.locator('[data-action="start"]').isDisabled(),true);
  assert.match(await page.locator('#connection-label').textContent(),/中断/);
  assert.equal(await page.locator('#engine-state').textContent(),'已停止');
  await page.locator('#scenario').selectOption('normal');
}

/** 对三个方向、两个视口、七个页面逐项断言并保存真实截图。 */
async function main(){
  const browser=await chromium.launch({channel:'chrome',headless:true});
  try {
    for(let variant=1;variant<=3;variant++){
      for(const viewport of [{width:1440,height:900},{width:375,height:812}]){
        const context=await browser.newContext({viewport,locale:'zh-CN',reducedMotion:'reduce'});
        const page=await context.newPage();
        const errors=[],requests=[];
        page.on('pageerror',error=>errors.push(error.message));
        page.on('console',message=>{if(message.type()==='error')errors.push(message.text());});
        await page.route(/^https?:/,route=>{requests.push(route.request().url());return route.abort();});
        await page.goto(pathToFileURL(path.join(root,`dir-${variant}.html`)).href);
        assert.equal(await page.locator('.navigation .nav-item').count(),7);
        if(viewport.width<=760){assert.equal(await page.locator('.navigation').isVisible(),false);assert.equal(await page.locator('#mobile-view').isVisible(),true);}
        for(const view of ['overview','groups','chat','lottery','content','audit','settings']){
          await chooseView(page,view);
          assert.equal(await page.locator(`[data-page="${view}"]`).isVisible(),true);
          report.push(await inspect(page,variant,viewport.width,view));
        }
        await chooseView(page,'overview');
        await interactions(page);
        await page.reload();
        await page.screenshot({path:path.join(shots,`dir-${variant}-${viewport.width}-viewport.png`)});
        await page.screenshot({path:path.join(shots,`dir-${variant}-${viewport.width}-full.png`),fullPage:true});
        await page.locator('#scenario').selectOption('unknown');
        report.push(await inspect(page,variant,viewport.width,'unknown'));
        await page.screenshot({path:path.join(shots,`dir-${variant}-${viewport.width}-unknown.png`),fullPage:true});
        assert.deepEqual(errors,[],'控制台/脚本错误');assert.deepEqual(requests,[],'原型意外发起网络请求');
        report.push({variant,width:viewport.width,interactions:'passed',consoleErrors:errors.length,networkRequests:requests.length});
        await context.close();
      }
    }
    const result={status:'passed',browser:browser.version(),variants:3,viewports:2,viewsPerVariant:7,records:report};
    fs.writeFileSync(path.join(root,'verification.json'),JSON.stringify(result,null,2),'utf8');
    console.log(JSON.stringify({status:result.status,variants:result.variants,viewports:result.viewports,viewsPerVariant:result.viewsPerVariant,layoutChecks:report.filter(row=>row.view).length,interactionRuns:report.filter(row=>row.interactions).length,networkRequests:0,consoleErrors:0},null,2));
  } finally {await browser.close();}
}
main().catch(error=>{fs.writeFileSync(path.join(root,'verification-failure.json'),JSON.stringify({error:String(error),records:report},null,2));console.error(error);process.exitCode=1;});
