/* Run with node --test tests/js/attachments.test.js. Requires Playwright on the
 * module path and Chrome. All HTTP and uploads are isolated fixtures; no login,
 * running TicketFlow server, production data, or external viewers are used. */
'use strict';
const { test, before, after, beforeEach, afterEach } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright');
const source = path.resolve(__dirname, '../../resources/static/tickets/attachments.js');
const css = path.resolve(__dirname, '../../resources/static/tickets/attachments.css');
const vendor = path.resolve(__dirname, '../../resources/static/tickets/vendor/pdfjs');
let browser, page, exceptions;

before(async () => { browser = await chromium.launch({ channel: 'chrome', headless: true }); });
after(async () => { await browser?.close(); });
beforeEach(async () => {
    page = await browser.newPage({ viewport: { width: 900, height: 800 } });
    exceptions = [];
    page.on('pageerror', error => exceptions.push(error.message));
    await page.route('http://attachments.test/**', async route => {
        const pathname = new URL(route.request().url()).pathname;
        if (pathname.startsWith('/static/tickets/vendor/pdfjs/')) {
            const filename = path.resolve(vendor, pathname.slice('/static/tickets/vendor/pdfjs/'.length));
            if (filename.startsWith(vendor + path.sep) && fs.existsSync(filename)) {
                await route.fulfill({ body: fs.readFileSync(filename), contentType: filename.endsWith('.js') ? 'text/javascript' : 'application/octet-stream' });
                return;
            }
        }
        await route.fulfill({ contentType: 'text/html', body: '<!doctype html><html><head></head><body></body></html>' });
    });
    await page.goto('http://attachments.test/');
    await page.addStyleTag({ path: css });
    await page.evaluate(() => {
        window.__uploads = [];
        window.__requests = [];
        window.__statuses = [];
        window.__previewData = null;
        const nativeFetch = window.fetch.bind(window);
        window.fetch = async (url, options = {}) => {
            if (String(url).includes('/static/')) return nativeFetch(url, options);
            window.__requests.push({ url, method: options.method, headers: options.headers, body: options.body });
            return new Response(JSON.stringify(window.__previewData || {}), {
                status: window.__statuses.shift() || 200, headers: { 'Content-Type': 'application/json' }
            });
        };
        class Upload extends EventTarget {
            constructor() { super(); this.upload = new EventTarget(); }
            open(method, url) { this.method = method; this.url = url; }
            setRequestHeader(name, value) { (this.headers ||= {})[name] = value; }
            send(body) { this.body = body; window.__uploads.push(this); }
            abort() { this.aborted = true; this.dispatchEvent(new Event('abort')); }
            respond(data, status = 200) { this.response = data; this.status = status; this.dispatchEvent(new Event('load')); }
            progress(loaded, total) { this.upload.dispatchEvent(new ProgressEvent('progress', { lengthComputable: true, loaded, total })); }
        }
        window.XMLHttpRequest = Upload;
        window.__makePane = (id, dataset = {}) => {
            const root = document.createElement('div');
            root.dataset.ticketId = id;
            Object.assign(root.dataset, dataset);
            root.innerHTML = '<div id="messageInputArea"><div class="ql-editor" contenteditable="true"></div><textarea hidden></textarea><button id="attach-btn">Adjuntar</button><input id="attach-input" type="file"><div id="pending-attachments"></div></div>';
            document.body.append(root);
            return root;
        };
        window.__enqueue = (root, files, kind = 'change') => {
            const transfer = new DataTransfer();
            files.forEach(([name, size = 4, type = 'text/plain']) => transfer.items.add(new File([new Uint8Array(size)], name, { type })));
            if (kind === 'paste') {
                const event = new ClipboardEvent('paste', { bubbles: true, cancelable: true, clipboardData: transfer });
                root.querySelector('.ql-editor').dispatchEvent(event);
                return event.defaultPrevented;
            }
            if (kind === 'drop') {
                const event = new DragEvent('drop', { bubbles: true, cancelable: true, dataTransfer: transfer });
                root.querySelector('.ql-editor').dispatchEvent(event);
                return event.defaultPrevented;
            }
            const input = root.querySelector('input');
            input.files = transfer.files;
            input.dispatchEvent(new Event('change', { bubbles: true }));
        };
    });
    await page.addScriptTag({ path: source });
});
afterEach(async () => {
    const errors = exceptions.slice();
    await page?.close();
    assert.deepEqual(errors, [], 'No uncaught browser exceptions');
});

test('mount is idempotent, drafts and IDs remain isolated across detached panes', async () => {
    const result = await page.evaluate(() => {
        const a = __makePane('1'), b = __makePane('2');
        const first = TicketAttachments.mount(a), second = TicketAttachments.mount(b);
        __enqueue(a, [['a.txt']]);
        a.remove();
        __enqueue(b, [['b.txt']]);
        __uploads[0].respond({ id: 101, filename: 'a.txt', size: 4 });
        __uploads[1].respond({ id: 202, filename: 'b.txt', size: 4 });
        document.body.append(a);
        return { same: first === TicketAttachments.mount(a), different: first.draftKey !== second.draftKey,
            first: first.ids(), second: second.ids(), url: __uploads[0].url, ready: first.ready(), hasDraft: __uploads[0].body.has('draft_key') };
    });
    assert.deepEqual(result, { same: true, different: true, first: [101], second: [202], url: '/tickets/1/attachments/upload/', ready: true, hasDraft: false });
});

test('capturing paste and drop prevent Quill base64 handlers but leave plain-text paste alone', async () => {
    const result = await page.evaluate(() => {
        const root = __makePane('');
        const api = TicketAttachments.mount(root);
        let quill = 0;
        root.querySelector('.ql-editor').addEventListener('paste', () => quill++);
        const paste = __enqueue(root, [['capture.png', 10, 'image/png']], 'paste');
        const drop = __enqueue(root, [['drop.txt']], 'drop');
        root.querySelector('.ql-editor').dispatchEvent(new ClipboardEvent('paste', { bubbles: true }));
        return { paste, drop, quill, has: api.hasFiles(), count: root.querySelectorAll('.tf-att-card').length,
            endpoint: __uploads[0].url, draft: __uploads[0].body.get('draft_key') === api.draftKey };
    });
    assert.deepEqual(result, { paste: true, drop: true, quill: 1, has: true, count: 2, endpoint: '/attachments/drafts/upload/', draft: true });
});

test('real upload byte events separate processing from ready; retries preserve upload_key', async () => {
    const result = await page.evaluate(() => {
        const root = __makePane('3');
        const api = TicketAttachments.mount(root, { getCsrfToken: () => 'test-csrf' });
        __enqueue(root, [['first.txt'], ['second.txt']]);
        const first = __uploads[0], key = first.body.get('upload_key');
        first.progress(40, 100);
        const progress = root.querySelector('progress').value;
        first.progress(100, 100);
        const processing = root.querySelector('.tf-att-state').textContent;
        const readyBeforeResponse = api.ready();
        first.dispatchEvent(new Event('error'));
        const second = __uploads[1];
        second.respond({ id: 2, filename: 'second.txt', size: 4 });
        root.querySelector('[data-action="retry"]').click();
        const retry = __uploads[2];
        retry.respond({ id: 1, filename: 'first.txt', size: 4 });
        return { progress, processing, readyBeforeResponse, sameKey: retry.body.get('upload_key') === key,
            ready: api.ready(), ids: api.ids(), csrf: retry.headers['X-CSRFToken'] };
    });
    assert.deepEqual(result, { progress: 40, processing: 'Procesando…', readyBeforeResponse: false, sameKey: true, ready: true, ids: [1, 2], csrf: 'test-csrf' });
});

test('configured count, per-file bytes, total bytes and extension validation apply to every ingress', async () => {
    const result = await page.evaluate(() => {
        const root = __makePane('3', { attachmentMaxFiles: '2', attachmentMaxBytes: '6', attachmentMaxTotalBytes: '9', attachmentAllowedExtensions: 'txt,CSV' });
        TicketAttachments.mount(root);
        __enqueue(root, [['too-big.txt', 7], ['bad.exe', 1], ['first.txt', 5], ['total.csv', 5], ['good.CSV', 4], ['count.txt', 1]]);
        return { count: root.querySelectorAll('.tf-att-card').length, notice: root.querySelector('.tf-att-notice').textContent,
            accept: root.querySelector('input').accept };
    });
    assert.equal(result.count, 2);
    assert.equal(result.accept, '.txt,.csv');
    assert.match(result.notice, /máximo 6 B/);
    assert.match(result.notice, /tipo de archivo no permitido/);
    assert.match(result.notice, /total no puede superar 9 B/);
    assert.match(result.notice, /hasta 2 archivos/);
});

test('cancellation aborts XHR, uses scoped JSON and removes a late successful upload', async () => {
    const result = await page.evaluate(async () => {
        const root = __makePane('15');
        const api = TicketAttachments.mount(root, { getCsrfToken: () => 'csrf' });
        __enqueue(root, [['cancel.txt']]);
        const xhr = __uploads[0];
        root.querySelector('[data-action="remove"]').click();
        await new Promise(resolve => setTimeout(resolve));
        xhr.respond({ id: 19, filename: 'cancel.txt', size: 4 });
        await new Promise(resolve => setTimeout(resolve));
        return { aborted: xhr.aborted, has: api.hasFiles(), requests: __requests.map(r => ({ url: r.url, body: r.body, headers: r.headers })), key: xhr.body.get('upload_key'), draft: api.draftKey };
    });
    assert.equal(result.aborted, true);
    assert.equal(result.has, false);
    assert.equal(new URL(result.requests[0].url).pathname, '/attachments/uploads/cancel/');
    assert.deepEqual(JSON.parse(result.requests[0].body), { upload_key: result.key, ticket_id: 15 });
    assert.equal(result.requests[0].headers['Content-Type'], 'application/json');
    assert.equal(result.requests[0].headers['X-CSRFToken'], 'csrf');
    assert.equal(result.requests[1].url, '/attachments/19/pending/delete/');
});

test('failed removals preserve uploaded files; failed cancellation blocks publication until retried', async () => {
    const result = await page.evaluate(async () => {
        const root = __makePane('4');
        const api = TicketAttachments.mount(root);
        __enqueue(root, [['keep.txt']]);
        __uploads[0].respond({ id: 4, filename: 'keep.txt', size: 4 });
        __statuses.push(503);
        root.querySelector('[data-action="remove"]').click();
        await new Promise(resolve => setTimeout(resolve));
        const ids = api.ids();
        __enqueue(root, [['cancel.txt']]);
        __statuses.push(503);
        root.querySelectorAll('[data-action="remove"]')[1].click();
        await new Promise(resolve => setTimeout(resolve));
        const blocked = !api.ready();
        const error = root.querySelector('[data-state="cancel-error"]') !== null;
        root.querySelector('[data-state="cancel-error"] [data-action="remove"]').click();
        await new Promise(resolve => setTimeout(resolve));
        return { ids, blocked, error, recovered: api.ready(), count: root.querySelectorAll('.tf-att-card').length };
    });
    assert.deepEqual(result, { ids: [4], blocked: true, error: true, recovered: true, count: 1 });
});

test('lock blocks mutations; publication clear makes no server delete and removes unload warnings', async () => {
    const result = await page.evaluate(async () => {
        const root = __makePane('5');
        const api = TicketAttachments.mount(root);
        __enqueue(root, [['publish.txt']]);
        __uploads[0].respond({ id: 5, filename: 'publish.txt', size: 4 });
        api.lock(true);
        __enqueue(root, [['blocked.txt']], 'paste');
        const disabled = root.querySelector('[data-action="remove"]').disabled && root.querySelector('input').disabled;
        const count = root.querySelectorAll('.tf-att-card').length;
        const oldDraft = api.draftKey;
        api.clear();
        const exit = new Event('beforeunload', { cancelable: true });
        window.dispatchEvent(exit);
        const disposed = await api.dispose();
        return { disabled, count, has: api.hasFiles(), ready: api.ready(), ids: api.ids(), rotated: api.draftKey !== oldDraft,
            warning: exit.defaultPrevented, serverMutations: __requests.length, disposed };
    });
    assert.deepEqual(result, { disabled: true, count: 1, has: false, ready: true, ids: [], rotated: true, warning: false, serverMutations: 0, disposed: true });
});

test('pane waitUntil blocks close on failed cleanup and honors synchronous cancellation guards', async () => {
    const result = await page.evaluate(async () => {
        const root = __makePane('6');
        const api = TicketAttachments.mount(root);
        __enqueue(root, [['pending.txt']]);
        __uploads[0].respond({ id: 6, filename: 'pending.txt', size: 4 });
        window.confirm = () => true;
        const promises = [];
        const waitUntil = promise => promises.push(promise);
        __statuses.push(503);
        root.dispatchEvent(new CustomEvent('tf:before-pane-close', { cancelable: true, detail: { waitUntil } }));
        const failed = await Promise.all(promises);
        const retained = api.hasFiles();
        root.addEventListener('tf:before-pane-close', event => event.preventDefault(), { once: true });
        promises.length = 0;
        root.dispatchEvent(new CustomEvent('tf:before-pane-close', { cancelable: true, detail: { waitUntil } }));
        const prevented = await Promise.all(promises);
        promises.length = 0;
        root.dispatchEvent(new CustomEvent('tf:before-pane-close', { cancelable: true, detail: { waitUntil } }));
        const cleaned = await Promise.all(promises);
        return { failed, retained, prevented, cleaned, requests: __requests.length };
    });
    assert.deepEqual(result, { failed: [false], retained: true, prevented: [false], cleaned: [true], requests: 2 });
});

test('SSR hydration preserves null sizes, displays types and excludes external previews or unsafe downloads', async () => {
    const result = await page.evaluate(() => {
        const root = __makePane('7');
        const container = document.createElement('div');
        container.dataset.attachmentGallery = '';
        const script = document.createElement('script');
        script.type = 'application/json';
        script.textContent = JSON.stringify([
            { filename: 'nombre-muy-largo.csv', size: null, file_url: 'https://legacy.test/a.csv', preview_kind: 'csv', preview_url: 'https://legacy.test/a.csv' },
            { filename: '<script>alert(1)</script>.zip', file_url: 'javascript:alert(1)' }
        ]);
        container.append(script); root.append(container);
        TicketAttachments.mount(root);
        return { cards: container.querySelectorAll('.tf-att-card').length, detail: container.querySelector('.tf-att-size').textContent,
            previews: container.querySelectorAll('button').length, links: container.querySelectorAll('a').length,
            safeName: container.querySelectorAll('.tf-att-name')[1].title, scripts: container.querySelectorAll('script').length };
    });
    assert.deepEqual(result, { cards: 2, detail: 'CSV · Tamaño no disponible', previews: 0, links: 1, safeName: '<script>alert(1)</script>.zip', scripts: 0 });
});

test('plaintext previews never execute HTML and revoke local image URLs on close/clear', async () => {
    await page.evaluate(() => {
        const container = document.createElement('div'); document.body.append(container);
        __previewData = { kind: 'text', text: '<script>window.unsafe = true</script>' + 'x'.repeat(100001) };
        TicketAttachments.render(container, [{ id: 8, filename: 'safe.txt', size: 100040, preview_kind: 'text', preview_url: '/attachments/8/preview/' }]);
        container.querySelector('.tf-att-thumb-preview').click();
    });
    await page.locator('.tf-att-text').waitFor();
    assert.equal(await page.locator('.tf-att-text').evaluate(el => el.textContent.length), 100000);
    assert.equal(await page.evaluate(() => !!window.unsafe), false);
    await page.getByRole('button', { name: 'Cerrar', exact: true }).click();
    const result = await page.evaluate(async () => {
        let created = 0, revoked = 0;
        const create = URL.createObjectURL.bind(URL), revoke = URL.revokeObjectURL.bind(URL);
        URL.createObjectURL = blob => { created++; return create(blob); };
        URL.revokeObjectURL = url => { revoked++; revoke(url); };
        const root = __makePane('9'); const api = TicketAttachments.mount(root);
        __enqueue(root, [['photo.png', 4, 'image/png']]);
        root.querySelector('.tf-att-thumb-preview').click();
        await new Promise(resolve => setTimeout(resolve));
        document.querySelector('.tf-att-dialog').dispatchEvent(new Event('cancel', { cancelable: true }));
        __uploads[0].respond({ id: 9, filename: 'photo.png', size: 4 }); api.clear();
        return { created, revoked, dialog: document.querySelector('dialog') !== null };
    });
    assert.deepEqual(result, { created: 2, revoked: 2, dialog: false });
});

test('vendored .js ES modules render PDF pages and release resources without script blobs', { skip: !fs.existsSync(path.join(vendor, 'pdf.js')) }, async () => {
    await page.evaluate(() => {
        window.__activePdfUrls = new Set();
        const create = URL.createObjectURL.bind(URL), revoke = URL.revokeObjectURL.bind(URL);
        URL.createObjectURL = blob => { const url = create(blob); __activePdfUrls.add(url); return url; };
        URL.revokeObjectURL = url => { __activePdfUrls.delete(url); revoke(url); };
        const objects = [
            '<< /Type /Catalog /Pages 2 0 R >>', '<< /Type /Pages /Kids [3 0 R 5 0 R] /Count 2 >>',
            '<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] /Resources << >> /Contents 4 0 R >>',
            '<< /Length 27 >>\nstream\n1 0 0 rg 20 20 80 80 re f\n\nendstream',
            '<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] /Resources << >> /Contents 6 0 R >>',
            '<< /Length 27 >>\nstream\n0 0 1 rg 20 20 80 80 re f\n\nendstream'
        ];
        let pdf = '%PDF-1.4\n'; const offsets = [0];
        objects.forEach((object, index) => { offsets.push(pdf.length); pdf += `${index + 1} 0 obj\n${object}\nendobj\n`; });
        const xref = pdf.length;
        pdf += `xref\n0 7\n0000000000 65535 f \n${offsets.slice(1).map(offset => String(offset).padStart(10, '0') + ' 00000 n \n').join('')}trailer\n<< /Size 7 /Root 1 0 R >>\nstartxref\n${xref}\n%%EOF`;
        const root = __makePane('10'); TicketAttachments.mount(root);
        const transfer = new DataTransfer(); transfer.items.add(new File([pdf], 'pages.pdf', { type: 'application/pdf' }));
        const input = root.querySelector('input'); input.files = transfer.files; input.dispatchEvent(new Event('change', { bubbles: true }));
        root.querySelector('.tf-att-thumb-preview').click();
    });
    await page.locator('.tf-att-pdf-canvas').waitFor({ timeout: 30000 });
    await page.waitForFunction(() => document.querySelector('.tf-att-preview-status').textContent === '');
    assert.deepEqual(await page.locator('canvas').evaluate(canvas => Array.from(canvas.getContext('2d').getImageData(Math.floor(canvas.width * .3), Math.floor(canvas.height * .7), 1, 1).data)), [255, 0, 0, 255]);
    assert.equal(await page.locator('.tf-att-pages').innerText(), 'Página anterior\nPágina 1 de 2\nPágina siguiente');
    assert.equal(await page.locator('iframe').count(), 0);
    await page.getByRole('button', { name: 'Página siguiente', exact: true }).click();
    await page.waitForFunction(() => document.querySelector('canvas')?.getAttribute('aria-label').includes('página 2'));
    await page.waitForFunction(() => document.querySelector('.tf-att-preview-status').textContent === '');
    assert.deepEqual(await page.locator('canvas').evaluate(canvas => Array.from(canvas.getContext('2d').getImageData(Math.floor(canvas.width * .3), Math.floor(canvas.height * .7), 1, 1).data)), [0, 0, 255, 255]);
    assert.equal(await page.locator('canvas').count(), 1);
    await page.setViewportSize({ width: 360, height: 780 });
    await page.waitForFunction(() => document.querySelector('canvas').getBoundingClientRect().width <= document.querySelector('.tf-att-preview-body').clientWidth);
    await page.waitForFunction(() => document.querySelector('.tf-att-preview-body').getAttribute('aria-busy') === 'false');
    await page.getByRole('button', { name: 'Acercar', exact: true }).click();
    await page.keyboard.press('Escape');
    assert.equal(await page.locator('dialog').count(), 0);
    await page.waitForFunction(() => __activePdfUrls.size === 0);
});

test('narrow gallery preserves extension, 36px actions and centered dialog despite reset styles', async () => {
    await page.setViewportSize({ width: 320, height: 720 });
    await page.addStyleTag({ content: '* { margin: 0; }' });
    await page.evaluate(() => {
        const container = document.createElement('div'); document.body.append(container);
        __previewData = { kind: 'text', text: 'safe' };
        TicketAttachments.render(container, [{ id: 11, filename: 'very-long-file-'.repeat(20) + '.txt', size: null, preview_kind: 'text', preview_url: '/attachments/11/preview/' }]);
    });
    const result = await page.evaluate(() => ({ overflow: document.documentElement.scrollWidth > innerWidth,
        ext: document.querySelector('.tf-att-name-ext').textContent,
        actions: Array.from(document.querySelectorAll('.tf-att-action')).every(el => el.getBoundingClientRect().height >= 36) }));
    assert.deepEqual(result, { overflow: false, ext: '.txt', actions: true });
    await page.locator('.tf-att-thumb-preview').click();
    const box = await page.locator('dialog').boundingBox();
    assert.ok(Math.abs(box.x - (320 - box.width) / 2) <= 1);
});

test('five pending images at 390px scroll within a bounded queue without horizontal overflow', async () => {
    await page.setViewportSize({ width: 390, height: 844 });
    const result = await page.evaluate(() => {
        const root = __makePane('18');
        TicketAttachments.mount(root);
        __enqueue(root, Array.from({ length: 5 }, (_, i) => ['imagen-con-nombre-largo-'.repeat(8) + i + '.png', 4, 'image/png']));
        const pending = root.querySelector('#pending-attachments');
        const thumbnails = Array.from(pending.querySelectorAll('.tf-att-thumb'));
        return { count: pending.querySelectorAll('.tf-att-card').length,
            bounded: pending.getBoundingClientRect().height <= Math.min(innerHeight * .32, 320) + 1,
            scrolls: pending.scrollHeight > pending.clientHeight,
            horizontalOverflow: pending.scrollWidth > pending.clientWidth,
            compact: thumbnails.every(el => el.getBoundingClientRect().height <= 44) };
    });
    assert.deepEqual(result, { count: 5, bounded: true, scrolls: true, horizontalOverflow: false, compact: true });
});
