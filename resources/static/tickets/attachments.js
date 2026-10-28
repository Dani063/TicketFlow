/*
 * TicketAttachments — independent state for each ticket pane; no Quill dependency.
 *
 * mount(root, { ticketId, getCsrfToken }) is idempotent. Public instance:
 *   hasFiles(), ready(), ids(), draftKey (UUID), clear(), lock(bool), dispose().
 * clear() is for a successful publication ONLY: it never deletes server files.
 * It returns false while uploads/removals are incomplete. dispose() returns a
 * Promise<boolean>; a failed pending-file cleanup keeps the pane usable.
 *
 * Optional root datasets (byte limits are integers): attachmentMaxFiles,
 * attachmentMaxBytes, attachmentMaxTotalBytes, attachmentAllowedExtensions
 * (CSV), attachmentCancelUrl. Defaults: 5 / 15 MiB / 25 MiB.
 * Cancellation POST /attachments/uploads/cancel/ uses JSON
 * { upload_key, ticket_id } OR { upload_key, draft_key }, never both scopes.
 * The server must record a tombstone even
 * if the upload has not committed yet. A late successful XHR is also deleted.
 *
 * Events on root: ticketattachments:change { instance, ready, ids, count, size },
 * ticketattachments:error { message }, ticketattachments:disposed.
 * Routers dispatch cancelable tf:before-pane-close with detail.waitUntil(promise),
 * await every registered Promise<boolean>, and close ONLY if every result is
 * true. tf:pane-dispose is the idempotent final teardown notification.
 * Detaching/hiding a pane is NOT a disposal.
 * render(container, metadata[]) replaces a comment gallery; SSR galleries use
 * [data-attachment-gallery] with a child script[type="application/json"].
 */
(function (window) {
    'use strict';

    const document = window.document;
    const mounts = new WeakMap();
    const galleries = new WeakMap();
    const previewKinds = new Set(['image', 'pdf', 'text', 'csv']);
    const rasterTypes = new Set(['image/png', 'image/jpeg', 'image/gif', 'image/webp', 'image/avif', 'image/bmp']);
    const MAX_PREVIEW_BYTES = 25 * 1024 * 1024;
    const MAX_JSON_BYTES = 1024 * 1024;
    const MAX_TEXT = 100000;
    const MAX_ROWS = 200;
    const MAX_COLS = 30;
    let currentPreview = null;

    function uuid() {
        if (window.crypto.randomUUID) return window.crypto.randomUUID();
        const bytes = window.crypto.getRandomValues(new Uint8Array(16));
        bytes[6] = (bytes[6] & 15) | 64;
        bytes[8] = (bytes[8] & 63) | 128;
        return Array.from(bytes, (b, i) => ([4, 6, 8, 10].includes(i) ? '-' : '') + b.toString(16).padStart(2, '0')).join('');
    }

    function node(tag, className, text) {
        const el = document.createElement(tag);
        if (className) el.className = className;
        if (text !== undefined) el.textContent = text;
        return el;
    }

    function icon(name) {
        const el = node('i', 'fas fa-' + name);
        el.setAttribute('aria-hidden', 'true');
        return el;
    }

    function button(label, glyph, action) {
        const el = node('button', 'tf-att-action');
        el.type = 'button';
        el.append(icon(glyph), node('span', '', label));
        el.addEventListener('click', action);
        return el;
    }

    function sizeLabel(value) {
        if (value === null || value === undefined) return 'Tamaño no disponible';
        const bytes = Math.max(0, Number(value) || 0);
        if (bytes < 1024) return bytes + ' B';
        const power = Math.min(3, Math.floor(Math.log(bytes) / Math.log(1024)));
        return new Intl.NumberFormat('es', { maximumFractionDigits: 1 }).format(bytes / (1024 ** power)) + ' ' + ['B', 'KB', 'MB', 'GB'][power];
    }

    function safeId(id) {
        return (typeof id === 'number' && Number.isSafeInteger(id) && id > 0)
            || (typeof id === 'string' && /^[A-Za-z0-9_-]{1,100}$/.test(id));
    }

    function safeUrl(value, external) {
        if (typeof value !== 'string' || !value.trim()) return null;
        try {
            const url = new URL(value, window.location.origin);
            if (url.username || url.password || !['https:', 'http:'].includes(url.protocol)) return null;
            if (url.origin === window.location.origin) return url.href;
            return external && url.protocol === 'https:' ? url.href : null;
        } catch (_) { return null; }
    }

    function metadata(value) {
        const raw = value && typeof value === 'object' ? value : {};
        const id = safeId(raw.id) ? raw.id : null;
        const explicitDownload = safeUrl(raw.download_url, true);
        return {
            id,
            filename: typeof raw.filename === 'string' && raw.filename ? raw.filename : 'Archivo adjunto',
            size: raw.size !== null && raw.size !== undefined && raw.size !== '' && Number.isFinite(Number(raw.size)) && Number(raw.size) >= 0 ? Number(raw.size) : null,
            file_type: typeof raw.file_type === 'string' ? raw.file_type : '',
            category: typeof raw.category === 'string' ? raw.category : '',
            preview_kind: previewKinds.has(raw.preview_kind) ? raw.preview_kind : null,
            preview_url: safeUrl(raw.preview_url, false),
            thumbnail_url: safeUrl(raw.thumbnail_url, false),
            download_url: explicitDownload || (id !== null ? safeUrl('/attachments/' + encodeURIComponent(id) + '/download/', false) : safeUrl(raw.file_url, true))
        };
    }

    function localKind(file) {
        if (rasterTypes.has(file.type)) return 'image';
        if (file.type === 'application/pdf' || /\.pdf$/i.test(file.name)) return 'pdf';
        if (file.type === 'text/csv' || /\.csv$/i.test(file.name)) return 'csv';
        if (file.type === 'text/plain' || /\.(txt|log)$/i.test(file.name)) return 'text';
        return null;
    }

    function filename(el, name) {
        el.replaceChildren();
        el.title = name;
        el.setAttribute('aria-label', name);
        const dot = name.lastIndexOf('.');
        const base = dot > 0 ? name.slice(0, dot) : name;
        const ext = dot > 0 ? name.slice(dot) : '';
        const stem = node('span', 'tf-att-name-stem', base);
        stem.dir = 'auto';
        el.append(stem);
        if (ext) el.append(node('span', 'tf-att-name-ext', ext));
    }

    function download(meta) {
        if (!meta.download_url) return null;
        const link = node('a', 'tf-att-action tf-att-download');
        const external = new URL(meta.download_url).origin !== window.location.origin;
        link.href = meta.download_url;
        link.append(icon('download'), node('span', '', external ? 'Descargar externo' : 'Descargar'));
        link.title = (external ? 'Descargar desde un sitio externo: ' : 'Descargar: ') + meta.filename;
        link.setAttribute('aria-label', link.title);
        link.rel = 'noopener noreferrer';
        link.referrerPolicy = 'no-referrer';
        if (external) link.target = '_blank';
        else link.setAttribute('download', meta.filename);
        return link;
    }

    function canPreview(entry) {
        if (entry.cancelRequested) return false;
        return entry.file ? !!localKind(entry.file) : !!(entry.meta.preview_kind && entry.meta.preview_url);
    }

    function glyph(meta) {
        const kind = meta.preview_kind || meta.category || meta.file_type;
        if (kind === 'image' || kind.startsWith('image/')) return 'file-image';
        if (kind === 'pdf' || /pdf$/i.test(meta.filename)) return 'file-pdf';
        if (kind === 'csv' || /\.(csv|xlsx?)$/i.test(meta.filename)) return 'file-excel';
        if (/\.(docx?)$/i.test(meta.filename)) return 'file-word';
        if (/\.(zip|rar|7z)$/i.test(meta.filename)) return 'file-archive';
        return 'file-alt';
    }

    function typeLabel(meta) {
        const ext = /\.([a-z0-9]{1,12})$/i.exec(meta.filename)?.[1];
        if (ext) return ext.toUpperCase();
        return { image: 'Imagen', pdf: 'PDF', text: 'Texto', csv: 'CSV' }[meta.preview_kind] || 'Archivo';
    }

    function fileDetail(meta) { return typeLabel(meta) + ' · ' + sizeLabel(meta.size); }

    function makeCard(entry, gallery) {
        const meta = entry.meta;
        const card = node('article', 'tf-att-card');
        const thumb = node(canPreview(entry) ? 'button' : 'div', 'tf-att-thumb');
        if (canPreview(entry)) {
            thumb.type = 'button';
            thumb.classList.add('tf-att-thumb-preview');
            thumb.setAttribute('aria-label', 'Vista previa de ' + meta.filename);
            thumb.addEventListener('click', () => openPreview(gallery, entry, thumb));
        }
        thumb.append(icon(glyph(meta)));
        const thumbnail = entry.thumbURL || (meta.preview_kind === 'image' ? meta.thumbnail_url : null);
        if (thumbnail) {
            if (!entry.file && meta.preview_kind === 'image') card.classList.add('tf-att-card-image');
            const img = node('img', 'tf-att-thumbnail');
            img.alt = '';
            img.loading = 'lazy';
            img.decoding = 'async';
            img.referrerPolicy = 'no-referrer';
            img.addEventListener('error', () => img.remove(), { once: true });
            img.src = thumbnail;
            thumb.append(img);
        }
        const info = node('div', 'tf-att-info');
        const title = node('div', 'tf-att-name');
        filename(title, meta.filename);
        info.append(title, node('div', 'tf-att-size', fileDetail(meta)));
        const actions = node('div', 'tf-att-actions');
        if (canPreview(entry)) {
            const preview = button('Vista previa', 'expand', () => openPreview(gallery, entry, preview));
            preview.setAttribute('aria-label', 'Vista previa de ' + meta.filename);
            actions.append(preview);
        }
        if (!entry.file || entry.state === 'uploaded') {
            const link = download(meta);
            if (link) actions.append(link);
        }
        card.append(thumb, info, actions);
        return { card, actions };
    }

    function render(container, cards) {
        if (!container || container.nodeType !== 1) throw new TypeError('Se necesita un contenedor de adjuntos.');
        const previous = galleries.get(container);
        if (currentPreview && currentPreview.gallery === previous) currentPreview.close();
        const gallery = { container, entries: (Array.isArray(cards) ? cards : []).map(meta => ({ meta: metadata(meta) })) };
        galleries.set(container, gallery);
        container.classList.add('tf-att-gallery');
        container.setAttribute('data-attachment-gallery', '');
        container.replaceChildren(...gallery.entries.map(entry => makeCard(entry, gallery).card));
        return container;
    }

    function hydrate(root) {
        const containers = Array.from(root.querySelectorAll('[data-attachment-gallery]'));
        if (root.matches('[data-attachment-gallery]')) containers.unshift(root);
        containers.forEach(container => {
            const script = container.querySelector('script[type="application/json"]');
            if (!script) return;
            try {
                const data = JSON.parse(script.textContent);
                render(container, Array.isArray(data) ? data : data.attachments || []);
            } catch (_) {
                // Keep existing SSR download links when JSON cannot be hydrated.
                if (!container.querySelector('.tf-att-hydration-error')) {
                    container.append(node('p', 'tf-att-hydration-error', 'No se pudo cargar la vista de adjuntos. Recarga la página.'));
                }
            }
        });
    }

    // Read response bodies with a byte budget before constructing JSON or blobs.
    async function boundedBytes(response, limit) {
        if (Number(response.headers.get('Content-Length')) > limit) throw new Error('preview-limit');
        if (!response.body || !response.body.getReader) {
            const data = new Uint8Array(await response.arrayBuffer());
            if (data.length > limit) throw new Error('preview-limit');
            return data;
        }
        const reader = response.body.getReader();
        const chunks = [];
        let size = 0;
        try {
            while (true) {
                const result = await reader.read();
                if (result.done) break;
                size += result.value.length;
                if (size > limit) {
                    await reader.cancel();
                    throw new Error('preview-limit');
                }
                chunks.push(result.value);
            }
        } finally { reader.releaseLock(); }
        const bytes = new Uint8Array(size);
        let offset = 0;
        chunks.forEach(chunk => { bytes.set(chunk, offset); offset += chunk.length; });
        return bytes;
    }

    // Bounded RFC-4180 style CSV parser for immediate LOCAL previews only.
    function parseCsv(text) {
        const rows = [];
        let row = [], value = '', quoted = false, truncated = false;
        const cell = () => {
            if (row.length < MAX_COLS) row.push(value);
            else truncated = true;
            value = '';
        };
        for (let i = 0; i < text.length; i++) {
            const c = text[i];
            if (c === '"') {
                if (quoted && text[i + 1] === '"') { value += '"'; i++; }
                else if (quoted || !value) quoted = !quoted;
                else value += c;
            } else if (c === ',' && !quoted) cell();
            else if ((c === '\n' || c === '\r') && !quoted) {
                cell(); rows.push(row); row = [];
                if (c === '\r' && text[i + 1] === '\n') i++;
                if (rows.length >= MAX_ROWS) { truncated = truncated || i < text.length - 1; break; }
            } else value += c;
        }
        if ((value || row.length) && rows.length < MAX_ROWS) { cell(); rows.push(row); }
        return { kind: 'csv', rows, truncated };
    }

    function plainPreview(body, data, kind) {
        let truncated = !!data.truncated;
        if (kind === 'text') {
            if (typeof data.text !== 'string') throw new Error('preview-data');
            truncated = truncated || data.text.length > MAX_TEXT;
            body.append(node('pre', 'tf-att-text', data.text.slice(0, MAX_TEXT)));
        } else {
            if (!Array.isArray(data.rows)) throw new Error('preview-data');
            const table = node('table', 'tf-att-csv');
            table.append(node('caption', 'tf-att-sr', 'Contenido CSV; filas y columnas del archivo'));
            const tbody = node('tbody');
            let remaining = MAX_TEXT;
            truncated = truncated || data.rows.length > MAX_ROWS;
            for (const row of data.rows.slice(0, MAX_ROWS)) {
                if (!Array.isArray(row)) throw new Error('preview-data');
                if (!remaining) { truncated = true; break; }
                const tr = node('tr');
                truncated = truncated || row.length > MAX_COLS;
                for (const value of row.slice(0, MAX_COLS)) {
                    const text = value == null ? '' : String(value);
                    const cap = Math.min(remaining, 2000);
                    tr.append(node('td', '', text.slice(0, cap)));
                    remaining -= Math.min(text.length, cap);
                    truncated = truncated || text.length > cap;
                }
                tbody.append(tr);
            }
            table.append(tbody);
            const scroll = node('div', 'tf-att-table-scroll');
            scroll.tabIndex = 0;
            scroll.setAttribute('role', 'region');
            scroll.setAttribute('aria-label', 'Vista previa CSV desplazable');
            scroll.append(table);
            body.append(scroll);
        }
        if (truncated) body.append(node('p', 'tf-att-preview-note', 'Vista previa recortada. Descarga el archivo para ver todo el contenido.'));
    }

    function openPreview(gallery, first, opener) {
        if (currentPreview) currentPreview.close();
        const entries = gallery.entries.filter(canPreview);
        if (!entries.includes(first)) return;
        const dialog = node('dialog', 'tf-att-dialog');
        const titleId = 'tf-att-title-' + uuid();
        dialog.setAttribute('aria-labelledby', titleId);
        const head = node('div', 'tf-att-dialog-head');
        const heading = node('div', 'tf-att-dialog-heading');
        const title = node('h2', 'tf-att-dialog-title');
        title.id = titleId;
        const detail = node('p', 'tf-att-size');
        heading.append(title, detail);
        const links = node('div', 'tf-att-actions');
        const closeButton = button('Cerrar', 'times', close);
        head.append(heading, links, closeButton);
        const toolbar = node('div', 'tf-att-preview-toolbar');
        const prev = button('Anterior', 'chevron-left', () => show(index - 1));
        const next = button('Siguiente', 'chevron-right', () => show(index + 1));
        const position = node('span', 'tf-att-position');
        position.setAttribute('aria-live', 'polite');
        const zooms = node('div', 'tf-att-zoom');
        const minus = button('Alejar', 'minus', () => setZoom(zoom / 1.25));
        const reset = button('100 %', 'compress', () => setZoom(1));
        const plus = button('Acercar', 'plus', () => setZoom(zoom * 1.25));
        zooms.append(minus, reset, plus);
        const pages = node('div', 'tf-att-pages');
        const previousPage = button('Página anterior', 'chevron-left', () => { void renderPdfPage(pdfPageNumber - 1); });
        const nextPage = button('Página siguiente', 'chevron-right', () => { void renderPdfPage(pdfPageNumber + 1); });
        const pagePosition = node('span', 'tf-att-position');
        pagePosition.setAttribute('aria-live', 'polite');
        pages.append(previousPage, pagePosition, nextPage);
        toolbar.append(prev, position, next, zooms, pages);
        const status = node('p', 'tf-att-preview-status');
        status.setAttribute('role', 'status');
        status.setAttribute('aria-live', 'polite');
        const body = node('div', 'tf-att-preview-body');
        dialog.append(head, toolbar, status, body);
        let index = entries.indexOf(first), zoom = 1, img = null;
        let objectURL = null, controller = null, revision = 0, closed = false;
        let pdfDocument = null, pdfLoadingTask = null, pdfRenderTask = null;
        let pdfPageNumber = 1, pdfRenderRevision = 0;
        let resizeFrame = 0, observedWidth = 0;
        const resizeObserver = typeof window.ResizeObserver === 'function' ? new window.ResizeObserver(() => {
            const width = body.clientWidth;
            if (width === observedWidth) return;
            observedWidth = width;
            if (!pdfDocument) return;
            window.cancelAnimationFrame(resizeFrame);
            resizeFrame = window.requestAnimationFrame(() => {
                resizeFrame = 0;
                if (!closed && pdfDocument) void renderPdfPage(pdfPageNumber);
            });
        }) : null;

        function release() {
            revision++;
            window.cancelAnimationFrame(resizeFrame);
            resizeFrame = 0;
            if (controller) controller.abort();
            controller = null;
            pdfRenderRevision++;
            if (pdfRenderTask) pdfRenderTask.cancel();
            pdfRenderTask = null;
            const loadingTask = pdfLoadingTask;
            pdfLoadingTask = null;
            pdfDocument = null;
            if (loadingTask) void loadingTask.destroy().catch(() => {});
            body.replaceChildren();
            img = null;
            if (objectURL) window.URL.revokeObjectURL(objectURL);
            objectURL = null;
        }

        function close() {
            if (closed) return;
            closed = true;
            resizeObserver?.disconnect();
            release();
            if (dialog.open) dialog.close();
            dialog.remove();
            if (currentPreview && currentPreview.dialog === dialog) currentPreview = null;
            const focusTarget = opener?.isConnected ? opener : first.element?.querySelector('.tf-att-thumb-preview') || gallery.container.querySelector('.tf-att-action');
            focusTarget?.focus({ preventScroll: true });
        }

        function setZoom(value) {
            zoom = Math.max(0.25, Math.min(4, value));
            reset.querySelector('span').textContent = Math.round(zoom * 100) + ' %';
            minus.disabled = zoom <= 0.25;
            plus.disabled = zoom >= 4;
            if (img) {
                img.style.width = Math.round(zoom * 100) + '%';
                img.style.maxWidth = 'none';
            }
            if (pdfDocument) void renderPdfPage(pdfPageNumber);
        }

        async function renderPdfPage(number) {
            const pdf = pdfDocument;
            if (!pdf || number < 1 || number > pdf.numPages || closed) return;
            const run = ++pdfRenderRevision;
            const previousRender = pdfRenderTask;
            if (previousRender) previousRender.cancel();
            pdfRenderTask = null;
            pdfPageNumber = number;
            previousPage.disabled = number <= 1;
            nextPage.disabled = number >= pdf.numPages;
            pagePosition.textContent = 'Página ' + number + ' de ' + pdf.numPages;
            status.textContent = 'Dibujando página ' + number + '…';
            body.setAttribute('aria-busy', 'true');
            try {
                if (previousRender) await previousRender.promise.catch(() => {});
                if (closed || run !== pdfRenderRevision) return;
                const page = await pdf.getPage(number);
                if (closed || run !== pdfRenderRevision) return;
                const base = page.getViewport({ scale: 1 });
                const availableWidth = Math.max(160, body.clientWidth - 32);
                const viewport = page.getViewport({ scale: (availableWidth / base.width) * zoom });
                // Limit one canvas to 8 million pixels, including high-DPI screens.
                const outputScale = Math.min(window.devicePixelRatio || 1, 2,
                    8192 / viewport.width, 8192 / viewport.height,
                    Math.sqrt(8000000 / (viewport.width * viewport.height)));
                const canvas = node('canvas', 'tf-att-pdf-canvas');
                canvas.width = Math.max(1, Math.floor(viewport.width * outputScale));
                canvas.height = Math.max(1, Math.floor(viewport.height * outputScale));
                canvas.style.width = Math.floor(viewport.width) + 'px';
                canvas.style.height = Math.floor(viewport.height) + 'px';
                canvas.setAttribute('role', 'img');
                canvas.setAttribute('aria-label', entries[index].meta.filename + ', página ' + number + ' de ' + pdf.numPages);
                body.replaceChildren(canvas);
                const task = page.render({
                    canvasContext: canvas.getContext('2d'), viewport,
                    transform: outputScale === 1 ? null : [outputScale, 0, 0, outputScale, 0, 0],
                    annotationMode: 0
                });
                pdfRenderTask = task;
                await task.promise;
                if (!closed && run === pdfRenderRevision) status.textContent = '';
            } catch (error) {
                if (closed || run !== pdfRenderRevision || error.name === 'RenderingCancelledException') return;
                status.textContent = 'No se pudo mostrar esta página. Reintenta o descarga el PDF.';
                body.replaceChildren(button('Reintentar página', 'redo', () => { void renderPdfPage(number); }));
            } finally {
                if (!closed && run === pdfRenderRevision) {
                    pdfRenderTask = null;
                    body.setAttribute('aria-busy', 'false');
                }
            }
        }

        async function show(newIndex) {
            if (newIndex < 0 || newIndex >= entries.length || closed) return;
            release();
            const run = revision;
            index = newIndex;
            const entry = entries[index];
            const meta = entry.meta;
            const kind = entry.file ? localKind(entry.file) : meta.preview_kind;
            filename(title, meta.filename);
            detail.textContent = fileDetail(meta);
            links.replaceChildren();
            const link = (!entry.file || entry.state === 'uploaded') && download(meta);
            if (link) links.append(link);
            prev.disabled = index === 0;
            next.disabled = index === entries.length - 1;
            position.textContent = (index + 1) + ' de ' + entries.length;
            zooms.hidden = kind !== 'image' && kind !== 'pdf';
            pages.hidden = kind !== 'pdf';
            previousPage.disabled = true;
            nextPage.disabled = true;
            pagePosition.textContent = 'Cargando páginas…';
            setZoom(1);
            status.textContent = 'Cargando vista previa…';
            body.setAttribute('aria-busy', 'true');
            controller = new window.AbortController();
            const signal = controller.signal;
            try {
                let blob, data;
                if (entry.file) {
                    if (kind === 'text' || kind === 'csv') {
                        const text = await entry.file.slice(0, MAX_TEXT).text();
                        data = kind === 'csv' ? parseCsv(text) : { kind: 'text', text };
                        data.truncated = data.truncated || entry.file.size > MAX_TEXT;
                    } else {
                        if (entry.file.size > MAX_PREVIEW_BYTES) throw new Error('preview-limit');
                        blob = entry.file;
                    }
                } else {
                    const response = await window.fetch(meta.preview_url, {
                        credentials: 'same-origin', redirect: 'error', cache: 'no-store', signal,
                        headers: { Accept: kind === 'text' || kind === 'csv' ? 'application/json' : kind === 'pdf' ? 'application/pdf' : 'image/*' }
                    });
                    if (!response.ok || (response.url && !safeUrl(response.url, false))) throw new Error('preview-response');
                    const type = (response.headers.get('Content-Type') || '').split(';')[0].trim().toLowerCase();
                    if (kind === 'text' || kind === 'csv') {
                        if (type !== 'application/json') throw new Error('preview-type');
                        data = JSON.parse(new TextDecoder().decode(await boundedBytes(response, MAX_JSON_BYTES)));
                        if (!data || data.kind !== kind) throw new Error('preview-kind');
                    } else {
                        if (kind === 'image' ? !rasterTypes.has(type) : type !== 'application/pdf') throw new Error('preview-type');
                        blob = new Blob([await boundedBytes(response, MAX_PREVIEW_BYTES)], { type });
                    }
                }
                if (closed || run !== revision) return;
                if (data) plainPreview(body, data, kind);
                else {
                    if (kind === 'pdf') {
                        if (!(await blob.slice(0, 1024).text()).includes('%PDF-')) throw new Error('preview-type');
                        if (closed || run !== revision) return;
                        const pdfjs = await import('/static/tickets/vendor/pdfjs/pdf.js');
                        if (closed || run !== revision) return;
                        pdfjs.GlobalWorkerOptions.workerSrc = '/static/tickets/vendor/pdfjs/pdf.worker.js';
                        const data = new Uint8Array(await blob.arrayBuffer());
                        if (closed || run !== revision) return;
                        const assetBase = window.location.origin + '/static/tickets/vendor/pdfjs/';
                        const loadingTask = pdfjs.getDocument({
                            data, isEvalSupported: false, disableFontFace: false, enableXfa: false,
                            standardFontDataUrl: assetBase + 'standard_fonts/',
                            cMapUrl: assetBase + 'cmaps/', cMapPacked: true,
                            wasmUrl: assetBase + 'wasm/'
                        });
                        pdfLoadingTask = loadingTask;
                        const loadedDocument = await loadingTask.promise;
                        if (closed || run !== revision) return;
                        pdfDocument = loadedDocument;
                        await renderPdfPage(1);
                        return;
                    }
                    objectURL = window.URL.createObjectURL(blob);
                    if (kind === 'image') {
                        img = node('img', 'tf-att-preview-image');
                        img.alt = meta.filename;
                        img.addEventListener('error', () => {
                            if (run === revision && !closed) status.textContent = 'No se pudo mostrar la imagen. Puedes descargar el archivo.';
                        }, { once: true });
                        img.src = objectURL;
                        body.append(img);
                        setZoom(1);
                    }
                }
                status.textContent = '';
            } catch (error) {
                if (closed || run !== revision || error.name === 'AbortError') return;
                status.textContent = error.message === 'preview-limit'
                    ? 'El archivo supera el tamaño de vista previa. Puedes descargarlo.'
                    : 'No se pudo abrir la vista previa. Inténtalo de nuevo o descarga el archivo.';
                body.replaceChildren(button('Reintentar vista previa', 'redo', () => show(index)));
            } finally {
                if (!closed && run === revision && !pdfDocument) body.setAttribute('aria-busy', 'false');
            }
        }

        dialog.addEventListener('cancel', event => { event.preventDefault(); close(); });
        dialog.addEventListener('close', close);
        dialog.addEventListener('click', event => { if (event.target === dialog) close(); });
        document.body.append(dialog);
        currentPreview = { gallery, dialog, close };
        if (typeof dialog.showModal !== 'function') {
            close();
            return;
        }
        dialog.showModal();
        observedWidth = body.clientWidth;
        resizeObserver?.observe(body);
        closeButton.focus();
        void show(index);
    }

    function mount(root, options) {
        if (!root || root.nodeType !== 1) throw new TypeError('Se necesita el panel del ticket.');
        if (mounts.has(root)) { hydrate(root); return mounts.get(root); }
        options = options || {};
        const ticketId = options.ticketId == null ? root.dataset.ticketId : options.ticketId;
        const uploadUrl = ticketId ? '/tickets/' + encodeURIComponent(ticketId) + '/attachments/upload/' : '/attachments/drafts/upload/';
        const positive = (value, fallback) => Number.isSafeInteger(Number(value)) && Number(value) > 0 ? Number(value) : fallback;
        const limits = {
            files: positive(root.dataset.attachmentMaxFiles, 5),
            bytes: positive(root.dataset.attachmentMaxBytes, 15 * 1024 * 1024),
            total: positive(root.dataset.attachmentMaxTotalBytes, 25 * 1024 * 1024)
        };
        const extensions = (root.dataset.attachmentAllowedExtensions || '.pdf,.png,.jpg,.jpeg,.gif,.webp,.bmp,.txt,.csv,.doc,.docx,.xls,.xlsx,.zip,.log')
            .split(',').map(ext => ext.trim().toLowerCase()).filter(Boolean).map(ext => ext.startsWith('.') ? ext : '.' + ext);
        const cancelUrl = safeUrl(root.dataset.attachmentCancelUrl || '/attachments/uploads/cancel/', false);
        const attachBtn = root.querySelector('#attach-btn');
        const input = root.querySelector('#attach-input') || root.querySelector('input[type="file"]');
        const pending = root.querySelector('#pending-attachments');
        const composer = root.querySelector('#messageInputArea');
        const unbind = [];
        const originalDisabled = new Map([attachBtn, input].filter(Boolean).map(el => [el, el.disabled]));
        const gallery = { container: pending, entries: [] };
        let items = gallery.entries;
        let draftKey = uuid(), locked = false, disposed = false, disposing = null;
        let active = null, dragDepth = 0;
        let noticeText = '';
        const summary = node('div', 'tf-att-summary');
        summary.setAttribute('role', 'status');
        summary.setAttribute('aria-live', 'polite');
        summary.setAttribute('aria-atomic', 'true');
        const notice = node('p', 'tf-att-notice');
        notice.setAttribute('role', 'alert');
        const list = node('div', 'tf-att-gallery tf-att-pending-list');
        if (pending) {
            pending.classList.add('tf-att-pending');
            pending.replaceChildren(summary, notice, list);
        }
        if (composer) composer.classList.add('tf-att-composer');

        // Imágenes dentro del texto (#21525): se suben como cualquier adjunto y,
        // al terminar, se insertan en el editor donde estaba el cursor en vez de
        // quedarse en la lista. Siguen siendo adjuntos del comentario por debajo.
        function editorQuill() {
            return root.querySelector('#quill-editor')?.__quillComposer?.quill || null;
        }
        function previewPath(item) {
            return '/attachments/' + encodeURIComponent(item.meta.id) + '/preview/';
        }
        function inEditor(item) {
            const quill = editorQuill();
            if (!quill || !item.meta.id) return false;
            return Array.from(quill.root.querySelectorAll('img')).some(img => (img.getAttribute('src') || '').endsWith(previewPath(item)));
        }
        function counted(item) {
            // Una imagen ya insertada solo cuenta mientras siga en el texto.
            return !(item.inline && item.state === 'uploaded') || inEditor(item);
        }
        function insertInline(item) {
            const quill = editorQuill();
            if (!quill || !item.meta.preview_url) { item.inline = false; return; }
            const length = quill.getLength();
            let at = Math.min(Number.isInteger(item.insertAt) ? item.insertAt : length - 1, length - 1);
            // La imagen va en su propia línea y se sigue escribiendo debajo, como
            // al pegar una captura en Outlook o en Word.
            let added = 2;
            if (at > 0 && quill.getText(at - 1, 1) !== '\n') {
                quill.insertText(at, '\n', 'user');
                at += 1;
                added += 1;
            }
            quill.insertEmbed(at, 'image', previewPath(item), 'user');
            quill.insertText(at + 1, '\n', 'user');
            quill.setSelection(at + 2, 0, 'user');
            // Las siguientes imágenes de la misma tanda van detrás de esta.
            items.forEach(other => {
                if (other !== item && other.inline && other.state !== 'uploaded' && Number.isInteger(other.insertAt) && other.insertAt >= at - (added - 2)) other.insertAt += added;
            });
        }
        const originalAccept = input?.getAttribute('accept');
        if (input && extensions.length) input.setAttribute('accept', extensions.join(','));

        function listen(target, type, callback, capture) {
            if (!target) return;
            target.addEventListener(type, callback, capture);
            unbind.push(() => target.removeEventListener(type, callback, capture));
        }

        function emit(name, detail) {
            root.dispatchEvent(new window.CustomEvent('ticketattachments:' + name, { bubbles: true, detail }));
        }

        function report(message) {
            noticeText = message;
            notice.textContent = message;
            notice.hidden = !message;
            if (message) emit('error', { message });
        }

        function csrf() {
            if (typeof options.getCsrfToken === 'function') return options.getCsrfToken() || '';
            return root.querySelector('[name="csrfmiddlewaretoken"]')?.value || '';
        }

        function stateLabel(item) {
            if (item.state === 'queued') return 'En cola';
            if (item.state === 'processing') return 'Procesando…';
            if (item.state === 'uploaded') return item.error || 'Listo';
            if (item.state === 'removing') return 'Quitando…';
            if (item.state === 'canceling') return 'Cancelando…';
            if (item.state === 'cancel-error') return 'Error: no se pudo confirmar la cancelación.';
            if (item.state === 'error') return item.error || 'Error al subir el archivo.';
            return item.total ? 'Subiendo ' + Math.min(100, Math.floor(item.loaded / item.total * 100)) + ' %' : 'Subiendo…';
        }

        function updateCard(item) {
            const old = item.element;
            if (item.inline && item.state === 'uploaded') {
                old?.remove();
                item.element = null;
                return;
            }
            const focused = old && old.contains(document.activeElement) ? document.activeElement.dataset.action : null;
            const view = makeCard(item, gallery);
            item.element = view.card;
            view.card.dataset.state = item.state;
            view.card.dataset.uploadKey = item.key;
            const state = node('div', 'tf-att-state', stateLabel(item));
            state.setAttribute('role', 'status');
            state.setAttribute('aria-live', 'polite');
            state.setAttribute('aria-atomic', 'true');
            view.card.append(state);
            if (['uploading', 'processing'].includes(item.state)) {
                const progress = node('progress', 'tf-att-progress');
                progress.max = item.total || 1;
                if (item.total) progress.value = Math.min(item.loaded, item.total);
                if (item.state === 'processing') progress.value = progress.max;
                progress.setAttribute('aria-label', 'Subida de ' + item.meta.filename);
                progress.setAttribute('aria-valuetext', stateLabel(item));
                view.card.append(progress);
            }
            if (item.state === 'error' && !item.cancelRequested) {
                const retry = button('Reintentar', 'redo', () => {
                    if (locked || disposed || item.state !== 'error') return;
                    item.state = 'queued'; item.error = ''; item.loaded = 0; item.total = 0;
                    changed(item); pump();
                });
                retry.dataset.action = 'retry';
                retry.disabled = locked;
                view.actions.append(retry);
            }
            const removing = ['canceling', 'removing'].includes(item.state);
            const label = item.state === 'cancel-error' ? 'Reintentar cancelación' : ['uploading', 'processing'].includes(item.state) ? 'Cancelar' : 'Quitar';
            const remove = button(label, 'times', () => { if (!locked) void removeItem(item); });
            remove.dataset.action = 'remove';
            remove.setAttribute('aria-label', label + ': ' + item.meta.filename);
            remove.disabled = locked || removing;
            view.actions.append(remove);
            if (old) old.replaceWith(view.card);
            else list.append(view.card);
            if (focused) view.card.querySelector('[data-action="' + focused + '"]')?.focus({ preventScroll: true });
        }

        function totals() { return items.reduce((sum, item) => sum + item.file.size, 0); }

        function changed(item) {
            if (disposed) return;
            if (item) updateCard(item);
            const shown = items.filter(entry => !(entry.inline && entry.state === 'uploaded'));
            const done = shown.filter(entry => entry.state === 'uploaded').length;
            summary.textContent = shown.length
                ? shown.length + (shown.length === 1 ? ' archivo · ' : ' archivos · ') + sizeLabel(totals()) + ' / ' + sizeLabel(limits.total) + ' · ' + done + ' de ' + shown.length + ' listos'
                : '';
            summary.hidden = !shown.length;
            list.hidden = !shown.length;
            notice.hidden = !noticeText;
            pending?.classList.toggle('tf-att-has-files', !!shown.length);
            emit('change', { instance: api, ready: api.ready(), ids: api.ids(), count: items.length, size: totals() });
        }

        function forget(item) {
            if (currentPreview && currentPreview.gallery === gallery) currentPreview.close();
            items = items.filter(entry => entry !== item);
            gallery.entries = items;
            item.element?.remove();
            if (item.thumbURL) window.URL.revokeObjectURL(item.thumbURL);
            item.thumbURL = null;
            changed();
        }

        async function post(url, body, json, missingOkay) {
            if (!url) throw new Error('endpoint');
            const controller = new window.AbortController();
            const timeout = window.setTimeout(() => controller.abort(), 30000);
            try {
                const response = await window.fetch(url, {
                    method: 'POST', credentials: 'same-origin', redirect: 'error',
                    headers: { 'X-CSRFToken': csrf(), ...(json ? { 'Content-Type': 'application/json' } : {}) },
                    body, keepalive: true, signal: controller.signal
                });
                if (!response.ok && !(missingOkay && response.status === 404)) throw new Error('pending-request');
            } finally { window.clearTimeout(timeout); }
        }

        function deleteMeta(item, meta) {
            if (item.published || !meta || meta.id === null) return Promise.resolve();
            return post('/attachments/' + encodeURIComponent(meta.id) + '/pending/delete/', undefined, false, item.cancelRequested);
        }

        async function cancelUpload(item) {
            const body = JSON.stringify({ upload_key: item.key, ...(ticketId ? { ticket_id: Number(ticketId) } : { draft_key: item.draftKey }) });
            await post(cancelUrl, body, true);
            // Handles successful upload responses that raced the cancellation.
            if (item.meta.id !== null) await deleteMeta(item, item.meta);
        }

        async function removeItem(item) {
            if (disposed || !items.includes(item)) return true;
            if (item.removal) return item.removal;
            if (!item.attempted) { forget(item); pump(); return true; }
            const previous = item.state;
            const hasMeta = item.meta.id !== null && !item.cancelRequested;
            item.cancelRequested = !hasMeta;
            item.state = hasMeta ? 'removing' : 'canceling';
            const xhr = item.xhr;
            changed(item);
            if (xhr && !hasMeta) xhr.abort();
            item.removal = (async () => {
                try {
                    if (hasMeta) await deleteMeta(item, item.meta);
                    else await cancelUpload(item);
                    forget(item);
                    return true;
                } catch (_) {
                    item.state = hasMeta ? previous : 'cancel-error';
                    item.error = 'Error: no se pudo quitar. Inténtalo de nuevo.';
                    report(hasMeta ? 'No se pudo quitar el adjunto. Se ha conservado en el borrador.' : 'No se pudo confirmar la cancelación. Reinténtala antes de publicar o cerrar.');
                    changed(item);
                    return false;
                } finally {
                    item.removal = null;
                    pump();
                }
            })();
            return item.removal;
        }

        function pump() {
            if (disposed || disposing || active) return;
            const item = items.find(entry => entry.state === 'queued');
            if (!item) return;
            active = item;
            item.attempted = true;
            item.state = 'uploading';
            item.loaded = 0; item.total = 0;
            const xhr = new window.XMLHttpRequest();
            item.xhr = xhr;
            let finished = false;
            function finish() {
                if (finished) return;
                finished = true;
                if (active === item) active = null;
                if (item.xhr === xhr) item.xhr = null;
                if (items.includes(item)) changed(item);
                pump();
            }
            function fail(message) {
                if (!item.cancelRequested && !item.published && items.includes(item)) {
                    item.state = 'error'; item.error = message;
                }
                finish();
            }
            xhr.upload.addEventListener('progress', event => {
                if (finished || item.cancelRequested || item.published) return;
                item.loaded = event.loaded;
                item.total = event.lengthComputable ? event.total : 0;
                const state = item.total && item.loaded >= item.total ? 'processing' : 'uploading';
                if (item.state !== state) { item.state = state; changed(item); }
                else {
                    // Preserve focused controls and live-region nodes for frequent byte events.
                    const progress = item.element?.querySelector('progress');
                    if (progress && item.total) { progress.max = item.total; progress.value = Math.min(item.loaded, item.total); }
                    if (progress) progress.setAttribute('aria-valuetext', stateLabel(item));
                    const label = item.element?.querySelector('.tf-att-state');
                    if (label && label.textContent !== stateLabel(item)) label.textContent = stateLabel(item);
                }
            });
            xhr.upload.addEventListener('load', () => {
                if (finished || item.cancelRequested || item.published) return;
                item.state = 'processing'; changed(item);
            });
            xhr.addEventListener('load', () => {
                let data;
                try { data = typeof xhr.response === 'object' && xhr.response ? xhr.response : JSON.parse(xhr.responseText); }
                catch (_) { fail('Error: el servidor no confirmó la subida. Reintenta.'); return; }
                if (xhr.status < 200 || xhr.status >= 300 || !safeId(data?.id)) {
                    const message = xhr.status === 413 ? 'Error: el archivo supera el límite del servidor.'
                        : [401, 403].includes(xhr.status) ? 'Error: no se pudo autorizar la subida. Comprueba tu sesión y reintenta.'
                        : xhr.status === 415 ? 'Error: este tipo de archivo no está permitido.'
                        : 'Error al subir el archivo. Reintenta.';
                    fail(message); return;
                }
                const meta = metadata(data);
                if (item.published) { finish(); return; }
                if (item.cancelRequested || disposed || !items.includes(item)) {
                    item.meta = meta;
                    void deleteMeta(item, meta).catch(() => {
                        if (disposed) return;
                        if (!items.includes(item)) { items.push(item); gallery.entries = items; item.element = null; }
                        item.state = 'cancel-error';
                        report('No se pudo retirar una subida cancelada. Reintenta la cancelación.');
                        changed(item);
                    });
                    finish(); return;
                }
                if (finished) return;
                item.meta = meta;
                item.state = 'uploaded';
                item.error = '';
                if (item.inline) insertInline(item);
                finish();
            });
            xhr.addEventListener('error', () => fail('Error de conexión. Reintenta la subida.'));
            xhr.addEventListener('timeout', () => fail('Error: la subida tardó demasiado. Reintenta.'));
            xhr.addEventListener('abort', () => fail('Subida cancelada.'));
            changed(item);
            try {
                xhr.open('POST', uploadUrl, true);
                xhr.timeout = 180000;
                xhr.responseType = 'json';
                xhr.setRequestHeader('X-CSRFToken', csrf());
                const form = new window.FormData();
                form.append('file', item.file, item.file.name);
                if (!ticketId) form.append('draft_key', item.draftKey);
                form.append('upload_key', item.key);
                xhr.send(form);
            } catch (_) { fail('Error: no se pudo iniciar la subida. Reintenta.'); }
        }

        function enqueue(files, inline) {
            if (disposed || locked) return;
            const quill = inline ? editorQuill() : null;
            let insertAt = quill ? (quill.getSelection()?.index ?? quill.getLength() - 1) : null;
            if (!pending) { report('No se encontró el área de adjuntos. Recarga la página.'); return; }
            const rejected = [];
            for (const file of files) {
                if (!(file instanceof window.Blob)) continue;
                const ext = /\.[^.]+$/.exec(file.name.toLowerCase())?.[0] || '';
                if (extensions.length && !extensions.includes(ext)) { rejected.push(file.name + ': tipo de archivo no permitido.'); continue; }
                if (items.length >= limits.files) { rejected.push('Puedes adjuntar hasta ' + limits.files + ' archivos.'); continue; }
                if (file.size > limits.bytes) { rejected.push(file.name + ': máximo ' + sizeLabel(limits.bytes) + ' por archivo.'); continue; }
                if (totals() + file.size > limits.total) { rejected.push('El total no puede superar ' + sizeLabel(limits.total) + '.'); continue; }
                const kind = localKind(file);
                const item = {
                    inline: !!quill && kind === 'image', insertAt: quill && kind === 'image' ? insertAt++ : null,
                    key: uuid(), draftKey, file, state: 'queued', attempted: false, published: false,
                    cancelRequested: false, xhr: null, loaded: 0, total: 0, error: '',
                    meta: metadata({ filename: file.name, size: file.size, file_type: file.type, preview_kind: kind }),
                    thumbURL: kind === 'image' ? window.URL.createObjectURL(file) : null
                };
                items.push(item);
                changed(item);
            }
            report([...new Set(rejected)].join(' '));
            pump();
        }

        function stopFiles(event) {
            event.preventDefault();
            event.stopImmediatePropagation();
        }

        function transferFiles(transfer) {
            if (!transfer) return [];
            const files = Array.from(transfer.files || []);
            return files.length ? files : Array.from(transfer.items || []).filter(item => item.kind === 'file').map(item => item.getAsFile()).filter(Boolean);
        }

        function isFileDrag(event) {
            return event.dataTransfer && (Array.from(event.dataTransfer.types || []).includes('Files') || transferFiles(event.dataTransfer).length > 0);
        }

        function confirmClose() {
            if (locked) { report('Espera a que termine la publicación antes de cerrar este ticket.'); return false; }
            if (!items.length) return true;
            return window.confirm('Hay adjuntos sin publicar en este ticket. ¿Quieres descartarlos y cerrar?');
        }

        const api = {
            hasFiles: () => items.some(counted),
            ready: () => items.every(item => item.state === 'uploaded' && !item.cancelRequested),
            ids: () => items.filter(item => item.state === 'uploaded' && !item.cancelRequested && counted(item)).map(item => item.meta.id),
            get draftKey() { return draftKey; },
            clear() {
                if (disposed) return false;
                if (!api.ready()) { report('Espera a que todos los adjuntos estén listos antes de publicar.'); return false; }
                // No deletion, cancellation request, or abort on a published batch.
                items.slice().forEach(item => { item.published = true; forget(item); });
                draftKey = uuid();
                report(''); changed();
                return true;
            },
            lock(value) {
                if (disposed) return;
                locked = !!value;
                originalDisabled.forEach((disabled, el) => { el.disabled = locked || disabled; });
                root.classList.toggle('tf-att-locked', locked);
                items.forEach(updateCard);
                if (locked) composer?.classList.remove('tf-att-dragover');
            },
            dispose() {
                if (disposed) return Promise.resolve(true);
                if (disposing) return disposing;
                const wasLocked = locked;
                api.lock(true);
                // Defer cleanup one microtask so abort callbacks cannot start queued files.
                disposing = Promise.resolve().then(async () => {
                    const results = await Promise.all(items.slice().map(removeItem));
                    if (results.some(result => !result)) {
                        disposing = null;
                        api.lock(wasLocked);
                        return false;
                    }
                    if (currentPreview && (currentPreview.gallery === gallery || root.contains(currentPreview.gallery.container))) currentPreview.close();
                    disposed = true;
                    unbind.forEach(fn => fn());
                    originalDisabled.forEach((disabled, el) => { el.disabled = disabled; });
                    if (input) {
                        if (originalAccept === null) input.removeAttribute('accept');
                        else input.setAttribute('accept', originalAccept);
                    }
                    composer?.classList.remove('tf-att-composer', 'tf-att-dragover');
                    root.classList.remove('tf-att-locked');
                    pending?.classList.remove('tf-att-pending', 'tf-att-has-files');
                    pending?.replaceChildren();
                    mounts.delete(root);
                    emit('disposed', {});
                    return true;
                });
                return disposing;
            }
        };

        mounts.set(root, api);
        hydrate(root);
        listen(root, 'click', event => {
            if (!attachBtn || !attachBtn.contains(event.target)) return;
            stopFiles(event);
            if (!locked && input && !input.disabled) input.click();
        }, true);
        listen(root, 'change', event => {
            if (event.target !== input) return;
            stopFiles(event);
            enqueue(Array.from(input.files || []));
            input.value = '';
        }, true);
        // Capture on the pane, before Quill's editor listeners; inspect the REAL
        // .ql-editor at event time, including an editor initialized after mount.
        listen(root, 'paste', event => {
            const editor = event.target.closest?.('.ql-editor');
            if (!editor || !root.contains(editor) || (composer && !composer.contains(editor))) return;
            const files = transferFiles(event.clipboardData);
            if (!files.length) return;
            stopFiles(event);
            enqueue(files, true);
        }, true);
        listen(composer, 'dragenter', event => {
            if (!isFileDrag(event)) return;
            stopFiles(event);
            dragDepth++;
            if (!locked) composer.classList.add('tf-att-dragover');
        }, true);
        listen(composer, 'dragover', event => {
            if (!isFileDrag(event)) return;
            stopFiles(event);
            event.dataTransfer.dropEffect = locked ? 'none' : 'copy';
        }, true);
        listen(composer, 'dragleave', event => {
            if (!isFileDrag(event)) return;
            event.stopPropagation();
            dragDepth = Math.max(0, dragDepth - 1);
            if (!dragDepth) composer.classList.remove('tf-att-dragover');
        }, true);
        listen(composer, 'drop', event => {
            if (!isFileDrag(event)) return;
            stopFiles(event);
            dragDepth = 0;
            composer.classList.remove('tf-att-dragover');
            // Soltar sobre el texto inserta las imágenes ahí; fuera, son adjuntos.
            enqueue(transferFiles(event.dataTransfer), !!event.target.closest?.('.ql-editor'));
        }, true);
        listen(window, 'beforeunload', event => {
            if (!items.length) return;
            event.preventDefault();
            event.returnValue = '';
        });
        listen(root, 'tf:before-pane-close', event => {
            if (event.target !== root || event.defaultPrevented) return;
            if (!confirmClose()) { event.preventDefault(); return; }
            if (typeof event.detail?.waitUntil === 'function') {
                // Other synchronous pane guards may still reject this same event.
                event.detail.waitUntil(Promise.resolve().then(() => event.defaultPrevented ? false : api.dispose()));
            }
            else if (items.length) {
                event.preventDefault();
                report('No se pudo preparar el cierre seguro. Recarga la página o quita los adjuntos antes de cerrar.');
            }
        });
        listen(root, 'tf:pane-dispose', event => { if (event.target === root) void api.dispose(); });
        changed();
        return api;
    }

    window.TicketAttachments = Object.freeze({ mount, render });
})(window);
