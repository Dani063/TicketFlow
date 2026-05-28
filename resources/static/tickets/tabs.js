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

    const STATUS_LABELS = { open:'Abierto', pending:'Pendiente', resolved:'Resuelto', closed:'Cerrado' };
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
            if (notifIcon) notifIcon.classList.toggle('active', which === 'notif');
            if (userIcon) userIcon.classList.toggle('active', which === 'user');
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
                    notifList.innerHTML = "<li class='notif-empty'>No hay notificaciones</li>";
                    if (badge) { badge.textContent = "0"; badge.style.display = "none"; }
                    return;
                }

                if (badge) { badge.textContent = data.notifications.length; badge.style.display = "inline-block"; }

                data.notifications.forEach(n => {
                    const li = document.createElement("li");
                    li.innerHTML = `<strong>${n.message}</strong><br><small>${n.created_at}</small>`;
                    li.onclick = async () => {
                        await fetch(`/api/notifications/${n.id}/read/`, {
                            method: "POST",
                            headers: { 'X-CSRFToken': getCsrfToken() }
                        });
                        if (n.ticket_id) openTicket(n.ticket_id);
                        loadNotifications();
                        refreshUpdates();
                    };
                    notifList.appendChild(li);
                });
            } catch (err) {
                console.error("Error cargando notificaciones", err);
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

        function renderResults(data) {
            resultsList.innerHTML = "";

            if ((!data.tickets || !data.tickets.length) &&
                (!data.users || !data.users.length)) {
                resultsList.innerHTML = "<li>No hay resultados</li>";
                resultsBox.style.display = "block";
                return;
            }

            if (data.tickets && data.tickets.length) {
                const header = document.createElement("li");
                header.textContent = "Tickets";
                header.classList.add("result-header");
                resultsList.appendChild(header);

                data.tickets.forEach(t => {
                    const li = document.createElement("li");
                    li.innerHTML = `<strong>#${t.id}</strong> ${t.subject} 
                                <small>(${t.status} – ${t.requester})</small>`;
                    li.onclick = () => openTicket(t.id);
                    resultsList.appendChild(li);
                });
            }

            if (data.users && data.users.length) {
                const header = document.createElement("li");
                header.textContent = "Usuarios";
                header.classList.add("result-header");
                resultsList.appendChild(header);

                data.users.forEach(u => {
                    const li = document.createElement("li");
                    li.innerHTML = `<strong>${u.name}</strong> (${u.email}) 
                                <small>${u.role} / ${u.group}</small>`;
                    li.onclick = () => openCustomer(u.id);
                    resultsList.appendChild(li);
                });
            }

            resultsBox.style.display = "block";
        }

        searchInput.addEventListener("input", () => {
            clearTimeout(timer);
            const q = searchInput.value.trim();
            if (q.length < 2) {
                resultsBox.style.display = "none";
                return;
            }
            resultsList.innerHTML = '<li class="result-header" style="font-style:italic;font-weight:normal;">Buscando…</li>';
            resultsBox.style.display = "block";
            timer = setTimeout(async () => {
                try {
                    const res = await fetch(`/search/?q=${encodeURIComponent(q)}`);
                    const data = await res.json();
                    renderResults(data);
                } catch(e) {
                    resultsList.innerHTML = '<li>Error al buscar</li>';
                }
            }, 300);
        });

        document.addEventListener("click", (e) => {
            if (!resultsBox.contains(e.target) && e.target !== searchInput) {
                resultsBox.style.display = "none";
            }
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
            initSearch();
            updateBadge();
            refreshUpdates();
            setInterval(updateBadge, 30000);
        } catch (e) {
            console.error('[tabs] init error:', e);
        }
    }

    document.addEventListener('DOMContentLoaded', initOnce);

    window.openCustomer = function (id) {
        if (window.urls && window.urls.customer_profile) {
            Tabs.addTab(`Cliente ${id}`, `${window.urls.customer_profile}?id=${id}`);
        } else {
            window.location.href = `/customers/profile/?id=${id}`;
        }
    };

    window.Tabs = {
        addTab, loadTabs, saveTabs, highlightActiveTab, normalizeUrl,
        closeDraftNewTicketTabs,
        __initialized: true
    };
})();
