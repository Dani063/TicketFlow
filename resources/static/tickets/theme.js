/* TicketFlow theme + user dropdown + keyboard shortcuts (Sprint 2)
 *
 * - Reads theme preference from localStorage ("tf.theme": "light" | "dark" | "system")
 * - Applies data-theme on <html>; on "system" follows prefers-color-scheme
 * - Manages the user-popup submenu navigation (main / appearance / help)
 * - Wires the Keyboard shortcuts modal
 * - Global keyboard shortcuts: /, c, g+t, g+d, Esc, Ctrl+Enter, ?
 *
 * The first half (applyStored) is intentionally inlined in base.html <head>
 * to avoid FOUC; the rest runs after DOMContentLoaded.
 */
(function () {
    'use strict';

    const STORAGE_KEY = 'tf.theme';
    const VALID = ['light', 'dark', 'system'];

    function getStored() {
        try {
            const v = localStorage.getItem(STORAGE_KEY);
            return VALID.includes(v) ? v : 'system';
        } catch (_) { return 'system'; }
    }

    function setStored(value) {
        try { localStorage.setItem(STORAGE_KEY, value); } catch (_) { /* private mode */ }
    }

    function resolveEffective(pref) {
        if (pref === 'dark' || pref === 'light') return pref;
        const m = window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)');
        return m && m.matches ? 'dark' : 'light';
    }

    function applyTheme(pref) {
        const eff = resolveEffective(pref);
        const html = document.documentElement;
        if (eff === 'dark') html.setAttribute('data-theme', 'dark');
        else html.removeAttribute('data-theme');
        html.setAttribute('data-theme-pref', pref);
        // Notify components that paint with captured colors (e.g. Chart.js canvases
        // in reporting.js) so they can re-read the --color-* tokens and redraw.
        try {
            document.dispatchEvent(new CustomEvent('tf:themechange', { detail: { theme: eff } }));
        } catch (_) { /* CustomEvent unsupported — ignore */ }
    }

    // Expose a tiny API
    const Theme = {
        get: getStored,
        set(pref) {
            if (!VALID.includes(pref)) return;
            setStored(pref);
            applyTheme(pref);
            refreshDropdownChecks();
        },
    };
    window.TfTheme = Theme;

    // Apply at the earliest possible moment (script may be inlined by base.html
    // before DOM is ready). Re-apply on system change if pref === 'system'.
    applyTheme(getStored());
    const media = window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)');
    if (media && media.addEventListener) {
        media.addEventListener('change', () => {
            if (getStored() === 'system') applyTheme('system');
        });
    }

    function refreshDropdownChecks() {
        const pref = getStored();
        document.querySelectorAll('.up-theme-option').forEach(li => {
            li.dataset.selected = (li.dataset.themeChoice === pref) ? 'true' : 'false';
        });
    }

    // ====================== Dropdown navigation ======================
    function initUserDropdown() {
        const pop = document.getElementById('userPopup');
        if (!pop) return;
        pop.setAttribute('data-view', 'main');
        refreshDropdownChecks();

        pop.addEventListener('click', (e) => {
            const item = e.target.closest('[data-action], [data-theme-choice]');
            if (!item) return;
            // Stop the global "click-outside" handler from also firing
            e.stopPropagation();

            if (item.dataset.themeChoice) {
                Theme.set(item.dataset.themeChoice);
                return;
            }
            const action = item.dataset.action;
            switch (action) {
                case 'open-appearance': pop.setAttribute('data-view', 'appearance'); break;
                case 'open-help':       pop.setAttribute('data-view', 'help'); break;
                case 'back':            pop.setAttribute('data-view', 'main'); break;
                case 'shortcuts':
                    pop.style.display = 'none';
                    openShortcutsModal();
                    break;
                case 'help-docs':
                    window.location.href = (window.urls && window.urls.documentation) || '/docs/';
                    break;
                case 'help-center':
                    window.location.href = item.dataset.url || '/help/';
                    break;
                case 'signout':
                    pop.style.display = 'none';
                    {
                        const target = (window.urls && window.urls.logout) || '/logout/';
                        window.location.href = target;
                    }
                    break;
            }
        });

        // Reset to main view whenever the popup is reopened
        const observer = new MutationObserver(() => {
            if (pop.style.display === 'none') pop.setAttribute('data-view', 'main');
        });
        observer.observe(pop, { attributes: true, attributeFilter: ['style'] });
    }

    // ====================== Keyboard shortcuts modal ======================
    function openShortcutsModal() {
        const ov = document.getElementById('kbsOverlay');
        if (!ov) return;
        ov.classList.add('open');
    }
    function closeShortcutsModal() {
        const ov = document.getElementById('kbsOverlay');
        if (!ov) return;
        ov.classList.remove('open');
    }
    function initShortcutsModal() {
        const ov = document.getElementById('kbsOverlay');
        if (!ov) return;
        ov.addEventListener('click', (e) => { if (e.target === ov) closeShortcutsModal(); });
        const close = document.getElementById('kbsClose');
        if (close) close.addEventListener('click', closeShortcutsModal);
    }

    // ====================== Global keyboard shortcuts ======================
    function isTypingTarget(el) {
        if (!el) return false;
        const tag = el.tagName;
        if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return true;
        if (el.isContentEditable) return true;
        // Quill editor
        if (el.closest && el.closest('.ql-editor')) return true;
        return false;
    }

    function initGlobalShortcuts() {
        let lastKey = null;
        let lastKeyTs = 0;

        document.addEventListener('keydown', (e) => {
            const inText = isTypingTarget(e.target);

            // Ctrl+Enter: send comment (anywhere inside a form/composer)
            if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) {
                const btn = document.querySelector('[data-shortcut="send-comment"], #send-message-btn');
                if (btn) {
                    e.preventDefault();
                    btn.click();
                    return;
                }
            }

            // Esc: close shortcuts modal, then user popup, then any open dialog/overlay
            if (e.key === 'Escape') {
                const ov = document.getElementById('kbsOverlay');
                if (ov && ov.classList.contains('open')) { closeShortcutsModal(); return; }
                const up = document.getElementById('userPopup');
                if (up && up.style.display === 'block') { up.style.display = 'none'; return; }
                const np = document.getElementById('notificationsPopup');
                if (np && np.style.display === 'block') { np.style.display = 'none'; return; }
                const overlay = document.querySelector('.bulk-merge-overlay.open');
                if (overlay) { overlay.classList.remove('open'); return; }
            }

            if (inText) return;

            // "?": open keyboard shortcuts
            if (e.key === '?' || (e.key === '/' && e.shiftKey)) {
                e.preventDefault();
                openShortcutsModal();
                return;
            }

            // "/": focus search
            if (e.key === '/') {
                const search = document.querySelector('.search-bar');
                if (search) {
                    e.preventDefault();
                    search.focus();
                    search.select && search.select();
                }
                return;
            }

            // "c": create ticket
            if (e.key === 'c' && !e.ctrlKey && !e.metaKey && !e.altKey) {
                const url = (window.urls && window.urls.create_ticket) || '/tickets/create/';
                if (window.Tabs && typeof window.Tabs.addTab === 'function') {
                    window.Tabs.addTab('Nuevo ticket', url);
                } else {
                    window.location.href = url;
                }
                return;
            }

            // Two-key sequences "g d", "g t"
            const now = Date.now();
            if (lastKey === 'g' && (now - lastKeyTs) < 1200) {
                if (e.key === 't') {
                    e.preventDefault();
                    window.location.href = (window.urls && window.urls.tickets_list) || '/tickets/';
                    lastKey = null; return;
                }
                if (e.key === 'd') {
                    e.preventDefault();
                    window.location.href = '/';
                    lastKey = null; return;
                }
            }
            if (e.key === 'g') {
                lastKey = 'g';
                lastKeyTs = now;
                return;
            }
            lastKey = null;
        });
    }

    document.addEventListener('DOMContentLoaded', () => {
        initUserDropdown();
        initShortcutsModal();
        initGlobalShortcuts();
    });
})();
