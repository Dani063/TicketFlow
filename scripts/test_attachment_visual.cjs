/** Visual QA for the local attachment demo. Uses the same explicit environment
 * as test_attachment_browser.cjs and writes screenshots to tmp, not production.
 */
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const base = process.env.ATTACHMENT_QA_URL || 'http://127.0.0.1:8000';
assert(['localhost','127.0.0.1'].includes(new URL(base).hostname));
const email = process.env.ATTACHMENT_QA_EMAIL;
const ticket = process.env.ATTACHMENT_QA_TICKET;
assert(email && /^\d+$/.test(ticket || ''));
const output = process.env.ATTACHMENT_QA_OUTPUT || path.join(__dirname,'../tmp/attachment-browser');
fs.mkdirSync(output,{recursive:true});
(async()=>{
  const browser = await chromium.launch({channel:'chrome',headless:true});
  try {
    const ctx = await browser.newContext({baseURL:base,viewport:{width:1540,height:1000}});
    const page = await ctx.newPage();
    const errors=[];
    page.on('pageerror',error=>errors.push(error.message));
    const login=await ctx.request.get('/dev-login/');
    const csrf=(await login.text()).match(/name="csrfmiddlewaretoken" value="([^"]+)"/)[1];
    assert((await ctx.request.post('/dev-login/',{form:{csrfmiddlewaretoken:csrf,email}})).ok());
    await page.goto('/tickets/create/?id='+ticket);
    await page.locator('.tf-att-card').first().waitFor();
    const card = name=>page.locator('#messagesBox .tf-att-card').filter({hasText:name});
    const dialog = ()=>page.locator('dialog[open]');
    async function preview(name) {
      const opener=card(name).getByRole('button',{name:'Vista previa de '+name,exact:true}).first();
      await opener.click();
      await dialog().waitFor();
      await dialog().locator('.tf-att-preview-body[aria-busy="false"]').waitFor();
      return opener;
    }
    for (const theme of ['light','dark']) {
      await page.evaluate(theme=>document.documentElement.dataset.theme=theme,theme);
      for (const width of [1540,1024,760,390]) {
        await page.setViewportSize({width,height:1000});
        await card('Captura de soporte 1.png').scrollIntoViewIfNeeded();
        await card('Captura de soporte 1.png').locator('img').evaluate(img=>img.decode());
        await page.screenshot({path:path.join(output,'cards-'+theme+'-'+width+'.png')});
        const geometry=await page.locator('.tf-att-card').evaluateAll(cards=>cards.map(c=>({width:c.clientWidth,scroll:c.scrollWidth})));
        assert(geometry.every(c=>c.width>0 && c.scroll<=c.width+1),'Overflow at '+width+' '+theme);
        const opener=await preview('Captura de soporte 1.png');
        await page.waitForFunction(()=>{const img=document.querySelector('dialog[open] .tf-att-preview-image');return img?.complete && img.naturalWidth>0;});
        const bounds=await dialog().boundingBox();
        assert(bounds.x>=0 && bounds.x+bounds.width<=width+1 && bounds.y>=0 && bounds.y+bounds.height<=1001);
        await dialog().getByRole('button',{name:'Acercar',exact:true}).click();
        assert((await dialog().innerText()).includes('125 %'));
        await dialog().getByRole('button',{name:'Siguiente',exact:true}).click();
        assert((await dialog().innerText()).includes('Captura de soporte 2'));
        await dialog().locator('.tf-att-preview-body[aria-busy="false"]').waitFor();
        await page.waitForFunction(()=>{const img=document.querySelector('dialog[open] .tf-att-preview-image');return img?.complete && img.naturalWidth>0;});
        await page.screenshot({path:path.join(output,'image-'+theme+'-'+width+'.png')});
        await page.keyboard.press('Escape');
        assert(await opener.evaluate(el=>el===document.activeElement),'Keyboard focus restored');
      }
    }
    await page.setViewportSize({width:1540,height:1000});
    for (const name of ['Guía de comprobación.pdf','test_numerosYO.csv','Registro de diagnóstico con un nombre largo para comprobar que la extensión permanece visible.log']) {
      await preview(name);
      if (name.endsWith('.pdf')) {
        await dialog().locator('canvas.tf-att-pdf-canvas').waitFor();
        const pixels=await dialog().locator('canvas').evaluate(canvas=>{
          const data=canvas.getContext('2d').getImageData(0,0,canvas.width,canvas.height).data;
          let ink=0;for(let i=0;i<data.length;i+=4) if(data[i]<160 && data[i+1]<160 && data[i+2]<160 && data[i+3]>0) ink++;
          return ink;
        });
        assert(pixels>100,'PDF actually renders ink, not an empty canvas');
        assert((await dialog().innerText()).includes('Página 1 de 1'));
        assert(await dialog().getByRole('button',{name:'Página siguiente',exact:true}).isDisabled());
      } else if(name.endsWith('.csv')) {
        assert.equal(await dialog().locator('tr').count(),3);
        assert((await dialog().innerText()).includes('Prueba A'));
      } else assert((await dialog().locator('pre').innerText()).includes('[INFO]'));
      await page.screenshot({path:path.join(output,'preview-'+name.split('.').pop()+'.png')});
      await page.keyboard.press('Escape');
    }
    assert.equal(await card('Evidencias de soporte.zip').getByRole('button',{name:/Vista previa/}).count(),0);
    assert.equal(await card('Evidencias de soporte.zip').getByRole('link',{name:/Descargar/}).count(),1);
    assert.deepEqual(errors,[]);
    console.log('PASS screenshots light/dark at 1540/1024/760/390, gallery, zoom, focus/Escape, PDF pixels, CSV, log and ZIP download');
  } finally {await browser.close();}
})().catch(error=>{console.error(error.stack);process.exitCode=1;});
