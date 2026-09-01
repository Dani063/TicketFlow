/* ===== Tabs manager (reworked) ===== */
(function () {
    // Evita doble inicialización si el script se carga dos veces por error
    if (window.Tabs && window.Tabs.__initialized) return;

    // The URL of the page that loaded as the outer shell (deep-link).
    const _realPageUrl = (window.location.pathname + window.location.search);

    // ── SPA shell visibility ─────────────────────────────────────────────────
    // The app has two top-level content containers:
    //   #app-content — sidebar pages (home, tickets list, customers, profile)
    //   #workspace   — ticket panes (each ticket lives here, kept mounted)
    // Only one is visible at a time. Switching between them is just toggling
    // the `hidden` attribute — keeps panes alive across sidebar navigation.

    function _getAppContent() { return document.getElementById('app-content'); }

    function _showWorkspace() {
        const ws = _getWorkspace();
        const ac = _getAppContent();
        if (ws) ws.hidden = false;
        if (ac) ac.hidden = true;
    }

    function _showAppContent() {
        const ws = _getWorkspace();
        const ac = _getAppContent();
        if (ws) ws.hidden = true;
        if (ac) ac.hidden = false;
    }

    // Patch document.addEventListener once: handlers registered for
    // DOMContentLoaded AFTER the page has already loaded (which happens for
    // every script inside a SPA-fetched fragment) get invoked on the next
    // microtask instead of being silently dropped. Without this patch, every
    // page's `document.addEventListener('DOMContentLoaded', ...)` block stops
    // working when reached via sidebar SPA navigation.
    (function _patchDCL() {
        if (window._tfDclPatched) return;
        window._tfDclPatched = true;
        const _orig = document.addEventListener.bind(document);
        document.addEventListener = function (type, handler, ...rest) {
            if (type === 'DOMContentLoaded' && document.readyState !== 'loading' && typeof handler === 'function') {
                // Already past DOMContentLoaded — schedule handler on next tick.
                Promise.resolve().then(() => {
                    try { handler(new Event('DOMContentLoaded')); }
                    catch (e) { console.error('[tabs] DOMContentLoaded handler threw', e); }
                });
                return;
            }
            return _orig(type, handler, ...rest);
        };
    })();

    // Wrap an inline-script source in an IIFE that:
    //   1. Isolates top-level `let`/`const` so re-execution doesn't throw
    //      "Identifier already declared" (the bug that broke /tickets/ table).
    //   2. Auto-exposes top-level `function NAME(...)` declarations to `window`
    //      so inline `onclick="NAME(...)"` handlers in HTML still resolve.
    function _wrapScriptInIIFE(text) {
        if (!text) return '';
        // Find top-level function declarations: at column 0 (after optional whitespace),
        // not inside another function. This is approximate but matches the common case
        // of inline page scripts that define standalone helpers.
        const fnRegex = /(^|\n)[ \t]*function\s+([A-Za-z_$][\w$]*)\s*\(/g;
        const names = new Set();
        let m;
        while ((m = fnRegex.exec(text)) !== null) names.add(m[2]);
        const exports = [...names]
            .map(n => `try { if (typeof ${n} === 'function') window.${n} = ${n}; } catch(_) {}`)
            .join('\n');
        return '(function(){\n' + text + '\n;' + exports + '\n})();';
    }

    async function _runFragmentScripts(scripts, label) {
        console.debug('[tabs] re-executing', scripts.length, 'scripts for', label);
        for (let idx = 0; idx < scripts.length; idx += 1) {
            const s = scripts[idx];
            try {
                await new Promise((resolve, reject) => {
                    const ns = document.createElement('script');
                    if (s.src) {
                        ns.src = s.src;
                        ns.onload = () => { ns.remove(); resolve(); };
                        ns.onerror = () => { ns.remove(); reject(new Error(`No se pudo cargar ${s.src}`)); };
                        document.head.appendChild(ns);
                        return;
                    }
                    ns.textContent = _wrapScriptInIIFE(s.textContent);
                    document.head.appendChild(ns);
                    ns.remove();
                    resolve();
                });
                console.debug('[tabs]   script', idx, 'executed OK');
            } catch (e) {
                console.error('[tabs]   script', idx, 'threw:', e);
            }
        }
    }

    async function _navigateAppContent(url) {
        const ac = _getAppContent();
        if (!ac) {
            _showNavLoading();
            window.location.href = url;
            return;
        }
        _showNavLoading();
        try {
            const sep = url.includes('?') ? '&' : '?';
            const res = await fetch(url + sep + 'fragment=1', {
                credentials: 'same-origin',
                headers: { 'X-Requested-With': 'XMLHttpRequest' },
            });
            if (!res.ok) throw new Error('Fragment fetch failed: ' + res.status);
            const html = await res.text();

            // Extract scripts BEFORE setting innerHTML (which doesn't execute
            // them), so we can re-execute them with the page DOM in place.
            const tpl = document.createElement('template');
            tpl.innerHTML = html;
            const scripts = [...tpl.content.querySelectorAll('script')];
            scripts.forEach(s => s.remove());

            ac.innerHTML = '';
            ac.appendChild(tpl.content);

            _showAppContent();
            history.pushState({ appContentUrl: url }, '', url);

            // Execute the page's inline scripts. The DCL patch above ensures
            // their `document.addEventListener('DOMContentLoaded', cb)` calls
            // still invoke cb (asynchronously, on next microtask).
            //
            // CRITICAL: wrap each inline script in an IIFE. Top-level `let`/`const`
            // in classic scripts create bindings in the global declarative
            // environment that CANNOT be re-declared. Without the IIFE, re-running
            // the same fragment script throws "Identifier 'X' has already been
            // declared" SyntaxError → DCL handler never runs → page broken.
            //
            // We auto-expose top-level `function` declarations to window so inline
            // event handlers like onclick="toggleSort('id')" still work.
            await _runFragmentScripts(scripts, url);

            // Highlight active sidebar item
            document.querySelectorAll('.sidebar-navigation li').forEach(li => li.classList.remove('active'));
            document.querySelectorAll('.sidebar-navigation a[href]').forEach(a => {
                try {
                    const aPath = new URL(a.getAttribute('href'), window.location.origin).pathname;
                    if (aPath === window.location.pathname) {
                        const li = a.closest('li');
                        if (li) li.classList.add('active');
                    }
                } catch (e) { /* ignore invalid hrefs */ }
            });

            // Per-page hooks: refresh dynamic UI that lives outside #app-content
            // and was wired up by tabs.js itself on first load.
            const path = window.location.pathname;
            if (path === '/' || path === '') {
                // Home page: refresh the "recent activity" column in the right rail
                if (typeof refreshUpdates === 'function') refreshUpdates();
                if (typeof updateBadge === 'function') updateBadge();
            }
        } catch (err) {
            console.error('[tabs] sidebar fragment fetch failed; falling back to full nav', err);
            window.location.href = url;
        } finally {
            _hideNavLoading();
        }
    }
    // ─────────────────────────────────────────────────────────────────────────

    // ── Pane cache (SPA-style fragment tabs) ─────────────────────────────────
    // When window.TF_USE_PANES === true, tab clicks fetch /tickets/N/?fragment=1
    // and swap the .ticket-pane div in #workspace instead of reloading the page.
    // Each visited tab keeps its DOM mounted (display:none when inactive) so
    // subsequent switches are instant — no fetch, no widget re-init.
    const _paneCache = new Map();  // normUrl -> { $pane, ticketId, lastUsed }

    function _panesEnabled() { return window.TF_USE_PANES === true; }

    function _getWorkspace() { return document.getElementById('workspace'); }

    // Detach (preserve in JS, remove from DOM) all panes in the workspace that
    // are NOT paneEl. This is critical because panes can share the same set
    // of IDs (#empresa, #solicitante, #tags, ...) — keeping multiple panes
    // mounted simultaneously creates duplicate-ID collisions that break
    // Select2 widget anchoring on the second pane onwards.
    //
    // jQuery's .detach() preserves attached data and event handlers, so the
    // Select2/Quill instances stay alive on the detached pane and re-attach
    // when the pane is re-appended on its tab being clicked again.
    function _detachInactivePanes(activePane) {
        const ws = _getWorkspace();
        if (!ws || !window.jQuery) return;
        ws.querySelectorAll(':scope > .ticket-pane, :scope > .profile-pane').forEach(p => {
            if (p !== activePane) {
                // Keep the cache entry — only remove from DOM. The reference
                // in _paneCache.$pane is still valid; appendChild re-mounts it.
                window.jQuery(p).detach();
            }
        });
    }

    function _showPane(paneEl) {
        const ws = _getWorkspace();
        if (!ws) return;
        // First, detach any other panes so we never have duplicate IDs live.
        _detachInactivePanes(paneEl);
        const wasDetached = !ws.contains(paneEl);
        // Re-attach paneEl if it was detached on a previous switch.
        if (wasDetached) {
            ws.appendChild(paneEl);
        }
        // Make sure no leftover hidden-class lingers from older versions of this code.
        paneEl.classList.remove('tf-pane-hidden');
        // Select2 widget DOM survives jQuery.detach() but its internal state
        // does not always render correctly after re-attach. Force a destroy +
        // re-init so the widget repaints from scratch. Cheap (~5ms) and
        // guaranteed-correct.
        if (paneEl.classList.contains('ticket-pane') && wasDetached && typeof window.tfApplyPaneSelect2 === 'function') {
            try { window.tfApplyPaneSelect2(paneEl, { force: true }); }
            catch (e) { console.error('[tabs] Select2 re-init failed on re-attach', e); }
        }
        // Rebind window.QuillComposer to this pane's instance (idempotent;
        // if the pane never had Quill it inits now, otherwise it's a no-op).
        if (paneEl.classList.contains('ticket-pane') && window.QuillComposer && typeof window.QuillComposer.initFor === 'function') {
            try { window.QuillComposer.initFor(paneEl); }
            catch (e) { console.error('[tabs] Quill re-bind failed on re-attach', e); }
        }
    }

    function _getOrCreatePaneSpinner() {
        const ws = _getWorkspace();
        if (!ws) return null;
        let s = ws.querySelector(':scope > .tf-pane-spinner');
        if (!s) {
            s = document.createElement('div');
            s.className = 'tf-pane-spinner';
            s.innerHTML = '<div class="tf-frame-spinner-dot"></div>';
            ws.appendChild(s);
        }
        return s;
    }

    async function _fetchAndMountPane(rawUrl, normUrl) {
        const ws = _getWorkspace();
        if (!ws) throw new Error('No #workspace element');

        const spinner = _getOrCreatePaneSpinner();
        if (spinner) spinner.style.display = 'flex';
        // Detach (preserve in JS, remove from DOM) any panes currently mounted —
        // we want exactly ONE pane in the DOM at any time to avoid duplicate-ID
        // collisions on Select2/Quill widgets.
        _detachInactivePanes(null);

        try {
            const sep = rawUrl.includes('?') ? '&' : '?';
            const res = await fetch(rawUrl + sep + 'fragment=1', {
                credentials: 'same-origin',
                headers: { 'X-Requested-With': 'XMLHttpRequest' },
            });
            if (!res.ok) throw new Error('Fragment fetch failed: ' + res.status);
            const html = await res.text();

            const tpl = document.createElement('template');
            tpl.innerHTML = html.trim();
            const paneEl = tpl.content.querySelector('.ticket-pane, .profile-pane');
            if (!paneEl) throw new Error('No compatible pane in fragment response');
            const isTicketPane = paneEl.classList.contains('ticket-pane');
            const scripts = isTicketPane ? [] : [...tpl.content.querySelectorAll('script')];

            // Ticket scripts are legacy globals and ticket initialization is
            // handled explicitly below. Profile scripts are re-entrant and do
            // need to run after their fragment has been mounted.
            // path to set window.ticketId / window.urls — both now passed via
            // ctx parameter to initTicketPane. Re-executing them was causing
            // global state thrash that broke widget init across panes.
            if (isTicketPane) paneEl.querySelectorAll('script').forEach(s => s.remove());
            else scripts.forEach(s => s.remove());

            ws.appendChild(paneEl);

            const rawTid = paneEl.getAttribute('data-ticket-id');
            const ticketId = rawTid && rawTid !== 'null' ? (parseInt(rawTid, 10) || rawTid) : null;
            _paneCache.set(normUrl, {
                $pane: paneEl,
                ticketId,
                paneType: isTicketPane ? 'ticket' : 'profile',
                lastUsed: Date.now(),
            });

            const paneTitle = (paneEl.dataset.tabTitle || '').trim();
            const tab = findTabByNorm(normUrl);
            if (paneTitle && tab) {
                const text = tab.querySelector('.tab-text');
                if (text) {
                    text.textContent = paneTitle;
                    text.title = paneTitle;
                }
                saveTabs();
                _layoutTabs();
            }

            if (!isTicketPane) {
                paneEl.dataset.tfInitialized = '1';
                await _runFragmentScripts(scripts, rawUrl);
                return paneEl;
            }

            // Mark as initialized to prevent the auto-bootstrap in create_ticket.js
            // from re-initializing this pane on a future DOMContentLoaded event.
            paneEl.dataset.tfInitialized = '1';

            if (typeof window.initTicketPane !== 'function') {
                console.error('[tabs] initTicketPane is not loaded — pane will be unstyled. ' +
                              'create_ticket.js must be loaded before fragment panes are mounted.');
                return paneEl;
            }
            try {
                console.debug('[tabs] initTicketPane on fragment pane', { ticketId, normUrl });
                window.initTicketPane(window.jQuery ? window.jQuery(paneEl) : paneEl, {
                    ticketId: ticketId,
                    currentUserId: window.currentUserId,
                    addCommentUrl: ticketId ? ('/tickets/' + ticketId + '/add_comment/') : null,
                    mergeTicketUrl: ticketId ? ('/tickets/' + ticketId + '/merge/') : null,
                    searchUrl: '/search/',
                });
                console.debug('[tabs] pane initialized', ticketId);
            } catch (e) {
                console.error('[tabs] initTicketPane THREW on fragment pane', e);
                console.error('[tabs] pane HTML preview:', paneEl.outerHTML.slice(0, 300));
                throw e;
            }

            return paneEl;
        } finally {
            if (spinner) spinner.style.display = 'none';
        }
    }

    async function openOrSwitchPane(rawUrl) {
        const normUrl = normalizeUrl(rawUrl);
        // Switching to a ticket: hide #app-content, show #workspace
        _showWorkspace();
        const cached = _paneCache.get(normUrl);
        // Cache HIT covers both "still mounted" and "detached but JS-retained".
        // _showPane will re-append a detached pane to the workspace. Select2/Quill
        // state survives detach() because jQuery preserves attached data.
        if (cached && cached.$pane) {
            cached.lastUsed = Date.now();
            _showPane(cached.$pane);
            return cached.$pane;
        }
        const paneEl = await _fetchAndMountPane(rawUrl, normUrl);
        _showPane(paneEl);
        return paneEl;
    }
    // ─────────────────────────────────────────────────────────────────────────

    const qs = () => window.location.pathname + window.location.search;
    function isTabUrl(u) {
        if (!window.urls) return false;
        const norm = normalizeUrl(u);

        // Tickets
        if (window.urls.create_ticket) {
            const normCreate = normalizeUrl(window.urls.create_ticket);
            if (norm.startsWith(normCreate)) return true;
        }

        // Customers
        if (window.urls.customer_profile) {
            const normCustomer = normalizeUrl(window.urls.customer_profile);
            if (norm.startsWith(normCustomer)) return true;
        }

        return false;
    }

    function normalizeUrl(u) {
        try {
            if (u == null || u === '') return '';
            const url = new URL(String(u), window.location.origin);
            const path = url.pathname;
            const params = [...url.searchParams.entries()].sort((a, b) =>
                a[0] === b[0] ? (a[1] > b[1] ? 1 : -1) : (a[0] > b[0] ? 1 : -1)
            );
            const qs = params.map(([k, v]) => `${k}=${encodeURIComponent(v)}`).join('&');
            return qs ? `${path}?${qs}` : path;
        } catch (err) {
            console.warn('[tabs] normalizeUrl invalid:', u, err);
            return String(u || '');
        }
    }

    function getContainer() {
        return document.querySelector('.navbar-left');
    }

    // ── Navigation loading overlay ───────────────────────────────────────────
    // Shown when the user clicks a tab or opens a new ticket. Since the
    // navigation is a full page reload (~2-3s), without this the page just
    // freezes silently. The overlay disappears automatically when the new
    // page renders (its DOM replaces the current one).
    function _showNavLoading() {
        let o = document.getElementById('tf-nav-loading');
        if (!o) {
            o = document.createElement('div');
            o.id = 'tf-nav-loading';
            o.innerHTML = '<div class="tf-frame-spinner-dot"></div>';
            document.body.appendChild(o);
        }
        o.style.display = 'flex';
    }
    function _hideNavLoading() {
        const o = document.getElementById('tf-nav-loading');
        if (o) o.style.display = 'none';
    }
    // Hide it if the browser uses bfcache (back/forward navigation) and we
    // return to this page — otherwise the overlay would still be visible.
    window.addEventListener('pageshow', _hideNavLoading);

    // Intercept sidebar navigation clicks (Home, Vistas, Clientes, etc.) and
    // route them through the SPA fragment fetcher instead of doing a full page
    // reload. This keeps #workspace alive — open ticket tabs survive the nav.
    document.addEventListener('click', (e) => {
        const a = e.target.closest('.sidebar-navigation a[href]');
        if (!a) return;
        const href = a.getAttribute('href');
        if (!href || href.startsWith('#') || a.target === '_blank' || e.ctrlKey || e.metaKey || e.shiftKey) return;
        const target = normalizeUrl(href);
        const current = normalizeUrl(window.location.pathname + window.location.search);
        if (target === current) {
            // Same page: if user is currently viewing a ticket pane, just go
            // back to the app-content view.
            e.preventDefault();
            _showAppContent();
            return;
        }
        // Only SPA-route when we have an #app-content container to swap into
        if (!_panesEnabled() || !_getAppContent()) return;  // fall through to default navigation
        e.preventDefault();
        _navigateAppContent(href);
    });

    function findTabByNorm(norm) {
        const container = getContainer();
        if (!container) return null;
        return [...container.querySelectorAll('.tab')]
            .find(t => normalizeUrl(t.dataset.url || '') === norm) || null;
    }

    function dedupeDomTabs() {
        const container = getContainer();
        if (!container) return;
        const seen = new Set();
        [...container.querySelectorAll('.tab')].forEach(tab => {
            const norm = normalizeUrl(tab.dataset.url || '');
            if (seen.has(norm)) {
                tab.remove();
            } else {
                seen.add(norm);
            }
        });
    }

    // ── Tab overflow ("···") ────────────────────────────────────────────────
    function _getOverflowBtn() {
        const container = getContainer();
        return container ? container.querySelector('.tabs-overflow-btn') : null;
    }

    function _getOverflowPopup() {
        return document.getElementById('tabsOverflowPopup');
    }

    // Recompute which tabs fit in .navbar-left vs which go to the "···" menu.
    // Called after add/close/load/ensureCurrent, on window resize, and when a
    // tab becomes active (so the active one is never hidden in overflow).
    function _layoutTabs() {
        const container = getContainer();
        const overflowBtn = _getOverflowBtn();
        const addBtn = container ? container.querySelector('.add-tab') : null;
        if (!container || !overflowBtn || !addBtn) return;

        const tabs = [...container.querySelectorAll('.tab')];
        tabs.forEach(t => t.classList.remove('tf-overflowed'));
        if (!tabs.length) {
            overflowBtn.hidden = true;
            _renderOverflowList([]);
            return;
        }

        // Show the overflow button briefly so its width counts in measurements.
        overflowBtn.hidden = false;
        const badgeEl = overflowBtn.querySelector('.badge');
        if (badgeEl) badgeEl.textContent = '0';

        const avail = container.clientWidth - addBtn.offsetWidth - overflowBtn.offsetWidth - 8;

        // First pass: do they all fit?
        const total = tabs.reduce((s, t) => s + t.offsetWidth + 10, 0);
        if (total <= avail) {
            overflowBtn.hidden = true;
            _renderOverflowList([]);
            return;
        }

        // Overflow needed. Priority: active > most recent (DOM order reverse).
        const active = container.querySelector('.tab.active');
        const ordered = [...tabs].sort((a, b) => {
            if (a === active) return -1;
            if (b === active) return 1;
            return tabs.indexOf(b) - tabs.indexOf(a);
        });

        let used = 0;
        const keep = new Set();
        for (const t of ordered) {
            const w = t.offsetWidth + 10;
            if (used + w > avail) break;
            keep.add(t);
            used += w;
        }

        const overflowed = tabs.filter(t => !keep.has(t));
        overflowed.forEach(t => t.classList.add('tf-overflowed'));
        overflowBtn.hidden = overflowed.length === 0;
        if (badgeEl) badgeEl.textContent = String(overflowed.length);
        _renderOverflowList(overflowed);
    }

    function _renderOverflowList(overflowed) {
        const popup = _getOverflowPopup();
        if (!popup) return;
        popup.innerHTML = '';
        overflowed.forEach(tab => {
            const item = document.createElement('div');
            item.className = 'tabs-overflow-item';
            if (tab.classList.contains('active')) item.classList.add('active');
            item.setAttribute('role', 'menuitem');

            const icon = document.createElement('img');
            icon.className = 'item-icon';
            icon.alt = '';
            icon.src = (window.static_urls && window.static_urls.logo) || '';

            const text = document.createElement('span');
            text.className = 'item-text';
            const txt = tab.querySelector('.tab-text')?.textContent || '';
            text.textContent = txt;
            text.title = txt;

            const closeBtn = document.createElement('button');
            closeBtn.type = 'button';
            closeBtn.className = 'item-close';
            closeBtn.title = 'Cerrar';
            closeBtn.setAttribute('aria-label', 'Cerrar pestaña');
            closeBtn.textContent = '×';

            item.appendChild(icon);
            item.appendChild(text);
            item.appendChild(closeBtn);

            item.addEventListener('click', (e) => {
                if (e.target.closest('.item-close')) return;
                // Promote: move tab to end of bar (just before the overflow btn).
                const overflowBtn = _getOverflowBtn();
                const container = getContainer();
                if (overflowBtn && container) {
                    container.insertBefore(tab, overflowBtn);
                }
                _hideOverflowPopup();
                tab.click();          // delegate to the real tab click handler
                _layoutTabs();
                saveTabs();
            });

            closeBtn.addEventListener('click', (e) => {
                e.stopPropagation();
                // Reuse the close handler already wired in addTab()
                tab.querySelector('.close-tab')?.click();
                _layoutTabs();
            });

            popup.appendChild(item);
        });
    }

    function _positionOverflowPopup() {
        const btn = _getOverflowBtn();
        const popup = _getOverflowPopup();
        if (!btn || !popup) return;
        const rect = btn.getBoundingClientRect();
        const top = rect.bottom + 6;
        const left = Math.max(8, Math.min(window.innerWidth - 288, rect.right - 280));
        popup.style.top = top + 'px';
        popup.style.left = left + 'px';
    }

    function _showOverflowPopup() {
        const btn = _getOverflowBtn();
        const popup = _getOverflowPopup();
        if (!btn || !popup) return;
        _positionOverflowPopup();
        popup.hidden = false;
        btn.setAttribute('aria-expanded', 'true');
    }

    function _hideOverflowPopup() {
        const btn = _getOverflowBtn();
        const popup = _getOverflowPopup();
        if (popup) popup.hidden = true;
        if (btn) btn.setAttribute('aria-expanded', 'false');
    }
    // ─────────────────────────────────────────────────────────────────────────

    function saveTabs() {
        const container = getContainer();
        if (!container) return;
        const raw = [...container.querySelectorAll('.tab')].map(t => ({
            text: t.querySelector('.tab-text')?.innerText || '',
            url: t.dataset.url
        }));
        // dedupe por URL normalizada
        const map = new Map();
        raw.forEach(t => map.set(normalizeUrl(t.url), t));
        localStorage.setItem('tabs', JSON.stringify([...map.values()]));
    }

    function setActive(tabEl) {
        document.querySelectorAll('.navbar-left .tab').forEach(t => t.classList.remove('active'));
        if (tabEl) {
            tabEl.classList.add('active');
            // If the newly-active tab was hidden in overflow, recompute layout
            // so it becomes visible (active is always prioritized in _layoutTabs).
            if (tabEl.classList.contains('tf-overflowed')) _layoutTabs();
        }
    }

    function addTab(text, url, select = true) {
        if (!isTabUrl(url)) {
            // No crear pestañas para páginas del sidebar; simplemente navegar (o no hacer nada si ya estás ahí)
            const curr = normalizeUrl(window.location.pathname + window.location.search);
            const target = normalizeUrl(url);
            if (curr !== target) { _showNavLoading(); window.location.href = url; }
            return null;
        }
        const container = getContainer();
        if (!container) return null;

        const targetNorm = normalizeUrl(url);
        const currentNorm = normalizeUrl(qs());

        // Si ya existe, activar y navegar solo si hace falta
        const existing = findTabByNorm(targetNorm);
        if (existing) {
            setActive(existing);
            if (select && currentNorm !== targetNorm) {
                if (_panesEnabled() && _getWorkspace()) {
                    openOrSwitchPane(existing.dataset.url).then(() => {
                        history.pushState({ paneUrl: existing.dataset.url }, '', existing.dataset.url);
                    }).catch(err => {
                        console.error('[tabs] pane switch failed', err);
                        _showNavLoading();
                        window.location.href = existing.dataset.url;
                    });
                } else {
                    _showNavLoading();
                    window.location.href = existing.dataset.url;
                }
            }
            return existing;
        }

        // Crear DOM
        const tab = document.createElement('div');
        tab.className = 'tab';
        tab.dataset.url = url;

        const img = document.createElement('img');
        img.src = (window.static_urls && window.static_urls.logo) || '';
        img.alt = 'Icono';
        img.className = 'tab-icon';

        const tabText = document.createElement('span');
        tabText.className = 'tab-text';
        tabText.innerText = text;

        const closeButton = document.createElement('button');
        closeButton.type = 'button';
        closeButton.innerText = '×';
        closeButton.className = 'close-tab';
        closeButton.setAttribute('aria-label', 'Cerrar pestaña ' + text);

        closeButton.addEventListener('click', (e) => {
            e.stopPropagation();
            const wasActive = tab.classList.contains('active');
            const container = getContainer();
            const siblings = container ? [...container.querySelectorAll('.tab')] : [];
            const idx = siblings.indexOf(tab);
            const closedNorm = normalizeUrl(tab.dataset.url || '');

            tab.remove();
            saveTabs();
            _layoutTabs();

            // Clean up cached pane for this tab: destroy widgets before removing
            // the DOM so Select2 / Quill release their event listeners and memory.
            const cachedPane = _paneCache.get(closedNorm);
            if (cachedPane && cachedPane.$pane) {
                const $p = window.jQuery ? window.jQuery(cachedPane.$pane) : null;
                try {
                    if ($p) $p.find('select.select2').each(function () {
                        if ($p.data('select2')) $(this).select2('destroy');
                    });
                } catch (e) { /* tolerate per-instance failures */ }
                if (cachedPane.$pane.parentNode) {
                    cachedPane.$pane.parentNode.removeChild(cachedPane.$pane);
                }
            }
            _paneCache.delete(closedNorm);

            // Si no era la activa, no navegamos (solo quitar del DOM)
            if (!wasActive) return;

            // Elegimos la vecina lógica para activar si cerramos la activa
            const fallback = siblings[idx + 1] || siblings[idx - 1] || null;
            if (fallback) {
                setActive(fallback);
                fallback.click();  // re-use the click handler
            } else if (window.urls && window.urls.tickets_list) {
                if (normalizeUrl(qs()) !== normalizeUrl(window.urls.tickets_list)) {
                    _showNavLoading();
                    window.location.href = window.urls.tickets_list;
                }
            }
        });

        tab.appendChild(img);
        tab.appendChild(tabText);
        tab.appendChild(closeButton);

        // Note: tab click + close handlers are added below; the actual DOM
        // insertion happens via `container.insertBefore(tab, overflowBtn)`
        // a few lines down so the "···" button always stays at the end.

        tab.addEventListener('click', () => {
            const targetNorm = normalizeUrl(tab.dataset.url);
            const currentNorm = normalizeUrl(qs());
            setActive(tab);

            if (currentNorm === targetNorm) return;  // already showing

            // Pane-based routing (active when window.TF_USE_PANES === true)
            if (_panesEnabled() && _getWorkspace()) {
                openOrSwitchPane(tab.dataset.url).then(() => {
                    history.pushState({ paneUrl: tab.dataset.url }, '', tab.dataset.url);
                }).catch(err => {
                    console.error('[tabs] pane switch failed, falling back to navigation', err);
                    _showNavLoading();
                    window.location.href = tab.dataset.url;
                });
                return;
            }

            // Default: full page navigation. The iframe path was disabled because
            // Django sends X-Frame-Options: DENY, which blocks all iframe loads.
            _showNavLoading();
            window.location.href = tab.dataset.url;
        });

        const overflowBtn = _getOverflowBtn();
        if (overflowBtn && overflowBtn.parentNode === container) {
            container.insertBefore(tab, overflowBtn);
        } else {
            container.appendChild(tab);
        }
        dedupeDomTabs();   // asegurar 1 sola por URL
        saveTabs();
        _layoutTabs();

        // Seleccionar (pero sin duplicar navegación)
        if (select) {
            setActive(tab);
            const targetNorm2 = normalizeUrl(url);
            if (normalizeUrl(qs()) !== targetNorm2) {
                if (_panesEnabled() && _getWorkspace()) {
                    openOrSwitchPane(url).then(() => {
                        history.pushState({ paneUrl: url }, '', url);
                    }).catch(err => {
                        console.error('[tabs] pane switch failed', err);
                        _showNavLoading();
                        window.location.href = url;
                    });
                } else {
                    _showNavLoading();
                    window.location.href = url;
                }
            }
        }

        return tab;
    }

    function loadTabs() {
        let saved = [];
        try {
            saved = JSON.parse(localStorage.getItem('tabs') || '[]') || [];
        } catch (e) {
            console.warn('[tabs] corrupted localStorage, clearing', e);
            localStorage.removeItem('tabs');
            saved = [];
        }
        saved
            .filter(t => t && typeof t.url === 'string' && t.url.length && isTabUrl(t.url))
            .forEach(t => addTab(t.text || 'Ticket', t.url, false));
    }
    function closeDraftNewTicketTabs() {
        if (!window.urls || !window.urls.create_ticket) return;
        const base = normalizeUrl(window.urls.create_ticket); 
        const container = getContainer();
        if (!container) return;

        [...container.querySelectorAll('.tab')].forEach(tab => {
            const url = normalizeUrl(tab.dataset.url || '');
            if (!url) return;

            // 1) /tickets/create/ sin id => borrador
            if (url === base) { tab.remove(); return; }

            try {
                const u = new URL(url, window.location.origin);
                const isCreatePath = normalizeUrl(u.pathname) === base;
                if (!isCreatePath) return;

                const qid = (u.searchParams.get('id') || '').trim();
                const looksNumeric = /^\d+$/.test(qid);

                // 2) ids provisionales o no numéricos => borrador
                const isDraft = !qid ||
                    /^ticket-\d+$/i.test(qid) ||
                    /^(new|null|none|draft)$/i.test(qid) ||
                    !looksNumeric;

                if (isDraft) tab.remove();
            } catch (_) { /* noop */ }
        });

        saveTabs();
        _layoutTabs();
    }
    function ensureCurrentTab() {
        const currNorm = normalizeUrl(qs());

        // NEW: si estamos en un ticket real (id numérico), cierra pestañas "Nuevo ticket"
        try {
            const params = new URLSearchParams(window.location.search);
            const id = params.get('id');
            if (id && /^\d+$/.test(id)) {
                closeDraftNewTicketTabs();
            }
        } catch (_) { }

        if (findTabByNorm(currNorm)) { setActive(findTabByNorm(currNorm)); return; }

        // Título por defecto
        let text = document.querySelector('.title-bar h1')?.innerText?.trim();
        if (!text) {
            const params = new URLSearchParams(window.location.search);
            const id = params.get('id');
            text = id ? `Ticket ${id}` : 'Nuevo ticket';
        }
        addTab(text, currNorm, false);
        setActive(findTabByNorm(currNorm));
        saveTabs();
        _layoutTabs();
    }

    function highlightActiveTab() {
        const curr = normalizeUrl(window.location.pathname + window.location.search);
        const isTicket = isTabUrl(curr);
        document.querySelectorAll('.navbar-left .tab').forEach(tab => {
            const same = normalizeUrl(tab.dataset.url) === curr;
            tab.classList.toggle('active', isTicket && same);
        });
    }
    function hookGlobalAddButton() {
        document.addEventListener('click', (e) => {
            if (e.target && e.target.matches('.add-tab')) {
                const tabText = 'Nuevo ticket';
                const tabId = `ticket-${Date.now()}`;
                if (window.urls && window.urls.create_ticket) {
                    addTab(tabText, `${window.urls.create_ticket}?id=${tabId}`);
                }
            }
        });
    }

    // ===== Helpers de notificaciones (scope de módulo) =====
    function getCsrfToken() {
        const name = 'csrftoken';
        for (const cookie of document.cookie.split(';')) {
            const [k, v] = cookie.trim().split('=');
            if (k === name) return decodeURIComponent(v);
        }
        return '';
    }

    async function updateBadge() {
        try {
            const res = await fetch("/api/notifications/");
            if (!res.ok) return;
            const data = await res.json();
            const badge = document.querySelector(".notifications .badge");
            if (!badge) return;
            const count = data.notifications.length;
            badge.textContent = count;
            badge.style.display = count > 0 ? "inline-block" : "none";
        } catch (err) {
            console.error("Error actualizando badge", err);
        }
    }

    // ---- Updates popup ----
    const updPopup = document.createElement('div');
    updPopup.id = 'upd-popup';
    updPopup.innerHTML = `
        <div class="tlp-header">
            <span class="tlp-badge" id="upd-badge"></span>
            <span class="tlp-ticket-num" id="upd-ticket-num"></span>
            <button class="tlp-close" type="button" title="Cerrar">&#x2715;</button>
        </div>
        <div class="tlp-subject" id="upd-subject"></div>
        <div class="upd-meta" id="upd-meta"></div>
    `;
    document.body.appendChild(updPopup);

    let updHideTimer = null;

    const STATUS_LABELS = { open:'open', pending:'pending', resolved:'resolved', closed:'closed' };
    const PRIORITY_LABELS = { low:'Baja', normal:'Normal', high:'Alta', urgent:'Urgente' };

    function showUpdPopup(item) {
        clearTimeout(updHideTimer);
        const status   = item.dataset.status   || '';
        const id       = item.dataset.id       || '';
        const subject  = item.dataset.subject  || '';
        const requester= item.dataset.requester|| '';
        const assignee = item.dataset.assignee || '';
        const priority = item.dataset.priority || '';

        updPopup.querySelector('#upd-badge').textContent  = (STATUS_LABELS[status] || status).toUpperCase();
        updPopup.querySelector('#upd-badge').className    = `tlp-badge status-${status.toLowerCase()}`;
        updPopup.querySelector('#upd-ticket-num').textContent = `Ticket #${id}`;
        updPopup.querySelector('#upd-subject').textContent = subject;

        const rows = [];
        if (requester) rows.push(`<span class="upd-meta-row"><span class="upd-lbl">Solicitante</span> ${requester}</span>`);
        if (assignee)  rows.push(`<span class="upd-meta-row"><span class="upd-lbl">Asignado</span> ${assignee}</span>`);
        if (priority)  rows.push(`<span class="upd-meta-row"><span class="upd-lbl">Prioridad</span> ${PRIORITY_LABELS[priority] || priority}</span>`);
        updPopup.querySelector('#upd-meta').innerHTML = rows.join('');

        const rect = item.getBoundingClientRect();
        const popW = 300;
        const gap  = 10;
        let left = rect.right + gap;
        if (left + popW > window.innerWidth - 8) left = rect.left - popW - gap;
        let top = rect.top;
        const popH = updPopup.offsetHeight || 150;
        if (top + popH > window.innerHeight - 8) top = window.innerHeight - popH - 8;
        if (top < 8) top = 8;
        updPopup.style.left = left + 'px';
        updPopup.style.top  = top  + 'px';
        updPopup.classList.add('visible');
    }

    function hideUpdPopup() {
        updHideTimer = setTimeout(() => updPopup.classList.remove('visible'), 150);
    }

    updPopup.addEventListener('mouseenter', () => clearTimeout(updHideTimer));
    updPopup.addEventListener('mouseleave', hideUpdPopup);
    updPopup.querySelector('.tlp-close').addEventListener('click', () => {
        clearTimeout(updHideTimer);
        updPopup.classList.remove('visible');
    });

    async function refreshUpdates() {
        try {
            const res = await fetch("/api/activity/");
            if (!res.ok) return;
            const data = await res.json();
            const list = document.getElementById("updates-list");
            if (!list) return;

            list.innerHTML = "";
            if (!data.activity || !data.activity.length) {
                list.innerHTML = "<li class='upd-empty'>Sin actualizaciones recientes</li>";
                return;
            }
            data.activity.forEach(n => {
                const li = document.createElement("li");
                li.className = "upd-item";
                li.dataset.id        = n.ticket_id;
                li.dataset.status    = n.status    || '';
                li.dataset.subject   = n.subject   || '';
                li.dataset.requester = n.requester || '';
                li.dataset.assignee  = n.assignee  || '';
                li.dataset.priority  = n.priority  || '';
                li.innerHTML = `
                    <span class="tl-badge status-${(n.status||'').toLowerCase()}">${(n.status||'').slice(0,1).toUpperCase()}</span>
                    <div class="tl-content">
                        <div class="tl-subject">#${n.ticket_id} ${n.subject || ''}</div>
                        <div class="tl-when">${n.updated_at}</div>
                    </div>
                `;
                li.addEventListener('click',       () => openTicket(n.ticket_id));
                li.addEventListener('mouseenter',  () => showUpdPopup(li));
                li.addEventListener('mouseleave',  hideUpdPopup);
                list.appendChild(li);
            });
        } catch (err) {
            console.error("Error refrescando updates", err);
        }
    }

    // ===== Popups manager (Perfil / Notificaciones) =====
    function initPopups() {
        // Lookup lazy en cada acción para evitar referencias null si el DOM
        // aún no estaba completo en el momento del primer initPopups()
        const getNotifPop = () => document.getElementById('notificationsPopup');
        const getUserPop  = () => document.getElementById('userPopup');

        const show = el => el && (el.style.display = 'block');
        const hide = el => el && (el.style.display = 'none');
        const isOpen = el => el && el.style.display === 'block';
        const hideAll = () => { hide(getNotifPop()); hide(getUserPop()); toggleActive(null); };

        function toggleActive(which) {
            const notifIcon = document.querySelector('.navbar-right .notifications');
            const userIcon = document.querySelector('.navbar-right .user-profile');
            if (notifIcon) {
                notifIcon.classList.toggle('active', which === 'notif');
                notifIcon.setAttribute('aria-expanded', which === 'notif' ? 'true' : 'false');
            }
            if (userIcon) {
                userIcon.classList.toggle('active', which === 'user');
                userIcon.setAttribute('aria-expanded', which === 'user' ? 'true' : 'false');
            }
        }

        async function loadNotifications() {
            const notifList = document.getElementById('notificationsList');
            if (!notifList) return;
            try {
                const res = await fetch("/api/notifications/");
                const data = await res.json();

                notifList.innerHTML = "";
                const badge = document.querySelector(".notifications .badge");

                if (!data.notifications.length) {
                    notifList.innerHTML = "<li class='notif-empty'><i class='far fa-bell' aria-hidden='true'></i>" +
                        "<strong>Estás al día</strong><span>No hay notificaciones nuevas.</span></li>";
                    if (badge) { badge.textContent = "0"; badge.style.display = "none"; }
                    return;
                }

                if (badge) { badge.textContent = data.notifications.length; badge.style.display = "inline-block"; }

                data.notifications.forEach(n => {
                    const li = document.createElement("li");
                    li.className = 'notif-item';
                    li.tabIndex = 0;
                    li.setAttribute('role', 'button');
                    li.setAttribute('aria-label', `${n.message}. ${n.created_at}`);
                    const message = document.createElement('strong');
                    message.textContent = n.message;
                    const created = document.createElement('small');
                    created.textContent = n.created_at;
                    li.append(message, created);
                    const activate = async () => {
                        await fetch(`/api/notifications/${n.id}/read/`, {
                            method: "POST",
                            headers: { 'X-CSRFToken': getCsrfToken() }
                        });
                        if (n.ticket_id) openTicket(n.ticket_id);
                        loadNotifications();
                        refreshUpdates();
                    };
                    li.addEventListener('click', activate);
                    li.addEventListener('keydown', e => {
                        if (e.key !== 'Enter' && e.key !== ' ') return;
                        e.preventDefault();
                        activate();
                    });
                    notifList.appendChild(li);
                });
            } catch (err) {
                console.error("Error cargando notificaciones", err);
                notifList.innerHTML = "<li class='notif-empty notif-empty--error'><i class='fas fa-exclamation-circle' aria-hidden='true'></i>" +
                    "<strong>No se pudieron cargar</strong><span>Vuelve a abrir el panel para reintentarlo.</span></li>";
            }
        }

        // Delegación global: funciona aunque la navbar se re-renderice
        document.addEventListener('click', (e) => {
            const notifPop = getNotifPop();
            const userPop  = getUserPop();
            const notifBtn = e.target.closest('.notifications');
            const userBtn  = e.target.closest('.user-profile');
            const closeBtn = e.target.closest('#closePopup');

            if (closeBtn) {
                e.preventDefault(); e.stopPropagation();
                hide(notifPop); toggleActive(null);
                return;
            }

            if (notifBtn) {
                e.preventDefault(); e.stopPropagation();
                if (isOpen(notifPop)) {
                    hide(notifPop); toggleActive(null);
                } else {
                    hide(userPop); show(notifPop); loadNotifications(); toggleActive('notif');
                }
                return;
            }

            if (userBtn) {
                e.preventDefault(); e.stopPropagation();
                if (isOpen(userPop)) {
                    hide(userPop); toggleActive(null);
                } else {
                    hide(notifPop); show(userPop); toggleActive('user');
                }
                return;
            }

            // Clic fuera de los popups → cerrar
            const withinNotif = notifPop && notifPop.contains(e.target);
            const withinUser  = userPop  && userPop.contains(e.target);
            if (!withinNotif && !withinUser) hideAll();
        });

        document.addEventListener('keydown', (e) => {
            const trigger = e.target.closest('.user-profile[role="button"]');
            if (trigger && (e.key === 'Enter' || e.key === ' ')) {
                e.preventDefault();
                trigger.click();
            }
            if (e.key === 'Escape') hideAll();
        });

        const logoutBtn = document.getElementById('logoutButton');
        logoutBtn && logoutBtn.addEventListener('click', (e) => {
            e.preventDefault();
            e.stopPropagation();
            // Cierra popups y navega al logout real
            if (typeof hideAll === 'function') hideAll();
            const target = (window.urls && window.urls.logout) || '/logout/';
            window.location.href = target;
        });
    }
    function initSearch() {
        const searchInput = document.querySelector(".search-bar");
        const resultsBox = document.getElementById("search-results");
        const resultsList = document.getElementById("results-list");
        if (!searchInput || !resultsBox || !resultsList) return;

        let timer = null;
        let activeIndex = -1;

        function setResultsOpen(open) {
            resultsBox.style.display = open ? 'block' : 'none';
            searchInput.setAttribute('aria-expanded', open ? 'true' : 'false');
            if (!open) {
                activeIndex = -1;
                searchInput.removeAttribute('aria-activedescendant');
                resultItems().forEach((item) => {
                    item.classList.remove('is-keyboard-active');
                    item.setAttribute('aria-selected', 'false');
                });
            }
        }

        function resultItems() {
            return [...resultsList.querySelectorAll('.search-result-item')];
        }

        function setActiveResult(index) {
            const items = resultItems();
            if (!items.length) return;
            activeIndex = (index + items.length) % items.length;
            items.forEach((item, i) => {
                const selected = i === activeIndex;
                item.classList.toggle('is-keyboard-active', selected);
                item.setAttribute('aria-selected', selected ? 'true' : 'false');
            });
            const active = items[activeIndex];
            searchInput.setAttribute('aria-activedescendant', active.id);
            active.scrollIntoView({ block: 'nearest' });
        }

        function appendEmpty(kind, title, hint) {
            const li = document.createElement('li');
            li.className = `search-empty search-empty--${kind}`;
            li.innerHTML = `<i class="fas fa-${kind === 'error' ? 'exclamation-circle' : 'search'}" aria-hidden="true"></i>` +
                `<strong>${title}</strong><span>${hint}</span>`;
            resultsList.appendChild(li);
        }

        function appendResult(contentBuilder, onActivate) {
            const li = document.createElement('li');
            li.className = 'search-result-item';
            li.id = `search-result-${resultsList.querySelectorAll('.search-result-item').length}`;
            li.setAttribute('role', 'option');
            li.setAttribute('aria-selected', 'false');
            contentBuilder(li);
            li.addEventListener('click', onActivate);
            li.addEventListener('mousemove', () => {
                const index = resultItems().indexOf(li);
                if (index >= 0) setActiveResult(index);
            });
            resultsList.appendChild(li);
        }

        function renderResults(data) {
            resultsList.innerHTML = "";
            activeIndex = -1;

            if ((!data.tickets || !data.tickets.length) &&
                (!data.users || !data.users.length)) {
                appendEmpty('empty', 'Sin resultados', 'Prueba con otro asunto, ID, nombre o email.');
                setResultsOpen(true);
                return;
            }

            if (data.tickets && data.tickets.length) {
                const header = document.createElement("li");
                header.textContent = "Tickets";
                header.classList.add("result-header");
                header.setAttribute('role', 'presentation');
                resultsList.appendChild(header);

                data.tickets.forEach(t => {
                    appendResult(li => {
                        const title = document.createElement('strong');
                        title.textContent = `#${t.id} ${t.subject || ''}`;
                        const meta = document.createElement('small');
                        meta.textContent = `${t.status || ''} · ${t.requester || 'Sin solicitante'}`;
                        li.append(title, meta);
                    }, () => openTicket(t.id));
                });
            }

            if (data.users && data.users.length) {
                const header = document.createElement("li");
                header.textContent = "Usuarios";
                header.classList.add("result-header");
                header.setAttribute('role', 'presentation');
                resultsList.appendChild(header);

                data.users.forEach(u => {
                    appendResult(li => {
                        const title = document.createElement('strong');
                        title.textContent = `${u.name || 'Sin nombre'} · ${u.email || 'Sin email'}`;
                        const meta = document.createElement('small');
                        meta.textContent = `${u.role || 'Sin rol'} · ${u.group || 'Sin grupo'}`;
                        li.append(title, meta);
                    }, () => openCustomer(u.id, u.name));
                });
            }

            setResultsOpen(true);
        }

        searchInput.addEventListener("input", () => {
            clearTimeout(timer);
            const q = searchInput.value.trim();
            if (q.length < 2) {
                setResultsOpen(false);
                return;
            }
            resultsList.innerHTML = '<li class="search-loading"><i class="fas fa-circle-notch fa-spin" aria-hidden="true"></i> Buscando…</li>';
            setResultsOpen(true);
            timer = setTimeout(async () => {
                try {
                    const res = await fetch(`/search/?q=${encodeURIComponent(q)}`);
                    const data = await res.json();
                    renderResults(data);
                } catch(e) {
                    resultsList.innerHTML = '';
                    appendEmpty('error', 'No se pudo buscar', 'Comprueba la conexión y vuelve a intentarlo.');
                }
            }, 300);
        });

        searchInput.addEventListener('keydown', e => {
            const items = resultItems();
            if (e.key === 'ArrowDown' && items.length) {
                e.preventDefault();
                setActiveResult(activeIndex + 1);
            } else if (e.key === 'ArrowUp' && items.length) {
                e.preventDefault();
                setActiveResult(activeIndex <= 0 ? items.length - 1 : activeIndex - 1);
            } else if (e.key === 'Enter' && activeIndex >= 0 && items[activeIndex]) {
                e.preventDefault();
                items[activeIndex].click();
            } else if (e.key === 'Escape') {
                setResultsOpen(false);
            }
        });

        document.addEventListener("click", (e) => {
            if (!resultsBox.contains(e.target) && e.target !== searchInput) {
                setResultsOpen(false);
            }
        });
    }

    function initOnce() {
        if (window !== window.top) return;  // passive inside iframe

        try {
            loadTabs();
            dedupeDomTabs();
            ensureCurrentTab();
            highlightActiveTab();
            hookGlobalAddButton();
            initPopups();
            initSearch();
            updateBadge();
            refreshUpdates();
            setInterval(updateBadge, 30000);

            // Tab overflow ("···") popup: open/close + close on outside click.
            document.addEventListener('click', (e) => {
                const btn = e.target.closest('.tabs-overflow-btn');
                const popup = _getOverflowPopup();
                if (btn) {
                    e.preventDefault();
                    if (popup && !popup.hidden) _hideOverflowPopup();
                    else _showOverflowPopup();
                    return;
                }
                if (popup && !popup.hidden && !popup.contains(e.target)) {
                    _hideOverflowPopup();
                }
            });

            // Re-layout tabs on window resize (debounced) so visible/overflow
            // partitioning matches the new available width.
            let _layoutT = null;
            window.addEventListener('resize', () => {
                clearTimeout(_layoutT);
                _layoutT = setTimeout(() => {
                    _layoutTabs();
                    const p = _getOverflowPopup();
                    if (p && !p.hidden) _positionOverflowPopup();
                }, 80);
            });

            // First layout pass after loadTabs/ensureCurrentTab populated the bar.
            _layoutTabs();

            // Seed the pane cache with a server-rendered ticket or profile.
            if (_panesEnabled()) {
                const ws = _getWorkspace();
                let initialPane = ws && ws.querySelector(':scope > .ticket-pane, :scope > .profile-pane');
                if (!initialPane && isTabUrl(_realPageUrl)) {
                    const appPane = _getAppContent()?.querySelector(':scope > .profile-pane');
                    if (appPane && ws) {
                        ws.appendChild(appPane);
                        initialPane = appPane;
                    }
                }
                if (initialPane) {
                    const isTicketPane = initialPane.classList.contains('ticket-pane');
                    const rawTid = initialPane.getAttribute('data-ticket-id');
                    const ticketId = rawTid && rawTid !== 'null' && rawTid !== ''
                        ? (parseInt(rawTid, 10) || rawTid)
                        : null;
                    _paneCache.set(normalizeUrl(_realPageUrl), {
                        $pane: initialPane,
                        ticketId,
                        paneType: isTicketPane ? 'ticket' : 'profile',
                        lastUsed: Date.now(),
                    });
                    // Deep-link page load on a ticket: show workspace, hide app-content
                    _showWorkspace();
                    // Bootstrap the server-rendered pane HERE (single source of truth).
                    // Previously this was done by a separate DCL handler in
                    // create_ticket.js, but that path was racy: on F5 with tabs
                    // restored from localStorage, Select2/Quill sometimes never
                    // initialized and the form fell back to raw HTML widgets.
                    if (isTicketPane && initialPane.dataset.tfInitialized !== '1') {
                        initialPane.dataset.tfInitialized = '1';
                        if (typeof window.initTicketPane === 'function') {
                            try {
                                console.debug('[tabs] bootstrapping server-rendered pane', { ticketId });
                                window.initTicketPane(window.jQuery ? window.jQuery(initialPane) : initialPane, {
                                    ticketId,
                                    currentUserId: window.currentUserId,
                                    addCommentUrl: ticketId ? ('/tickets/' + ticketId + '/add_comment/') : null,
                                    mergeTicketUrl: ticketId ? ('/tickets/' + ticketId + '/merge/') : null,
                                    searchUrl: '/search/',
                                });
                            } catch (e) {
                                console.error('[tabs] initTicketPane THREW on server-rendered pane', e);
                            }
                        } else {
                            console.error('[tabs] initTicketPane not defined — server-rendered pane will be unstyled');
                        }
                    }
                } else {
                    // Sidebar page load (home/list/etc.): show app-content, hide workspace
                    _showAppContent();
                }
            }

            // popstate: handle browser Back/Forward without reloading the page.
            window.addEventListener('popstate', () => {
                if (!_panesEnabled()) return;
                const url = window.location.pathname + window.location.search;
                const norm = normalizeUrl(url);

                if (isTabUrl(url)) {
                    // Ticket URL: show cached pane or fetch
                    if (!_getWorkspace()) return;
                    _showWorkspace();
                    const cached = _paneCache.get(norm);
                    // Detached panes (only one in DOM at a time) still count as
                    // a cache hit — _showPane re-attaches them.
                    if (cached && cached.$pane) {
                        _showPane(cached.$pane);
                        const container = getContainer();
                        if (container) {
                            const matching = [...container.querySelectorAll('.tab')]
                                .find(t => normalizeUrl(t.dataset.url || '') === norm);
                            if (matching) setActive(matching);
                        }
                    } else {
                        openOrSwitchPane(url).catch(() => { window.location.reload(); });
                    }
                } else if (_getAppContent()) {
                    // Sidebar URL: fetch fragment into app-content
                    _navigateAppContent(url);
                }
            });
        } catch (e) {
            console.error('[tabs] init error:', e);
        }
    }

    document.addEventListener('DOMContentLoaded', initOnce);

    window.openCustomer = function (id, name) {
        if (window.urls && window.urls.customer_profile) {
            Tabs.addTab(name || `Cliente ${id}`, `${window.urls.customer_profile}?id=${id}`);
        } else {
            window.location.href = `/customers/profile/?id=${id}`;
        }
    };

    // Global helper for opening tickets from anywhere (home page, ticket list, etc.).
    // Previously defined inline in create_ticket.html — moved here so it's available
    // even on sidebar pages that don't load create_ticket.html.
    window.openTicket = function (ticketId, subject) {
        const tabText = subject || ('Ticket ' + ticketId);
        const tabUrl = (window.urls && window.urls.create_ticket) || '/tickets/create/';
        const fullUrl = tabUrl + '?id=' + ticketId;
        if (window.Tabs && typeof window.Tabs.addTab === 'function') {
            window.Tabs.addTab(tabText, fullUrl);
        } else {
            window.location.href = fullUrl;
        }
    };

    window.Tabs = {
        addTab, loadTabs, saveTabs, highlightActiveTab, normalizeUrl,
        closeDraftNewTicketTabs,
        // Pane-based API (active when window.TF_USE_PANES === true)
        openOrSwitchPane,
        paneCache: _paneCache,
        __initialized: true
    };
})();
