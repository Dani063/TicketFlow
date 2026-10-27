/* Restauración desde cualquier panel, también en enlaces directos y en la SPA. */
(function () {
    window.__tfTicketTrashController?.abort();
    const controller = new AbortController();
    window.__tfTicketTrashController = controller;

    async function refreshPane(pane, ticketId) {
        const tabs = window.Tabs;
        const url = `${window.urls?.create_ticket || '/tickets/create/'}?id=${ticketId}`;
        if (!tabs?.paneCache || typeof tabs.openOrSwitchPane !== 'function') {
            window.location.assign(url);
            return;
        }
        const cached = [...tabs.paneCache.entries()].find(([, entry]) => entry.$pane === pane);
        const key = cached?.[0] || tabs.normalizeUrl(url);
        const visible = pane.isConnected && pane.getClientRects().length > 0;
        const parent = pane.parentElement;
        tabs.paneCache.delete(key);
        try {
            if (visible) await tabs.openOrSwitchPane(key);
        } catch (error) {
            if (cached) tabs.paneCache.set(key, cached[1]);
            if (visible && parent && !pane.isConnected) parent.appendChild(pane);
            throw new Error('El ticket está restaurado, pero no se pudo recargar el panel. Reintenta la actualización.');
        }
        pane.dispatchEvent(new CustomEvent('tf:pane-dispose'));
        if (window.jQuery) window.jQuery(pane).remove();
        else pane.remove();
    }

    document.addEventListener('click', async event => {
        const button = event.target.closest('[data-restore-ticket]');
        if (!button || button.disabled) return;
        const pane = button.closest('.ticket-pane');
        const id = Number(button.dataset.restoreTicket);
        if (!pane || !Number.isSafeInteger(id) || id < 1) return;
        event.preventDefault();
        button.disabled = true;
        try {
            const csrf = document.cookie.match(/(?:^|;\s*)csrftoken=([^;]+)/)?.[1] || '';
            const response = await fetch('/tickets/bulk_restore/', {
                method: 'POST', credentials: 'same-origin',
                headers: {'Content-Type': 'application/json', 'X-CSRFToken': decodeURIComponent(csrf)},
                body: JSON.stringify({ids: [id]}),
            });
            const json = await response.json();
            if (!response.ok || !json.ok) throw new Error(json.detail || 'No se pudo restaurar el ticket.');
            await refreshPane(pane, id);
            window.toast?.success('Ticket restaurado.');
        } catch (error) {
            window.toast?.error(error.message || 'No se pudo restaurar el ticket.');
        } finally {
            button.disabled = false;
        }
    }, {signal: controller.signal});
})();
