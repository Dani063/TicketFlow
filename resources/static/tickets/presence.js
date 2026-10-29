/* Un solo latido para el panel visible; el estado compartido vive en el servidor. */
(function () {
    'use strict';
    if (window.__tfPresenceBound) return;
    window.__tfPresenceBound = true;
    let current = null;
    let authorized = true;
    let pageActive = true;
    const blocked = new WeakSet();

    function visiblePane() {
        if (document.hidden || !authorized || !pageActive) return null;
        return [...document.querySelectorAll('.ticket-pane[data-presence-enabled="1"]')]
            .find(p => !blocked.has(p) && p.isConnected && p.getClientRects().length &&
                Number.isSafeInteger(Number(p.dataset.ticketId)) && Number(p.dataset.ticketId) > 0) || null;
    }

    function csrf(pane) {
        return pane.querySelector('[name="csrfmiddlewaretoken"]')?.value ||
            decodeURIComponent(document.cookie.match(/(?:^|;\s*)csrftoken=([^;]+)/)?.[1] || '');
    }

    function url(state) { return `/api/tickets/${state.id}/presence/`; }

    function leave(state) {
        if (!state) return;
        state.controller?.abort();
        clearTimeout(state.typingTimer);
        state.pane.querySelector('[data-ticket-presence]')?.remove();
        if (!state.lastSeen) return;
        const body = new URLSearchParams({action:'leave', seen_at:state.lastSeen,
            csrfmiddlewaretoken:csrf(state.pane)});
        try {
            if (!navigator.sendBeacon(url(state), body)) {
                void fetch(url(state), {method:'POST', body, credentials:'same-origin', keepalive:true}).catch(() => {});
            }
        } catch (_) { /* La fila caduca en 45 s aunque el navegador no pueda avisar. */ }
    }

    function render(state, agents) {
        let badge = state.pane.querySelector('[data-ticket-presence]');
        if (!agents.length) { badge?.remove(); return; }
        const title = state.pane.querySelector('.title-bar');
        if (!title) return;
        if (!badge) {
            badge = document.createElement('div');
            badge.dataset.ticketPresence = '';
            badge.className = 'tf-ticket-presence';
            badge.setAttribute('role', 'status');
            badge.setAttribute('aria-live', 'polite');
            title.appendChild(badge);
        }
        // No reconstruir en cada latido: conserva las animaciones y evita anuncios repetidos.
        const signature = JSON.stringify(agents.map(a => [a.name, Boolean(a.composing)]));
        if (badge.dataset.signature === signature) return;
        badge.dataset.signature = signature;
        const items = agents.map(agent => {
            const item = document.createElement('span');
            item.className = 'tf-presence-person' + (agent.composing ? ' is-composing' : '');
            const icon = document.createElement('i');
            icon.className = 'fas ' + (agent.composing ? 'fa-pen' : 'fa-eye');
            icon.setAttribute('aria-hidden', 'true');
            const copy = document.createElement('span');
            copy.className = 'tf-presence-copy';
            const name = document.createElement('strong');
            name.textContent = agent.name;
            copy.append(name, document.createTextNode(agent.composing
                ? ' está escribiendo' : ' está viendo este ticket'));
            item.append(icon, copy);
            if (agent.composing) {
                const dots = document.createElement('span');
                dots.className = 'tf-presence-dots';
                dots.setAttribute('aria-hidden', 'true');
                for (let i = 0; i < 3; i++) dots.appendChild(document.createElement('span'));
                item.appendChild(dots);
            }
            return item;
        });
        badge.replaceChildren(...items);
    }

    async function pulse(state) {
        if (!state || state !== current || state.busy || state.pane !== visiblePane()) return;
        state.busy = true;
        state.controller = new AbortController();
        const timeout = setTimeout(() => state.controller.abort(), 10000);
        try {
            const response = await fetch(url(state), {
                method:'POST', credentials:'same-origin', signal:state.controller.signal,
                headers:{'Content-Type':'application/json', 'X-CSRFToken':csrf(state.pane)},
                body:JSON.stringify({composing:Date.now() - state.lastInput < 10000}),
            });
            if (!response.ok) {
                if (response.status === 401 || response.status === 403) authorized = false;
                if (response.status === 404) blocked.add(state.pane);
                render(state, []);
                return;
            }
            const json = await response.json();
            if (current !== state || state.pane !== visiblePane()) return;
            state.lastSeen = json.data?.last_seen || null;
            render(state, json.data?.others || []);
        } catch (_) { /* El siguiente latido reintenta sin interferir con el editor. */ }
        finally { clearTimeout(timeout); state.busy = false; }
    }

    function sync() {
        const pane = visiblePane();
        if (pane === current?.pane && Number(pane?.dataset.ticketId) === current?.id) return;
        leave(current);
        current = pane ? {pane, id:Number(pane.dataset.ticketId), lastSeen:null, lastInput:0, busy:false} : null;
        if (current) void pulse(current);
    }

    function tick() {
        sync();
        if (current) void pulse(current);
    }

    document.addEventListener('input', event => {
        const editor = event.target.closest?.('#messageInputArea .ql-editor, #messageInputArea textarea');
        if (!editor || editor.closest('.ticket-pane') !== current?.pane) return;
        const state = current;
        const wasTyping = Date.now() - state.lastInput < 10000;
        const text = 'value' in editor ? editor.value : editor.textContent;
        state.lastInput = text.trim() ? Date.now() : 0;
        clearTimeout(state.typingTimer);
        if (text.trim()) state.typingTimer = setTimeout(() => {
            state.lastInput = 0;
            void pulse(state);
        }, 10000);
        if (wasTyping !== Boolean(state.lastInput)) void pulse(state);
    });
    document.addEventListener('visibilitychange', tick);
    document.addEventListener('tf:pane-dispose', event => {
        blocked.add(event.target);
        if (event.target === current?.pane) { leave(current); current = null; }
    }, true);
    window.addEventListener('pagehide', () => { pageActive = false; leave(current); current = null; });
    window.addEventListener('pageshow', () => { pageActive = true; tick(); });
    const observer = new MutationObserver(sync);
    observer.observe(document.documentElement, {subtree:true, childList:true, attributes:true,
        attributeFilter:['hidden', 'class', 'data-ticket-id', 'data-presence-enabled']});
    window.__tfPresenceTimer = setInterval(tick, 15000);
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', tick, {once:true});
    else tick();
})();
