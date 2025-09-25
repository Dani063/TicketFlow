/* ===== Tabs manager (reworked) ===== */
(function () {
    // Evita doble inicialización si el script se carga dos veces por error
    if (window.Tabs && window.Tabs.__initialized) return;

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
        if (tabEl) tabEl.classList.add('active');
    }

    function addTab(text, url, select = true) {
        if (!isTabUrl(url)) {
            // No crear pestañas para páginas del sidebar; simplemente navegar (o no hacer nada si ya estás ahí)
            const curr = normalizeUrl(window.location.pathname + window.location.search);
            const target = normalizeUrl(url);
            if (curr !== target) window.location.href = url;
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
                window.location.href = existing.dataset.url;
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
        closeButton.innerText = 'X';
        closeButton.className = 'close-tab';

        closeButton.addEventListener('click', (e) => {
            e.stopPropagation();
            const wasActive = tab.classList.contains('active');
            const container = getContainer();
            const siblings = container ? [...container.querySelectorAll('.tab')] : [];
            const idx = siblings.indexOf(tab);

            tab.remove();
            saveTabs();

            // Si no era la activa, no navegamos (solo quitar del DOM)
            if (!wasActive) return;

            // Elegimos la vecina lógica para activar si cerramos la activa
            const fallback = siblings[idx + 1] || siblings[idx - 1] || null;
            if (fallback) {
                setActive(fallback);
                const nextUrl = fallback.dataset.url;
                if (normalizeUrl(qs()) !== normalizeUrl(nextUrl)) {
                    window.location.href = nextUrl;
                }
            } else if (window.urls && window.urls.tickets_list) {
                // No quedan pestañas → ir a lista
                if (normalizeUrl(qs()) !== normalizeUrl(window.urls.tickets_list)) {
                    window.location.href = window.urls.tickets_list;
                }
            }
        });

        tab.appendChild(img);
        tab.appendChild(tabText);
        tab.appendChild(closeButton);

        tab.addEventListener('click', () => {
            const targetNorm = normalizeUrl(tab.dataset.url);
            setActive(tab);
            if (normalizeUrl(qs()) !== targetNorm) {
                window.location.href = tab.dataset.url;
            }
        });

        container.appendChild(tab);
        dedupeDomTabs();   // asegurar 1 sola por URL
        saveTabs();

        // Seleccionar (pero sin duplicar navegación)
        if (select) {
            setActive(tab);
            const targetNorm2 = normalizeUrl(url);
            if (normalizeUrl(qs()) !== targetNorm2) {
                window.location.href = url;
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

    // ===== Popups manager (Perfil / Notificaciones) =====
    function initPopups() {
        // Cache de nodos (si no existen en alguna vista, todo queda no-op)
        const notifPop = document.getElementById('notificationsPopup');
        const userPop = document.getElementById('userPopup');
        const closeBtn = document.getElementById('closePopup');
        const notifList = document.getElementById('notificationsList');

        // Si no existe ninguno de los popups, no registramos nada
        if (!notifPop && !userPop) {
            // Opcional: consola para depurar
            // console.debug('[tabs] initPopups: no popups found');
            return;
        }

        const show = el => el && (el.style.display = 'block');
        const hide = el => el && (el.style.display = 'none');
        const isOpen = el => el && el.style.display === 'block';
        const hideAll = () => { hide(notifPop); hide(userPop); toggleActive(null); };

        function toggleActive(which) {
            const notifIcon = document.querySelector('.navbar-right .notifications');
            const userIcon = document.querySelector('.navbar-right .user-profile');
            if (notifIcon) notifIcon.classList.toggle('active', which === 'notif');
            if (userIcon) userIcon.classList.toggle('active', which === 'user');
        }

        function loadNotifications() {
            if (!notifList) return;
            // Sustituye por tu fetch cuando lo tengas listo
            const notifications = ['Notificación 1', 'Notificación 2', 'Notificación 3'];
            notifList.innerHTML = '';
            notifications.forEach(n => {
                const li = document.createElement('li');
                li.textContent = n;
                notifList.appendChild(li);
            });
        }

        // Delegación global: funciona aunque la navbar se re-renderice
        document.addEventListener('click', (e) => {
            const notifBtn = e.target.closest('.notifications');
            const userBtn = e.target.closest('.user-profile');

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
            const withinUser = userPop && userPop.contains(e.target);
            if (!withinNotif && !withinUser) hideAll();
        });

        // Botón cerrar de notificaciones (si existe)
        closeBtn && closeBtn.addEventListener('click', (e) => {
            e.preventDefault(); e.stopPropagation();
            hide(notifPop); toggleActive(null);
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
    function initOnce() {
        try {
            loadTabs();
            dedupeDomTabs();
            ensureCurrentTab();
            highlightActiveTab();
            hookGlobalAddButton();
            initPopups();
        } catch (e) {
            console.error('[tabs] init error:', e);
        }
    }

    document.addEventListener('DOMContentLoaded', initOnce);

    window.Tabs = {
        addTab, loadTabs, saveTabs, highlightActiveTab, normalizeUrl,
        closeDraftNewTicketTabs,
        __initialized: true
    };
})();
