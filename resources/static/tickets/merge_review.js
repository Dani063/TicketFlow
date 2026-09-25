/* Paso 2 de los modales de fusión (ticket, lista y home).
 *
 * Maneja el bloque de tickets/_merge_review.html: dos editores Quill, uno para
 * el comentario con el que se cierra el origen (el que recibe el cliente) y
 * otro para el que recibe el destino, cada uno con su selector Público/Interno.
 *
 *   const review = TFMergeReview.for(modalEl);
 *   review.reset();                                  // al abrir el modal
 *   review.show({ sources: [{displayId, subject}], target: {displayId, subject} });
 *   review.payload();  // { source: {content, html_body, is_public}, target: {...} }
 *
 * Re-entrante: base.html lo carga una vez, pero tabs.js puede re-ejecutarlo.
 */
(function () {
    'use strict';
    if (window.TFMergeReview) return;

    function plural(n, one, many) { return n === 1 ? one : many; }

    function ticketList(sources) {
        return sources.map(s => '#' + s.displayId).join(', ');
    }

    function defaultMessages(sources, target) {
        const one = sources.length === 1;
        return {
            source: 'Hola,\n\nHemos unido esta solicitud con el ticket #' + target.displayId +
                ' («' + target.subject + '»), donde continuaremos con su gestión. ' +
                'Este ticket queda cerrado.\n\nUn saludo.',
            target: one
                ? 'Se ha fusionado en este ticket el #' + sources[0].displayId +
                  (sources[0].subject ? ' («' + sources[0].subject + '»).' : '.')
                : 'Se han fusionado en este ticket los tickets ' + ticketList(sources) + '.',
        };
    }

    function create(modalEl) {
        const block = modalEl.querySelector('[data-merge-review]');
        const role = (name) => block.querySelector('[data-role="' + name + '"]');
        const editors = {};

        function setVisibility(ed, isPublic) {
            ed.isPublic = isPublic;
            ed.el.classList.toggle('is-internal', !isPublic);
            ed.el.querySelectorAll('.merge-vis__opt').forEach((btn) => {
                const active = (btn.dataset.public === 'true') === isPublic;
                btn.classList.toggle('is-active', active);
                btn.setAttribute('aria-pressed', active ? 'true' : 'false');
            });
        }

        // Por defecto el cierre del origen es público (es el aviso al cliente) y
        // el del destino interno: la conversación del origen ya viaja al destino.
        function resetVisibility() {
            if (!editors.source) return;
            setVisibility(editors.source, true);
            setVisibility(editors.target, false);
        }

        function ensureEditors() {
            if (editors.source) return;
            ['source', 'target'].forEach((key) => {
                const el = block.querySelector('[data-merge-editor="' + key + '"]');
                const quill = new Quill(el.querySelector('.merge-editor__quill'), {
                    theme: 'snow',
                    modules: {
                        toolbar: [
                            ['bold', 'italic', 'underline'],
                            [{ list: 'ordered' }, { list: 'bullet' }],
                            ['link', 'blockquote'],
                            ['clean'],
                        ],
                    },
                });
                const ed = { quill, el, isPublic: true, dirty: false };
                quill.on('text-change', (_delta, _old, source) => { if (source === 'user') ed.dirty = true; });
                el.querySelectorAll('.merge-vis__opt').forEach((btn) => {
                    btn.addEventListener('click', () => setVisibility(ed, btn.dataset.public === 'true'));
                });
                editors[key] = ed;
            });
            resetVisibility();
        }

        return {
            reset() {
                Object.values(editors).forEach((ed) => { ed.dirty = false; });
                resetVisibility();
            },

            show({ sources, target }) {
                ensureEditors();
                const n = sources.length;
                role('source-title').textContent = plural(n, 'Ticket origen', 'Tickets origen');
                role('source-ids').textContent = ticketList(sources);
                role('source-hint').textContent = plural(n,
                    'Se cerrará con este comentario',
                    'Se cerrarán con este comentario (lo recibe cada uno)');
                role('target-id').textContent = '#' + target.displayId;
                // Solo se reescribe lo que el agente no ha tocado: volver atrás a
                // cambiar de destino no le borra lo que ya había redactado.
                const defaults = defaultMessages(sources, target);
                Object.keys(editors).forEach((key) => {
                    if (!editors[key].dirty) editors[key].quill.setText(defaults[key]);
                });
                setTimeout(() => editors.source.quill.focus(), 30);
            },

            payload() {
                const out = {};
                Object.keys(editors).forEach((key) => {
                    const ed = editors[key];
                    const text = ed.quill.getText().trim();
                    out[key] = {
                        content: text,
                        html_body: text ? ed.quill.root.innerHTML : '',
                        is_public: ed.isPublic,
                    };
                });
                return out;
            },
        };
    }

    // Cambia de paso en los modales de fusión en bloque (lista y home): los
    // contenedores llevan data-step y los botones del pie se pasan en `buttons`.
    function setStep(modalEl, n, buttons) {
        modalEl.querySelectorAll('.merge-step[data-step]').forEach((el) => {
            el.hidden = el.dataset.step !== String(n);
        });
        const label = modalEl.querySelector('[data-role="step-label"]');
        if (label) label.textContent = 'Paso ' + n + ' de 2';
        const bar = modalEl.querySelector('[data-role="step-bar-2"]');
        if (bar) bar.classList.toggle('is-done', n === 2);
        if (buttons.cancel) buttons.cancel.hidden = n !== 1;
        if (buttons.next) buttons.next.hidden = n !== 1;
        if (buttons.back) buttons.back.hidden = n !== 2;
        if (buttons.confirm) {
            buttons.confirm.hidden = n !== 2;
            buttons.confirm.disabled = false;
        }
    }

    window.TFMergeReview = {
        setStep,
        // Una instancia por modal: los panes de ticket se cachean y se vuelven
        // a montar, y los Quill ya creados tienen que conservarse.
        for(modalEl) {
            if (!modalEl.__tfMergeReview) modalEl.__tfMergeReview = create(modalEl);
            return modalEl.__tfMergeReview;
        },
    };
})();
