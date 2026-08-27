/* Asistencia de respuesta IA en el detalle del ticket.
 *
 * Listeners delegados en document -> robusto frente al SPA (tabs.js re-monta
 * paneles): un único binding maneja clicks de cualquier panel re-renderizado.
 * Genera borradores vía /api/tickets/<id>/ai-suggest-reply/ y los inserta en el
 * editor Quill (window.QuillComposer.setHtml).
 */
(function () {
    'use strict';
    if (window.__aiReplyBound) return;
    window.__aiReplyBound = true;

    function getCookie(name) {
        const m = document.cookie.match('(^|;)\\s*' + name + '\\s*=\\s*([^;]+)');
        return m ? m.pop() : '';
    }

    function esc(s) {
        return String(s == null ? '' : s)
            .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;');
    }

    function paneOf(el) {
        return el.closest('.ticket-pane') || document;
    }

    function panelIn(pane) {
        return (pane.querySelector ? pane.querySelector('#ai-reply-panel') : null)
            || document.getElementById('ai-reply-panel');
    }

    function renderLoading(panel) {
        panel.hidden = false;
        panel.innerHTML =
            '<div class="ai-reply-head"><i class="fas fa-robot"></i> Generando respuestas con IA…' +
            '<button type="button" class="ai-reply-close" title="Cerrar">&times;</button></div>' +
            '<div class="ai-reply-loading"><span class="ai-dot"></span><span class="ai-dot"></span><span class="ai-dot"></span></div>';
    }

    function renderError(panel, msg) {
        panel.hidden = false;
        panel.innerHTML =
            '<div class="ai-reply-head"><i class="fas fa-robot"></i> Asistencia IA' +
            '<button type="button" class="ai-reply-close" title="Cerrar">&times;</button></div>' +
            '<div class="ai-reply-error">' + esc(msg) + '</div>' +
            '<div class="ai-reply-foot"><button type="button" class="ai-reply-regen">Reintentar</button></div>';
    }

    function render(panel, data) {
        const drafts = (data && data.drafts) || [];
        const similar = (data && data.similar) || [];
        let html =
            '<div class="ai-reply-head"><i class="fas fa-robot"></i> Borradores sugeridos' +
            ' <span class="ai-reply-lang">' + esc((data.language || '').toUpperCase()) + '</span>' +
            '<button type="button" class="ai-reply-close" title="Cerrar">&times;</button></div>';

        if (!drafts.length) {
            html += '<div class="ai-reply-error">La IA no devolvió borradores. Prueba a dar una instrucción.</div>';
        }
        drafts.forEach(function (d, i) {
            html +=
                '<div class="ai-draft">' +
                '<div class="ai-draft-title">' + esc(d.title || ('Borrador ' + (i + 1))) + '</div>' +
                '<div class="ai-draft-body">' + esc(d.body).replace(/\n/g, '<br>') + '</div>' +
                '<div class="ai-draft-actions">' +
                '<button type="button" class="ai-reply-insert" data-idx="' + i + '"><i class="fas fa-arrow-down"></i> Insertar</button>' +
                '<button type="button" class="ai-reply-copy" data-idx="' + i + '"><i class="far fa-copy"></i> Copiar</button>' +
                '</div></div>';
        });

        if (similar.length) {
            html += '<div class="ai-reply-similar"><span class="ai-reply-similar-lbl">Casos similares usados:</span> ';
            similar.forEach(function (s) {
                html += '<span class="ai-sim-chip" title="similitud ' + esc(s.score) + '">#' +
                    esc(s.id) + ' ' + esc((s.subject || '').slice(0, 40)) + '</span> ';
            });
            html += '</div>';
        }

        html +=
            '<div class="ai-reply-foot">' +
            '<input type="text" class="ai-reply-instruction" placeholder="Afinar: ¿qué quieres responder o pedir? (opcional)">' +
            '<button type="button" class="ai-reply-regen"><i class="fas fa-redo"></i> Regenerar</button>' +
            '</div>';

        panel.hidden = false;
        panel.innerHTML = html;
        // Guarda los borradores para insertar/copiar sin re-pedir.
        panel.__drafts = drafts;
    }

    async function generate(pane, instruction) {
        const ctx = (window.tfPaneContext && window.tfPaneContext[pane.dataset ? pane.dataset.ticketId : null]) || null;
        const tid = (pane.dataset && pane.dataset.ticketId) || (ctx && ctx.ticketId) || window.ticketId;
        const url = (ctx && ctx.aiSuggestReplyUrl) || (tid ? '/api/tickets/' + tid + '/ai-suggest-reply/' : null);
        const panel = panelIn(pane);
        if (!url || !panel) return;
        renderLoading(panel);
        try {
            const res = await fetch(url, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCookie('csrftoken') },
                body: JSON.stringify({ instruction: instruction || '' }),
            });
            const json = await res.json();
            if (!res.ok || json.ok === false) {
                renderError(panel, (json && (json.detail || (json.error && json.error.message))) || ('Error ' + res.status));
                return;
            }
            render(panel, json.data || json);
        } catch (e) {
            renderError(panel, 'No se pudo contactar con el servidor: ' + e);
        }
    }

    document.addEventListener('click', function (ev) {
        const btn = ev.target.closest(
            '#ai-reply-btn, .ai-reply-close, .ai-reply-insert, .ai-reply-copy, .ai-reply-regen'
        );
        if (!btn) return;
        const pane = paneOf(btn);
        const panel = panelIn(pane);

        if (btn.id === 'ai-reply-btn') {
            ev.preventDefault();
            generate(pane, '');
        } else if (btn.classList.contains('ai-reply-close')) {
            if (panel) { panel.hidden = true; panel.innerHTML = ''; }
        } else if (btn.classList.contains('ai-reply-regen')) {
            const inp = panel && panel.querySelector('.ai-reply-instruction');
            generate(pane, inp ? inp.value : '');
        } else if (btn.classList.contains('ai-reply-insert') || btn.classList.contains('ai-reply-copy')) {
            const idx = parseInt(btn.dataset.idx, 10);
            const drafts = (panel && panel.__drafts) || [];
            const body = drafts[idx] && drafts[idx].body;
            if (!body) return;
            if (btn.classList.contains('ai-reply-copy')) {
                if (navigator.clipboard) navigator.clipboard.writeText(body);
                btn.innerHTML = '<i class="fas fa-check"></i> Copiado';
            } else {
                const html = esc(body).replace(/\n/g, '<br>');
                if (window.QuillComposer && window.QuillComposer.setHtml) {
                    window.QuillComposer.setHtml(html);
                    if (window.QuillComposer.focus) window.QuillComposer.focus();
                } else {
                    const ta = pane.querySelector('#new-message');
                    if (ta) ta.value = body;
                }
                if (panel) panel.hidden = true;
            }
        }
    });
})();
