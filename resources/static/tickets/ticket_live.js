/* Refresco automatico del ticket que se esta mirando.
 *
 * Un unico poller para toda la pagina, a proposito: tabs.js conserva los panes
 * inactivos *detached pero vivos* en su cache, asi que un intervalo por pane
 * seguiria sondeando tickets que nadie tiene delante. Aqui se sondea solo el
 * pane montado y visible.
 *
 * Lo que NO hace, y no debe hacer: tocar el editor o los campos del formulario.
 * Solo anade comentarios nuevos y actualiza la cabecera (estado y fecha). Pisar
 * un campo mientras el agente lo edita le haria perder el cambio y ademas
 * descuadraria la linea base con la que create_ticket.js decide si hay cambios.
 *
 * Re-entrante: tabs.js re-ejecuta los scripts de pagina al volver a una
 * pestana, de ahi el guard de singleton y el intervalo unico en window.
 */
(function () {
    'use strict';
    if (window.__tfLivePollBound) return;
    window.__tfLivePollBound = true;

    const INTERVAL_MS = 15000;
    let busy = false;

    function activePane() {
        const panes = document.querySelectorAll('.ticket-pane');
        for (const pane of panes) {
            // Un pane cacheado por tabs.js sigue en memoria pero fuera del DOM
            // (isConnected false) o sin cajas de render si esta oculto.
            if (!pane.isConnected || !pane.getClientRects().length) continue;
            if (pane.__tfLive && pane.__tfLive.ticketId) return pane;
        }
        return null;
    }

    // La comparte create_ticket.js al publicar: si cada uno pintase la cabecera
    // por su cuenta, acabarian divergiendo. Aqui vive la unica version.
    window.tfApplyTicketHeader = applyHeader;

    function applyHeader(pane, ticket) {
        if (!pane || !ticket) return;
        // Descarta lo que el servidor leyo antes de lo ya pintado: el sondeo
        // puede salir antes de publicar y volver despues.
        var live0 = pane.__tfLive;
        var ts = Number(ticket.updated_ts) || 0;
        if (live0 && ts) {
            if (live0.headerTs && ts < live0.headerTs) return;
            live0.headerTs = ts;
        }
        const badge = pane.querySelector('.ticket-status-badge');
        if (badge && ticket.status) {
            badge.className = 'ticket-status-badge status-badge-' + ticket.status;
            badge.textContent = ticket.status_display || ticket.status;
        }

        const updated = pane.querySelector('[data-tf-updated]');
        if (updated && ticket.updated_at) updated.textContent = ticket.updated_at;

        // "Publicar como <estado>" arrastra el estado que se enviara al publicar,
        // asi que si otro agente cambia el estado hay que re-sincronizarlo o la
        // siguiente publicacion lo revertiria sin querer. Pero solo si el agente
        // no lo ha tocado el mismo: si lo ha cambiado a mano, esa es su intencion.
        const live = pane.__tfLive;
        const selected = pane.querySelector('#selected-status');
        if (selected && ticket.status && live) {
            const shown = (selected.textContent || '').trim().toLowerCase();
            const untouched = !live.serverStatus || shown === live.serverStatus;
            const editing = typeof live.hasPendingFieldChanges === 'function'
                && live.hasPendingFieldChanges();
            if (untouched && !editing) selected.textContent = ticket.status;
            live.serverStatus = ticket.status;
        }
    }

    async function poll() {
        if (busy || document.hidden) return;
        const pane = activePane();
        if (!pane) return;
        const live = pane.__tfLive;
        busy = true;
        try {
            const res = await fetch(
                '/api/tickets/' + encodeURIComponent(live.ticketId) +
                '/live/?after=' + encodeURIComponent(live.lastCommentId || 0),
                { headers: { 'X-Requested-With': 'XMLHttpRequest' } },
            );
            if (!res.ok) return;
            const data = await res.json();

            (data.comments || []).forEach(function (comment) {
                // appendComment deduplica por data-comment-id, asi que un mensaje
                // ya pintado (el que acaba de publicar el propio agente) no se repite.
                try { live.appendComment(comment); } catch (err) { console.error('[tf-live] append', err); }
            });
            const last = Number(data.last_comment_id);
            if (Number.isFinite(last) && last > (live.lastCommentId || 0)) live.lastCommentId = last;

            if (data.ticket) applyHeader(pane, data.ticket);
        } catch (err) {
            // Un fallo de red no rompe el ciclo: se reintenta en la vuelta siguiente.
        } finally {
            busy = false;
        }
    }

    window.__tfLivePollTimer = setInterval(poll, INTERVAL_MS);
    // Al volver a la pestana del navegador, una pasada inmediata en vez de
    // esperar hasta INTERVAL_MS con la conversacion desactualizada delante.
    document.addEventListener('visibilitychange', function () {
        if (!document.hidden) poll();
    });
})();
