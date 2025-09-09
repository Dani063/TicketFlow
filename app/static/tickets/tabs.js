/* ===== Tabs manager (reworked) ===== */
(function () {
    // Evita doble inicialización si el script se carga dos veces por error
    if (window.Tabs && window.Tabs.__initialized) return;

    const qs = () => window.location.pathname + window.location.search;
    function isTicketUrl(u) {
        // Solo consideramos "tab" a las URLs del formulario de ticket
        if (!window.urls || !window.urls.create_ticket) return false;
        const normCreate = normalizeUrl(window.urls.create_ticket);
        const norm = normalizeUrl(u);
        return norm.startsWith(normCreate); // ej: /tickets/create/?id=...
    }

    function normalizeUrl(u) {
        const url = new URL(u, window.location.origin);
        const path = url.pathname;
        const params = [...url.searchParams.entries()].sort((a, b) =>
            a[0] === b[0] ? (a[1] > b[1] ? 1 : -1) : (a[0] > b[0] ? 1 : -1)
        );
        const query = params.map(([k, v]) => `${k}=${encodeURIComponent(v)}`).join('&');
        return query ? `${path}?${query}` : path;
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
        if (!isTicketUrl(url)) {
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
        const saved = JSON.parse(localStorage.getItem('tabs') || '[]')
            .filter(t => isTicketUrl(t.url)); // ← solo tabs de tickets
        saved.forEach(t => addTab(t.text, t.url, false));
    }

    // Garantiza que exista la pestaña de la URL actual si es una vista “ticket”
    function ensureCurrentTab() {
        const currNorm = normalizeUrl(qs());
        if (findTabByNorm(currNorm)) return;

        // Título por defecto: usar h1 de la barra de título si existe; si no, inferir
        let text = document.querySelector('.title-bar h1')?.innerText?.trim();
        if (!text) {
            // Heurística para create_ticket con ?id=
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
        const isTicket = isTicketUrl(curr);
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

    function initOnce() {
        loadTabs();
        dedupeDomTabs();
        ensureCurrentTab();   // <— clave para que al entrar en create_ticket exista una sola pestaña correcta
        highlightActiveTab();
        hookGlobalAddButton();
    }

    document.addEventListener('DOMContentLoaded', initOnce);

    window.Tabs = {
        addTab, loadTabs, saveTabs, highlightActiveTab, normalizeUrl,
        __initialized: true
    };
})();
