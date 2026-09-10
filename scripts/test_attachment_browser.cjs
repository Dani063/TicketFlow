/** Local-only browser regression. Requires Playwright, a DEBUG SQLite instance,
 * a dev-login account and a seeded ticket. Never runs against DEV/STA/PRO.
 * ATTACHMENT_QA_EMAIL and ATTACHMENT_QA_TICKET must be set explicitly.
 * Tests create clearly labelled local comments/tickets; no existing data deleted.
 */
const { chromium } = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const base = process.env.ATTACHMENT_QA_URL || 'http://127.0.0.1:8000';
assert(['localhost', '127.0.0.1'].includes(new URL(base).hostname), 'Local instance only');
const email = process.env.ATTACHMENT_QA_EMAIL;
const ticket = process.env.ATTACHMENT_QA_TICKET;
assert(email && /^\d+$/.test(ticket || ''), 'Set ATTACHMENT_QA_EMAIL and ATTACHMENT_QA_TICKET');
const output = process.env.ATTACHMENT_QA_OUTPUT || path.join(__dirname, '../tmp/attachment-browser');
fs.mkdirSync(output, { recursive: true });
const run = Date.now();
const file = (name, content = 'Prueba local de adjuntos\n', mimeType = 'text/plain') => ({name, mimeType, buffer:Buffer.from(content)});

(async () => {
  const browser = await chromium.launch({channel:'chrome', headless:true});
  let page;
  try {
    const ctx = await browser.newContext({baseURL:base, viewport:{width:1540,height:1000}, acceptDownloads:true});
    page = await ctx.newPage();
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    page.on('console', message => { if(message.type()==='error') console.log('BROWSER ERROR',message.text()); });
    page.on('response', async response => {
      if (response.status() >= 400 && /attachments|add_comment/.test(response.url())) console.log('HTTP', response.status(), response.url(), (await response.text()).slice(0,400));
    });
    page.on('dialog', dialog => dialog.accept());
    const login = await ctx.request.get('/dev-login/');
    const csrf = (await login.text()).match(/name="csrfmiddlewaretoken" value="([^"]+)"/)[1];
    assert((await ctx.request.post('/dev-login/', {form:{csrfmiddlewaretoken:csrf,email}})).ok());
    const pane = () => page.locator('.ticket-pane:visible');
    const queue = () => pane().locator('#pending-attachments');
    async function open(id=ticket) {
      await page.goto('/tickets/create/?id=' + id);
      await pane().locator('.ql-editor').waitFor();
    }
    async function ready(count=1) {
      await page.waitForFunction(n => {
        const root = [...document.querySelectorAll('.ticket-pane')].find(el => el.getClientRects().length);
        const cards = root?.querySelectorAll('#pending-attachments .tf-att-card');
        return cards?.length === n && [...cards].every(el => el.dataset.state === 'uploaded');
      }, count);
    }
    async function removeAll() {
      while (await queue().locator('.tf-att-card').count()) {
        const card = queue().locator('.tf-att-card').first();
        const key = await card.getAttribute('data-upload-key');
        await card.locator('[data-action="remove"]').click();
        await queue().locator('[data-upload-key="'+key+'"]').waitFor({state:'detached'});
      }
    }
    async function publish() {
      const response = page.waitForResponse(r => /\/add_comment\/$/.test(new URL(r.url()).pathname) && r.request().method()==='POST');
      await pane().locator('#publish-button').click();
      const result = await response;
      assert(result.ok(), await result.text());
      const data = await result.json();
      await pane().locator('[data-comment-id="'+data.id+'"]').waitFor();
      await page.waitForFunction(() => !document.querySelector('.ticket-pane[aria-busy="true"]'));
      return data;
    }
    await open();

    // Existing ticket, files-only, Unicode name, download bytes and reload.
    const name = 'Comprobación visual ñ ' + run + '.txt';
    const payload = file(name);
    await pane().locator('#attach-input').setInputFiles(payload);
    await ready();
    const onlyFiles = await publish();
    assert.equal(onlyFiles.attachments[0].filename, name);
    assert.equal(await queue().locator('.tf-att-card').count(), 0);
    assert.equal((await pane().locator('.ql-editor').innerText()).trim(), '');
    const downloadResponse = await ctx.request.get(onlyFiles.attachments[0].download_url);
    assert(downloadResponse.ok());
    assert((await downloadResponse.body()).equals(payload.buffer));
    const downloadEvent = page.waitForEvent('download');
    await pane().locator('[data-comment-id="'+onlyFiles.id+'"]').getByRole('link',{name:/Descargar/}).click();
    assert.equal((await downloadEvent).suggestedFilename(), name);
    await page.reload();
    await pane().locator('.tf-att-card').filter({hasText:name}).waitFor();
    console.log('PASS files-only, Unicode, download, reload');

    // Upload error must preserve text and block publishing; retry uses same key.
    let failed = false;
    await page.route('**/tickets/*/attachments/upload/', async route => {
      if (!failed) { failed=true; await route.fulfill({status:502,body:'Temporary QA failure'}); }
      else await route.continue();
    });
    await pane().locator('.ql-editor').fill('Borrador conservado ' + run);
    await pane().locator('#attach-input').setInputFiles(file('Reintento '+run+'.txt'));
    await queue().locator('[data-state="error"]').waitFor();
    const uploadKey = await queue().locator('.tf-att-card').getAttribute('data-upload-key');
    const before = await pane().locator('#messagesBox .message').count();
    await pane().locator('#publish-button').click();
    assert.equal(await pane().locator('#messagesBox .message').count(), before);
    assert((await pane().locator('.ql-editor').innerText()).includes('Borrador conservado'));
    await queue().getByRole('button',{name:'Reintentar',exact:true}).click();
    await ready();
    assert.equal(await queue().locator('.tf-att-card').getAttribute('data-upload-key'),uploadKey);
    await page.unroute('**/tickets/*/attachments/upload/');
    await publish();
    assert.equal((await pane().locator('.ql-editor').innerText()).trim(),'');
    console.log('PASS upload failure, preserved draft, retry, editor reset');

    // Response lost AFTER commit: identical request key replays one comment.
    await pane().locator('.ql-editor').fill('Publicación idempotente '+run);
    let committed;
    await page.route('**/add_comment/', async route => {
      const response = await route.fetch();
      committed = await response.json();
      await route.abort('failed');
    }, {times:1});
    await pane().locator('#publish-button').click();
    await page.waitForFunction(() => !document.querySelector('.ticket-pane[aria-busy="true"]'));
    assert(committed?.id);
    assert((await pane().locator('.ql-editor').innerText()).includes('idempotente'));
    const replay = await publish();
    assert.equal(replay.id, committed.id);
    await page.reload();
    assert.equal(await pane().locator('#messagesBox .message').filter({hasText:'Publicación idempotente '+run}).count(),1);
    console.log('PASS lost publication response, no duplicate');

    // Input limit validation and pending removal, no accidental published delete.
    await pane().locator('#attach-input').setInputFiles(file('no-permitido.exe'));
    assert.equal(await queue().locator('.tf-att-card').count(),0);
    assert((await queue().innerText()).includes('no permitido'));
    await pane().locator('#attach-input').setInputFiles({name:'demasiado.txt',mimeType:'text/plain',buffer:Buffer.alloc(15*1024*1024+1)});
    assert.equal(await queue().locator('.tf-att-card').count(),0);
    assert((await queue().innerText()).includes('máximo'));
    await pane().locator('#attach-input').setInputFiles(Array.from({length:6},(_,i)=>file('Límite '+run+' '+i+'.txt')));
    await ready(5);
    assert((await queue().innerText()).includes('hasta 5'));
    await removeAll();
    assert((await ctx.request.get(onlyFiles.attachments[0].download_url)).ok());
    console.log('PASS file limits, pending removal, published file retained');

    // The server accepted the bytes, but the upload response has not arrived:
    // cancel by upload key must still remove the private pending object.
    let delayedAttachment, releaseResponse;
    const responseGate = new Promise(resolve => { releaseResponse=resolve; });
    let uploadedToServer;
    const serverGate = new Promise(resolve => { uploadedToServer=resolve; });
    await page.route('**/tickets/*/attachments/upload/', async route => {
      const response = await route.fetch();
      delayedAttachment = await response.json();
      uploadedToServer();
      await responseGate;
      await route.fulfill({response}).catch(()=>{});
    }, {times:1});
    await pane().locator('#attach-input').setInputFiles(file('Cancelación en curso '+run+'.txt'));
    await serverGate;
    await queue().locator('progress').waitFor();
    const cancellingKey = await queue().locator('.tf-att-card').getAttribute('data-upload-key');
    await queue().getByRole('button',{name:/^Cancelar:/}).click();
    await queue().locator('[data-upload-key="'+cancellingKey+'"]').waitFor({state:'detached'});
    releaseResponse();
    assert.equal((await ctx.request.get(delayedAttachment.download_url)).status(),404);
    console.log('PASS cancel during processing; no ghost attachment');

    // Internal text-only reply and rapid double publication do not regress.
    await pane().locator('#visBtn').click();
    await pane().locator('.vis-option[data-public="false"]').click();
    await pane().locator('.ql-editor').fill('Nota interna sin duplicados '+run);
    const internalResponse = page.waitForResponse(r=>/\/add_comment\/$/.test(new URL(r.url()).pathname));
    await pane().locator('#publish-button').evaluate(button=>{button.click();button.click();});
    const internal = await (await internalResponse).json();
    assert.equal(internal.is_public,false);
    await pane().locator('[data-comment-id="'+internal.id+'"].internal').waitFor();
    await page.reload();
    assert.equal(await pane().locator('#messagesBox .message').filter({hasText:'Nota interna sin duplicados '+run}).count(),1);
    console.log('PASS internal text-only and double-click protection');

    const previousPriority=await pane().locator('#prioridad').inputValue();
    const countBeforeMetadata=await pane().locator('#messagesBox .message').count();
    for(const priority of [previousPriority==='high'?'normal':'high',previousPriority]) {
      await pane().locator('#prioridad').selectOption(priority);
      const saved=page.waitForResponse(r=>new URL(r.url()).pathname==='/tickets/create/' && r.request().method()==='POST');
      await pane().locator('#publish-button').click();
      assert((await saved).ok());
      await page.reload();
      await pane().locator('.ql-editor').waitFor();
      assert.equal(await pane().locator('#prioridad').inputValue(),priority);
      assert.equal(await pane().locator('#messagesBox .message').count(),countBeforeMetadata);
    }
    console.log('PASS metadata-only update without an empty comment');

    // Real Quill target receives paste/drop files, not Base64 embedded images.
    await pane().locator('.ql-editor').evaluate(editor => {
      const data = new DataTransfer();
      data.items.add(new File(['pegado'],'pegado local.txt',{type:'text/plain'}));
      editor.dispatchEvent(new ClipboardEvent('paste',{clipboardData:data,bubbles:true,cancelable:true}));
    });
    await ready();
    await pane().locator('.ql-editor').evaluate(editor => {
      const data = new DataTransfer();
      data.items.add(new File(['drop'],'arrastrado local.txt',{type:'text/plain'}));
      editor.dispatchEvent(new DragEvent('drop',{dataTransfer:data,bubbles:true,cancelable:true}));
    });
    await ready(2);
    assert.equal(await pane().locator('.ql-editor img').count(),0);
    await page.screenshot({path:path.join(output,'queue-light.png')});
    await removeAll();
    console.log('PASS paste/drop into Quill');

    const imageCard=pane().locator('#messagesBox .tf-att-card').filter({hasText:'Captura de soporte 1.png'});
    const imageUrl=await imageCard.getByRole('link',{name:/Descargar/}).getAttribute('href');
    const imageBytes=await (await ctx.request.get(imageUrl)).body();
    await pane().locator('#attach-input').setInputFiles(Array.from({length:5},(_,i)=>({name:'Cola de imágenes '+i+'.png',mimeType:'image/png',buffer:imageBytes})));
    await ready(5);
    for(const viewport of [{width:1024,height:768},{width:390,height:844}]) {
      await page.setViewportSize(viewport);
      // Existing responsive side panels animate their width on breakpoint changes.
      await page.waitForTimeout(450);
      const bounds=await pane().locator('#send-message-btn').boundingBox();
      console.log('five images geometry',viewport,bounds,await queue().boundingBox());
      await page.screenshot({path:path.join(output,'five-images-'+viewport.width+'.png')});
      assert(bounds && bounds.y>=0 && bounds.y+bounds.height<=viewport.height,'Send must remain in the viewport with five attachments');
      assert(await pane().locator('#send-message-btn').evaluate(button=>{
        const b=button.getBoundingClientRect();
        const hit=document.elementFromPoint(b.x+b.width/2,b.bottom-1);
        return hit===button || button.contains(hit);
      }),'Send must not be covered or clipped by the footer');
      const queueBounds=await queue().boundingBox();
      assert(queueBounds.height>0 && queueBounds.height<=320,'Attachment queue must be visible and bounded');
    }
    await removeAll();
    await page.setViewportSize({width:1540,height:1000});
    console.log('PASS five image attachments in tablet/mobile composer');

    // Draft upload before ticket exists; first message is files-only.
    await page.goto('/tickets/create/');
    await pane().locator('.ql-editor').waitFor();
    await pane().locator('#producto').selectOption({index:1});
    await pane().locator('#empresa').selectOption({index:1});
    await pane().locator('#subject').fill('QA local · Primer mensaje con adjuntos '+run);
    const firstName = 'Primer mensaje '+run+'.csv';
    await pane().locator('#attach-input').setInputFiles(file(firstName,'columna,valor\nprueba,1\n','text/csv'));
    await ready();
    const firstCreate = page.waitForResponse(r=>new URL(r.url()).pathname==='/tickets/create/' && r.request().method()==='POST');
    await pane().locator('#publish-button').click();
    const createdResponse = await firstCreate;
    assert(createdResponse.ok(),await createdResponse.text());
    const created = await createdResponse.json();
    assert(created.ticket_id);
    await page.waitForURL('**id='+created.ticket_id);
    await pane().locator('#messagesBox .tf-att-card').filter({hasText:firstName}).waitFor();
    console.log('PASS first ticket message files-only; created local ticket',created.ticket_id);

    // Switching panes keeps queues private. Cancelling a close keeps draft;
    // accepting waits for server removal before destroying only that pane.
    await pane().locator('#attach-input').setInputFiles(file('Panel A '+run+'.txt'));
    await ready();
    await page.evaluate(id=>window.openTicket(id),Number(ticket));
    await pane().locator('[data-attachment-gallery]').first().waitFor();
    assert.equal(await queue().locator('.tf-att-card').count(),0);
    await pane().locator('#attach-input').setInputFiles(file('Panel B '+run+'.txt'));
    await ready();
    await page.evaluate(id=>window.openTicket(id),created.ticket_id);
    await ready();
    assert((await queue().innerText()).includes('Panel A'));
    const keyA = await queue().locator('.tf-att-card').getAttribute('data-upload-key');
    const activeTab = page.locator('.navbar-left .tab.active');
    await activeTab.locator('.close-tab').click();
    await page.waitForURL('**id='+ticket);
    await ready();
    assert((await queue().innerText()).includes('Panel B'));
    assert.notEqual(await queue().locator('.tf-att-card').getAttribute('data-upload-key'),keyA);
    await removeAll();
    console.log('PASS pane isolation and asynchronous close cleanup');

    // Read-only page smoke to catch integration script regressions.
    for (const url of ['/customers/','/reporting/','/tickets/']) {
      const response = await page.goto(url);
      assert(response.ok(),url+' '+response.status());
    }
    assert.deepEqual(errors,[]);
    console.log('PASS customers/reporting/tickets page smoke; no JS exceptions');
  } finally {
    // Failed assertions must not leave this isolated QA browser's queue behind.
    if(page && !page.isClosed()) await page.evaluate(async()=>{
      for(const entry of window.Tabs?.paneCache?.values() || []) {
        if(entry.$pane?.classList.contains('ticket-pane')) await window.TicketAttachments?.mount(entry.$pane).dispose();
      }
    }).catch(error=>console.warn('QA pending cleanup:',error.message));
    await browser.close();
  }
})().catch(error=>{console.error(error.stack);process.exitCode=1;});
