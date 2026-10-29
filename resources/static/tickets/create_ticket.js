// app/static/tickets/create_ticket.js
//
// Mensajes comunes de Select2, incluidos los campos con búsqueda remota.
if (window.jQuery?.fn.select2) {
    window.jQuery.fn.select2.defaults.set('language', {
        errorLoading: () => 'No se han podido cargar los resultados.',
        inputTooShort: args => {
            const remaining = args.minimum - args.input.length;
            return `Escribe ${remaining} ${remaining === 1 ? 'carácter más' : 'caracteres más'} para buscar.`;
        },
        inputTooLong: args => `Elimina ${args.input.length - args.maximum} caracteres.`,
        loadingMore: () => 'Cargando más resultados…',
        maximumSelected: args => `Puedes seleccionar hasta ${args.maximum} elementos.`,
        noResults: () => 'No se han encontrado resultados.',
        searching: () => 'Buscando…',
        removeAllItems: () => 'Quitar todos los elementos',
    });
}
// Pane-scoped initializer: invoked once per ticket pane (either deep-link page
// load or fragment fetch via tabs.js). Replaces the global DOMContentLoaded
// pattern so multiple panes can coexist mounted in the DOM at once.
//
// Inside this function:
//   - `rootEl` is the .ticket-pane DOM element this pane belongs to
//   - `$root` is the jQuery-wrapped pane
//   - `ctx`   is { ticketId, currentUserId, addCommentUrl, mergeTicketUrl, searchUrl }
// All DOM queries for elements inside the pane are scoped to $root / rootEl.
// References to window.ticketId have been replaced with ctx.ticketId.

// Estado que propone "Publicar como": en on_hold es open, porque comentar o
// editar interrumpe la espera de cierre (#21384); volver a on_hold hay que elegirlo.
function tfPublishStatusFor(status) {
    return status === 'on_hold' ? 'open' : status;
}
window.tfPublishStatusFor = tfPublishStatusFor;

// Shared Select2 config. Used by initTicketPane (first mount) and by
// tfApplyPaneSelect2 (defensive re-init after a pane was detached and
// re-attached by tabs.js — Select2 widget state doesn't survive that round-trip
// reliably, so we destroy + re-init).
const _tfUserAjaxConfig = {
    url: '/api/users/search/',
    dataType: 'json',
    delay: 200,
    data: params => ({ q: params.term || '', page: params.page || 1 }),
    processResults: data => _tfWithUserTitles(data),
    cache: true,
};

// Select2 usa `title` como tooltip del valor elegido: ahí va el email, que en
// el campo no cabe junto al nombre.
function _tfWithUserTitles(data) {
    const results = ((data && data.results) || []).map(u => (u.email ? Object.assign({ title: u.email }, u) : u));
    return { results, pagination: data && data.pagination };
}

// Opción de la lista de usuarios: nombre y, debajo, el email en pequeño. Los
// dos se cortan con «…» para que el desplegable no se ensanche.
function _tfUserResult(item) {
    if (item.loading || !item.id) return item.text;
    if (item.id === _TF_NEW_USER_ID) {
        const add = document.createElement('span');
        add.className = 'tf-select2-add-user';
        add.innerHTML = '<i class="fas fa-user-plus" aria-hidden="true"></i> ';
        add.appendChild(document.createTextNode('Añadir usuario'));
        return add;
    }
    const el = document.createElement('span');
    el.className = 'tf-user-opt';
    const name = document.createElement('span');
    name.className = 'tf-user-opt__name';
    name.textContent = item.text || '';
    el.appendChild(name);
    if (item.email) {
        const email = document.createElement('span');
        email.className = 'tf-user-opt__email';
        email.textContent = item.email;
        el.appendChild(email);
    }
    return el;
}

// «Solicitante» y «CCs» con alta rápida: la primera opción de la lista es «+ Añadir
// usuario», que abre TFQuickUser (quick_user.js) en vez de seleccionarse. Lo
// escrito en el buscador llega prerrellenado al diálogo.
const _TF_NEW_USER_ID = '__tf_new_user__';

function _tfInitQuickCreateUserSelect(selectEl) {
    const $select = window.jQuery(selectEl);
    let lastTerm = '';
    $select.select2({
        width: '100%',
        placeholder: '-',
        allowClear: true,
        minimumInputLength: 0,
        ajax: Object.assign({}, _tfUserAjaxConfig, {
            processResults: (data, params) => {
                lastTerm = (params && params.term) || '';
                const results = _tfWithUserTitles(data).results;
                const more = !!(data && data.pagination && data.pagination.more);
                const addUser = { id: _TF_NEW_USER_ID, text: '+ Añadir usuario' };
                // Sin texto va arriba, a la vista. Con texto, al final: Select2
                // resalta la primera opción y Enter tiene que elegir a la persona
                // buscada, no abrir el alta.
                if (!lastTerm) {
                    if ((params.page || 1) === 1) results.unshift(addUser);
                } else if (!more) {
                    results.push(addUser);
                }
                return { results, pagination: data && data.pagination };
            },
        }),
        templateResult: _tfUserResult,
    });
    $select.on('select2:selecting', async (e) => {
        if (!e.params || !e.params.args || e.params.args.data.id !== _TF_NEW_USER_ID) return;
        e.preventDefault();
        $select.select2('close');
        if (!window.TFQuickUser) return;
        // Lo escrito en el buscador se aprovecha: si parece un email va al
        // email y, si no, al nombre.
        const term = lastTerm.trim();
        const isEmail = term.includes('@');
        const user = await window.TFQuickUser.open({ email: isEmail ? term : '', name: isEmail ? '' : term });
        if (!user) return;
        if (!$select.find('option[value="' + user.id + '"]').length) {
            const option = new Option(user.text, user.id, false, false);
            option.title = user.email || '';
            $select.append(option);
        }
        // 'change' como en una selección normal: a partir de aquí el pane lo
        // trata igual que si se hubiera elegido de la lista.
        if (selectEl.multiple) {
            // CCs: se añade a los que ya había, no los sustituye.
            const current = ($select.val() || []).map(String);
            if (!current.includes(String(user.id))) current.push(String(user.id));
            $select.val(current).trigger('change');
        } else {
            $select.val(String(user.id)).trigger('change');
        }
    });
}

window.tfApplyPaneSelect2 = function (paneEl, opts) {
    if (!paneEl || !window.jQuery) return;
    const force = !!(opts && opts.force);
    const $root = window.jQuery(paneEl);
    const $selects = $root.find('select.select2');
    let inited = 0;
    let skipped = 0;
    $selects.each(function () {
        try {
            const isAjaxUser = window.jQuery(this).data('select2Type') === 'ajax-user';
            if (window.jQuery(this).hasClass('select2-hidden-accessible')) {
                if (force) {
                    try { window.jQuery(this).select2('destroy'); } catch (_) { /* ignore */ }
                } else {
                    skipped++;
                    return;
                }
            }
            if (isAjaxUser && this.dataset.quickCreate) {
                _tfInitQuickCreateUserSelect(this);
            } else if (isAjaxUser) {
                window.jQuery(this).select2({
                    width: '100%',
                    placeholder: '-',
                    allowClear: true,
                    minimumInputLength: 0,
                    ajax: _tfUserAjaxConfig,
                    templateResult: _tfUserResult,
                });
            } else if (this.id === 'tags') {
                window.jQuery(this).select2({
                    width: '100%',
                    tags: true,
                    tokenSeparators: [','],
                    placeholder: 'Selecciona o añade etiquetas',
                    minimumInputLength: 1,
                    ajax: {
                        url: '/api/tags/',
                        dataType: 'json',
                        delay: 250,
                        data: params => ({ q: params.term || '' }),
                        processResults: data => ({ results: (data.results || data || []).map(t => ({ id: t.id || t.name || t, text: t.name || t })) }),
                        cache: true,
                    },
                });
            } else {
                // La cruz de «limpiar» solo se ofrece donde el vacío es un valor
                // válido: el select tiene opción vacía y no es obligatorio
                // (#producto lo es). Sin opción vacía, Select2 dejaría el campo
                // en la primera opción real al pulsar la cruz.
                const clearable = !this.multiple
                    && !this.required
                    && !!this.querySelector('option[value=""]');
                window.jQuery(this).select2({
                    width: '100%',
                    placeholder: window.jQuery(this).attr('placeholder') || 'Selecciona una opción',
                    allowClear: clearable,
                });
            }
            inited++;
        } catch (e) {
            console.error('[tfApplyPaneSelect2] Select2 failed on', this.id || this.name, e);
        }
    });
    console.debug('[tfApplyPaneSelect2]', { paneTicketId: paneEl.getAttribute('data-ticket-id'), total: $selects.length, inited, skipped, force });
};

window.initTicketPane = function (root, ctx) {
    const rootEl = (root && root.jquery) ? root[0] : root;
    const $root  = (root && root.jquery) ? root   : window.jQuery(rootEl);
    ctx = ctx || {};
    if (ctx.ticketId == null) {
        ctx.ticketId = rootEl ? rootEl.getAttribute('data-ticket-id') : null;
        if (ctx.ticketId === '' || ctx.ticketId === 'null') ctx.ticketId = null;
        if (ctx.ticketId != null) ctx.ticketId = parseInt(ctx.ticketId, 10) || ctx.ticketId;
    }

    function getCookie(name) {
        let cookieValue = null;
        if (document.cookie && document.cookie !== '') {
            const cookies = document.cookie.split(';');
            for (let i = 0; i < cookies.length; i++) {
                const cookie = cookies[i].trim();
                if (cookie.substring(0, name.length + 1) === (name + '=')) {
                    cookieValue = decodeURIComponent(cookie.substring(name.length + 1));
                    break;
                }
            }
        }
        return cookieValue;
    }
    // Alias for the AJAX config used by the legacy #tags re-init block below.
    const _userAjaxConfig = _tfUserAjaxConfig;

    // Initial Select2 application — delegated to the module-level
    // tfApplyPaneSelect2 so tabs.js can re-run the same logic after detach/re-attach.
    window.tfApplyPaneSelect2(rootEl);

    // Pane-scoped Quill init. Required for SPA-loaded panes: quill-composer.js
    // only runs its DOMContentLoaded handler once per page, so panes mounted
    // after first load (sidebar → ticket, or new ticket via tab) would never
    // get a working composer without this call.
    let paneComposer = null;
    if (window.QuillComposer && typeof window.QuillComposer.initFor === 'function') {
        paneComposer = window.QuillComposer.initFor(rootEl);
    }

    function focusValidationTarget(selector) {
        if (selector === '#new-message') {
            if (paneComposer && typeof paneComposer.focus === 'function') {
                paneComposer.focus();
                return;
            }
            rootEl.querySelector('#quill-editor .ql-editor')?.focus();
            return;
        }

        const field = rootEl.querySelector(selector);
        if (!field) return;
        const select2Selection = field.nextElementSibling?.querySelector?.('.select2-selection');
        if (select2Selection) select2Selection.focus();
        else field.focus();
    }

    function warnMessageRequired(action) {
        window.toast.warning(`Escribe un mensaje o adjunta un archivo antes de ${action}.`);
        focusValidationTarget('#new-message');
    }

    function validateNewTicketRequiredFields() {
        const required = [
            {
                selector: '#producto',
                value: (rootEl.querySelector('#producto')?.value || '').trim(),
                message: 'Selecciona un producto o servicio antes de crear el ticket.',
            },
            {
                selector: '#empresa',
                value: (rootEl.querySelector('#empresa')?.value || '').trim(),
                message: 'Selecciona una empresa antes de crear el ticket.',
            },
            {
                selector: '#subject',
                value: (rootEl.querySelector('#subject')?.value || '').trim(),
                message: 'Escribe un asunto antes de crear el ticket.',
            },
            {
                selector: '#new-message',
                value: (rootEl.querySelector('#new-message')?.value || '').trim() || attachments.hasFiles(),
                message: 'Escribe un mensaje o adjunta un archivo antes de crear el ticket.',
            },
        ];
        const missing = required.find(item => !item.value);
        if (!missing) return true;

        window.toast.warning(missing.message);
        focusValidationTarget(missing.selector);
        return false;
    }
    // A separate controller per pane owns uploads, drafts and previews.
    const attachments = window.TicketAttachments.mount(rootEl, {
        ticketId: ctx.ticketId, getCsrfToken: () => getCookie('csrftoken'),
    });
    let submissionBusy = false;
    let lastAttempt = null;

    function setSubmissionBusy(busy) {
        submissionBusy = busy;
        attachments.lock(busy);
        rootEl.querySelectorAll('#publish-button, #send-message-btn, #dropdown-button, #visBtn, .vis-option')
            .forEach(button => { button.disabled = busy; });
        paneComposer?.quill?.enable(!busy);
        rootEl.setAttribute('aria-busy', String(busy));
    }

    function attachmentsReady() {
        if (attachments.ready()) return true;
        window.toast.warning('Espera a que terminen las subidas. Reintenta o retira los archivos con error antes de publicar.');
        return false;
    }

    function requestIdFor(payload) {
        const fingerprint = JSON.stringify(payload);
        if (!lastAttempt || lastAttempt.fingerprint !== fingerprint) {
            lastAttempt = { fingerprint, id: crypto.randomUUID() };
        }
        return lastAttempt.id;
    }

    // Con la respuesta, explica también los fallos sin JSON (WAF, sesión, caída).
    function responseError(data, fallback, response) {
        const describe = window.TicketAttachments?.failureMessage;
        if (response && describe) return describe(response.status, response.headers.get('server'), data, fallback);
        return data?.detail || data?.error?.message
            || (typeof data?.error === 'string' ? data.error : '') || fallback;
    }


    // ---- Desplegable Público/Interno ----
    const isPublicInput = rootEl.querySelector('#is_public_input');
    const visBtn = rootEl.querySelector('#visBtn');
    const visMenu = rootEl.querySelector('#visMenu');

    function setVisibility(publicVal) {
        const isPub = publicVal === true || publicVal === 'true';
        if (isPublicInput) isPublicInput.value = String(isPub);
        if (visBtn) {
            const label = visBtn.querySelector('.vis-label');
            const icon = visBtn.querySelector('i.fas:not(.caret)');
            if (label) label.textContent = isPub ? 'Público' : 'Interno';
            if (icon) icon.className = isPub ? 'fas fa-eye' : 'fas fa-user-shield';
        }
    }

    if (visBtn && visMenu) {
        // Estado inicial (desde hidden)
        setVisibility(isPublicInput ? isPublicInput.value : 'true');

        // Abrir/cerrar
        visBtn.addEventListener('click', (e) => {
            e.stopPropagation();
            const open = visBtn.parentElement.classList.toggle('open');
            visBtn.setAttribute('aria-expanded', open ? 'true' : 'false');
            if (open) {
                const first = visMenu.querySelector('.vis-option');
                first && first.focus();
            }
        });

        // Selección de opción
        visMenu.querySelectorAll('.vis-option').forEach((opt) => {
            opt.addEventListener('click', () => {
                const val = opt.dataset.public === 'true';
                setVisibility(val);
                visBtn.parentElement.classList.remove('open');
                visBtn.setAttribute('aria-expanded', 'false');
                visBtn.focus();
            });
        });

        // Cerrar al hacer click fuera o con ESC
        document.addEventListener('click', () => {
            if (visBtn.parentElement.classList.contains('open')) {
                visBtn.parentElement.classList.remove('open');
                visBtn.setAttribute('aria-expanded', 'false');
            }
        });
        document.addEventListener('keydown', (e) => {
            if (e.key === 'Escape' && visBtn.parentElement.classList.contains('open')) {
                visBtn.parentElement.classList.remove('open');
                visBtn.setAttribute('aria-expanded', 'false');
                visBtn.focus();
            }
        });
    }

    // Both actions share the same safe rendering and attachment association.
    function appendPublishedComment(data) {
        const box = rootEl.querySelector('#messagesBox');
        if (!box || box.querySelector('[data-comment-id="' + Number(data.id) + '"]')) return;
        const element = (tag, className, value) => {
            const node = document.createElement(tag);
            if (className) node.className = className;
            if (value != null) node.textContent = value;
            return node;
        };
        const message = element('div', 'message' +
            (Number(data.user_id) === Number(ctx.currentUserId) ? ' me' : '') +
            (data.is_public ? '' : ' internal'));
        message.dataset.commentId = data.id;
        const header = element('div', 'msg-header');
        const name = data.username || 'Usuario';
        const avatar = element('div', 'msg-avatar', name.charAt(0).toUpperCase());
        if (data.user_photo_url) {
            const photo = document.createElement('img');
            photo.className = 'msg-avatar-photo'; photo.alt = '';
            photo.addEventListener('error', () => photo.remove(), {once:true});
            photo.src = data.user_photo_url;
            avatar.appendChild(photo);
        }
        const palette = ['#5b67ca','#e06c75','#56b6c2','#98c379','#d19a66','#c678dd','#61afef','#e5c07b'];
        let hash = 0;
        for (const character of name) hash = character.charCodeAt(0) + ((hash << 5) - hash);
        avatar.style.backgroundColor = palette[Math.abs(hash) % palette.length];
        const meta = element('div', 'msg-meta');
        const author = element('a', 'msg-author', name);
        author.href = (window.urls?.customer_profile || '/customers/profile/') + '?id=' + Number(data.user_id);
        meta.appendChild(author);
        if (data.user_role) meta.appendChild(element('span', 'msg-role', data.user_role));
        const right = element('div', 'msg-right');
        if (!data.is_public) right.appendChild(element('span', 'badge-internal', 'Interno'));
        const timestamp = element('span', 'msg-timestamp', 'hace un momento');
        timestamp.dataset.ts = data.created_at_iso || '';
        timestamp.title = data.created_at || '';
        right.appendChild(timestamp);
        header.append(avatar, meta, right);
        message.appendChild(header);
        if (['agent', 'customer', 'internal'].includes(data.speaker_kind)) message.classList.add('speaker-' + data.speaker_kind);
        if (data.content || data.html_body) {
            const body = element('div', 'msg-body');
            if (data.conversation_html) {
                // Misma plantilla saneada que la carga inicial y los fragmentos SPA.
                body.innerHTML = data.conversation_html;
            } else if (data.html_body) {
                const html = element('div', 'email-html');
                // Only the server-sanitized comment HTML enters this surface.
                html.innerHTML = data.html_body;
                body.appendChild(html);
            } else {
                body.textContent = data.content || '';
                body.style.whiteSpace = 'pre-wrap';
            }
            message.appendChild(body);
        }
        if (data.attachments?.length) {
            const files = element('div', 'tf-attachments');
            window.TicketAttachments.render(files, data.attachments);
            message.appendChild(files);
        }
        box.appendChild(message);
        scrollToLatestMessage(box);
    }

    async function createTicketWithAttachments() {
        if (submissionBusy) return;
        if (rootEl.dataset.createdTicketId) {
            window.openTicket?.(rootEl.dataset.createdTicketId);
            return;
        }
        if (!validateNewTicketRequiredFields() || !attachmentsReady()) return;
        const form = rootEl.querySelector('#ticket-form');
        const fd = new FormData(form);
        fd.set('status', rootEl.querySelector('#selected-status')?.textContent?.trim() || 'open');
        fd.set('message', rootEl.querySelector('#new-message')?.value || '');
        fd.set('html_body', paneComposer?.getHtml?.() || '');
        fd.set('is_public', isPublicInput?.value || 'true');
        fd.set('attachment_ids', JSON.stringify(attachments.ids()));
        fd.set('draft_key', attachments.draftKey);
        fd.set('request_id', requestIdFor(Array.from(fd.entries()).filter(([key]) => key !== 'csrfmiddlewaretoken')));
        setSubmissionBusy(true);
        try {
            const response = await fetch(form.action, {
                method: 'POST', body: fd,
                headers: {'X-Requested-With':'XMLHttpRequest', 'X-CSRFToken':getCookie('csrftoken')},
            });
            const data = await response.json().catch(() => ({}));
            if (!response.ok || !data.ticket_id) {
                window.toast.error(responseError(data, 'No se pudo crear el ticket', response) + ' Tu borrador se conserva.');
                return;
            }
            attachments.clear();
            paneComposer?.clear();
            rootEl.dataset.createdTicketId = String(data.ticket_id);
            const oldKey = Array.from(window.Tabs?.paneCache?.entries() || [])
                .find(([, entry]) => entry.$pane === rootEl)?.[0];
            const oldTab = oldKey ? Array.from(document.querySelectorAll('.navbar-left .tab'))
                .find(tab => window.Tabs.normalizeUrl(tab.dataset.url || '') === oldKey) : null;
            setSubmissionBusy(false);
            if (window.openTicket) {
                window.openTicket(data.ticket_id, rootEl.querySelector('#subject')?.value);
                // Only close this submitted draft, never the drafts in other tabs.
                if (oldTab && !oldTab.classList.contains('active')) oldTab.querySelector('.close-tab')?.click();
            } else {
                window.location.assign(data.url || '/tickets/create/?id=' + Number(data.ticket_id));
            }
        } catch (_) {
            window.toast.error('No se pudo confirmar la publicación. El borrador se conserva; reintentar no duplicará el ticket.');
        } finally {
            setSubmissionBusy(false);
        }
    }

    async function submitComment(ev, updateFields) {
        if (ev) { ev.preventDefault(); ev.stopPropagation(); }
        if (submissionBusy) return;
        if (!ctx.ticketId) return createTicketWithAttachments();
        const lock = rootEl.querySelector('#ticket-form')?.dataset.locked;
        if (lock) {
            window.toast.warning(lock === 'readonly'
                ? 'Tu rol es de solo lectura: no puedes modificar el ticket.'
                : 'El ticket está cerrado y ya no admite cambios.');
            return;
        }
        if (!attachmentsReady()) return;
        const textarea = rootEl.querySelector('#new-message');
        const content = (textarea?.value || '').trim();
        const newStatus = (rootEl.querySelector('#selected-status')?.textContent || 'open').trim().toLowerCase();
        if (!content && !attachments.hasFiles()) {
            // El estado no es un campo del formulario: cambiarlo a secas también
            // es un cambio que se puede guardar sin escribir mensaje.
            const serverStatus = rootEl.__tfLive?.serverStatus;
            const statusChanged = !!serverStatus && newStatus !== serverStatus;
            const fieldsChanged = hasTicketFieldChanges();
            if (!updateFields || (!fieldsChanged && !statusChanged)) { warnMessageRequired('publicar'); return; }
            setSubmissionBusy(true);
            let saved = false;
            try {
                // Con otros campos cambiados el formulario ya lleva el estado; sin
                // ellos, la ruta de solo estado no exige producto.
                saved = fieldsChanged ? await saveTicketFields(newStatus) : await saveStatusOnly(newStatus);
                if (saved) {
                    rootEl.querySelector('#selected-status').textContent = tfPublishStatusFor(newStatus);
                    window.toast.success('Cambios del ticket guardados.');
                }
            } finally { setSubmissionBusy(false); }
            if (saved) returnToQueue(newStatus);
            return;
        }
        const payload = {
            content, is_public: isPublicInput ? isPublicInput.value === 'true' : true,
            html_body: paneComposer?.getHtml?.() || '', attachment_ids: attachments.ids(),
        };
        if (updateFields) payload.new_status = newStatus;
        payload.request_id = requestIdFor(payload);
        setSubmissionBusy(true);
        let publishedStatus = null;
        try {
            // El botón de enviar también guarda los campos: un CC añadido a mano
            // tiene que estar en el ticket antes de encolar la respuesta o no la
            // recibe. Los campos viajan sin estado: el estado va con el comentario,
            // para que la transición (y el aviso de on_hold) ocurra una sola vez.
            if (!await saveTicketFields(null)) return;
            const response = await fetch('/tickets/' + ctx.ticketId + '/add_comment/', {
                method:'POST',
                headers:{'Content-Type':'application/json','X-CSRFToken':getCookie('csrftoken')},
                body:JSON.stringify(payload),
            });
            const data = await response.json().catch(() => ({}));
            if (!response.ok) {
                window.toast.error(responseError(data, 'No se pudo publicar', response) + ' El texto y los adjuntos se conservan.');
                return;
            }
            appendPublishedComment(data);
            attachments.clear();
            paneComposer?.clear();
            textarea.value = '';
            lastAttempt = null;
            if (data.new_status) rootEl.querySelector('#selected-status').textContent = tfPublishStatusFor(data.new_status);
            window.tfApplyTicketHeader?.(rootEl, data.ticket);
            publishedStatus = data.new_status || newStatus;
        } catch (_) {
            window.toast.error('No se pudo confirmar la publicación. Reintenta; no se duplicará el mensaje.');
        } finally { setSubmissionBusy(false); }
        // El botón de enviar es para conversar: solo "Publicar como" actualiza el
        // ticket y devuelve a la cola.
        if (publishedStatus && updateFields) returnToQueue(publishedStatus);
    }

    // #21382: tras actualizar un ticket se vuelve a «Todos los tickets no
    // resueltos». Si queda pendiente, resuelto u on-hold ya no hace falta tenerlo
    // abierto, así que además se cierra su pestaña.
    function returnToQueue(status) {
        if (!window.Tabs?.navigate) return;
        const listUrl = ((window.urls && window.urls.tickets_list) || '/tickets/') + '?view=all_unsolved_no_tareas';
        if (['pending', 'resolved', 'on_hold'].includes(status)) {
            const tabUrl = ((window.urls && window.urls.create_ticket) || '/tickets/create/') + '?id=' + ctx.ticketId;
            window.Tabs.closeTab(tabUrl, {navigateTo: listUrl});
        } else {
            window.Tabs.navigate(listUrl);
        }
    }

    const sendMessage = ev => submitComment(ev, false);

    // ---- Guardado de los campos del ticket ----
    // "Publicar como <estado>" solo publicaba el comentario en tickets ya
    // existentes: el <form id="ticket-form"> nunca se enviaba, así que cualquier
    // cambio en Asignado, Solicitante, Empresa, Tipo... se perdía en silencio (el
    // pane seguía mostrando el valor elegido porque nunca se recarga).
    //
    // Se envía el formulario solo si algo cambió respecto a como se cargó: así un
    // ticket sin producto —los que entran por email— sigue admitiendo respuestas
    // sin que la validación de "producto obligatorio" bloquee la publicación.
    let _formBaseline = null;

    function serializeTicketForm() {
        const form = rootEl.querySelector('#ticket-form');
        if (!form) return null;
        const params = new URLSearchParams(new FormData(form));
        params.delete('message');               // el cuerpo del comentario no es metadato
        params.delete('csrfmiddlewaretoken');
        return JSON.stringify([...params.entries()].sort(
            (a, b) => (a[0] === b[0] ? (a[1] > b[1] ? 1 : -1) : (a[0] > b[0] ? 1 : -1))
        ));
    }

    function hasTicketFieldChanges() {
        if (_formBaseline === null) return false;   // la carga inicial falló: no tocamos nada
        return serializeTicketForm() !== _formBaseline;
    }

    async function saveTicketFields(status) {
        const form = rootEl.querySelector('#ticket-form');
        if (!form || !hasTicketFieldChanges()) return true;

        const fd = new FormData(form);
        // El comentario lo publica add_comment (con HTML, adjuntos y visibilidad).
        // Si 'message' viajara aquí, update_ticket crearía un segundo comentario.
        fd.delete('message');
        // Sin 'status' el backend conserva el estado actual.
        if (status) fd.set('status', status);
        else fd.delete('status');

        try {
            const res = await fetch(form.action, {
                method: 'POST',
                body: fd,
                headers: { 'X-Requested-With': 'XMLHttpRequest' },
            });
            if (!res.ok) {
                let msg = 'No se pudieron guardar los cambios del ticket.';
                // json_error() responde {ok, error:{code,message}, detail}: 'error' es
                // un objeto, no una cadena.
                try {
                    const data = await res.json();
                    msg = data.detail || data.error?.message || (typeof data.error === 'string' ? data.error : '') || msg;
                } catch (_) { /* respuesta no JSON */ }
                window.toast.error(msg);
                return false;
            }
            _formBaseline = serializeTicketForm();
            try {
                window.tfApplyTicketHeader?.(rootEl, (await res.json()).ticket);
            } catch (_) { /* respuesta no JSON: la cabecera la arregla el sondeo */ }
            return true;
        } catch (err) {
            console.error('Error guardando los campos del ticket:', err);
            window.toast.error('Error de red al guardar los cambios del ticket.');
            return false;
        }
    }

    async function saveStatusOnly(status) {
        try {
            const res = await fetch('/tickets/' + ctx.ticketId + '/add_comment/', {
                method: 'POST',
                headers: {'Content-Type': 'application/json', 'X-CSRFToken': getCookie('csrftoken')},
                body: JSON.stringify({new_status: status}),
            });
            const data = await res.json().catch(() => ({}));
            if (!res.ok) {
                window.toast.error(responseError(data, 'No se pudo cambiar el estado del ticket.'));
                return false;
            }
            rootEl.querySelector('#selected-status').textContent = tfPublishStatusFor(data.new_status || status);
            window.tfApplyTicketHeader?.(rootEl, data.ticket);
            return true;
        } catch (_) {
            window.toast.error('Error de red al cambiar el estado del ticket.');
            return false;
        }
    }

    const publishComment = ev => submitComment(ev, true);

    const sendMessageButton = rootEl.querySelector('#send-message-btn');
    sendMessageButton?.addEventListener('click', sendMessage);
    const ticketForm = rootEl.querySelector('#ticket-form');
    ticketForm?.addEventListener('submit', event => {
        event.preventDefault();
        event.stopPropagation();
        publishComment(event);
    });

    // Manejo del Dropdown de Estado
    const manageStatusDropdown = () => {
        const publishButton = rootEl.querySelector('#publish-button');
        const dropdownButton = rootEl.querySelector('#dropdown-button');
        const dropdownContent = rootEl.querySelector('#dropdown-content');
        const selectedStatus = rootEl.querySelector('#selected-status');
        const ticketForm = rootEl.querySelector('#ticket-form');

        if (selectedStatus && dropdownContent) {
            const current = selectedStatus.textContent.trim().toLowerCase();
            dropdownContent.querySelectorAll('a').forEach(a => {
                a.classList.toggle('active', a.dataset.status.toLowerCase() === current);
            });
        }

        dropdownContent.addEventListener('click', (event) => {
            if (event.target.tagName === 'A') {
                const status = event.target.getAttribute('data-status');
                selectedStatus.textContent = status;
                dropdownContent.querySelectorAll('a').forEach(a => {
                    a.classList.toggle('active', a.dataset.status === status);
                });
                dropdownContent.classList.remove('show');
            }
        });

        dropdownButton.addEventListener('click', (event) => {
            event.preventDefault();
            event.stopPropagation();
            dropdownContent.classList.toggle('show');
        });

        window.addEventListener('click', (event) => {
            if (!event.target.matches('.dropdown-btn') && !event.target.closest('#dropdown-content')) {
                dropdownContent.classList.remove('show');
            }
        });

        publishButton.addEventListener('click', publishComment);
    };

    // Inicializar Dropdown de Estado
    manageStatusDropdown();

    // #tags Select2 already initialized by tfApplyPaneSelect2 (with tags:true,
    // ajax, placeholder, etc.). Here we only need to bind the select2:select
    // handler for creating new tags. Re-initializing here would double-init.
    $root.find('#tags').on('select2:select', function (e) {
        const data = e.params.data;
        if (data.newTag) {
            const newTag = data.text;
            const isValidTag = /^[a-zA-Z0-9]+$/.test(newTag);
            if (newTag && isValidTag) {
                let exists = false;
                $root.find('#tags option').each(function () {
                    if ($(this).val() === newTag) {
                        exists = true;
                        return false;
                    }
                });

                if (!exists) {
                    const newOption = new Option(newTag, newTag, true, true);
                    $root.find('#tags').append(newOption).trigger('change');
                }

                $root.find('#tags').find('option[value="create_new_tag"]').remove();
            } else {
                window.toast.warning('El tag solo puede contener letras y números, sin espacios en blanco.');
                $root.find('#tags').find('option[value="create_new_tag"]').remove();
            }
        }
    });

    // Manejo de Guardar y Cargar Datos del Formulario en localStorage
    // El id viene del pane (data-ticket-id, ya resuelto en ctx.ticketId). En el SPA
    // la URL es /tickets/N/ (sin ?id=), así que leer solo el query param dejaba
    // ticketId=null y NO se poblaba el formulario desde la API (campos vacíos
    // aunque la IA/los datos existieran). Fallback al query param por compatibilidad.
    const ticketId = ctx.ticketId || new URLSearchParams(window.location.search).get('id');

    const saveFormData = () => {
        if (!ticketId) return;
        const formData = {};
        $root.find('select.select2').each(function () {
            if ($(this).data('select2Type') === 'ajax-user') return; // la API repopula estos
            formData[this.id] = $(this).val();
        });
        localStorage.setItem(`ticketFormData_${ticketId}`, JSON.stringify(formData));
    };

    const loadFormData = () => {
        if (!ticketId) return;
        const formData = JSON.parse(localStorage.getItem(`ticketFormData_${ticketId}`));
        if (formData) {
            $root.find('select.select2').each(function () {
                if ($(this).data('select2Type') === 'ajax-user') return; // la API repopula estos
                if (formData[this.id]) {
                    $(this).val(formData[this.id]).trigger('change');
                }
            });
        }
    };

    $root.find('select.select2').on('change', saveFormData);
    loadFormData();

    // "Problema vinculado" solo aplica a incidencias (ITIL: un problema agrupa incidentes)
    const toggleProblemLink = () => {
        const isIncident = $root.find('#tipo').val() === 'incident';
        $root.find('#problemLinkGroup').toggle(isIncident);
        if (!isIncident) $root.find('#problem_id').val('');
    };
    $root.find('#tipo').on('change', toggleProblemLink);
    toggleProblemLink();

    // Abre un ticket en una pestaña del SPA (o navega si no hay Tabs).
    const openTicketTab = (id, subject) => {
        const url = window.location.pathname + '?id=' + id;
        if (window.Tabs && typeof window.Tabs.addTab === 'function') {
            window.Tabs.addTab(subject || ('Ticket ' + id), url);
        } else {
            window.location.href = url;
        }
    };

    // Pinta el contexto del vínculo problema↔incidencias (ITIL):
    //  - en una incidencia: "Problema vinculado" como enlace con su asunto;
    //  - en un problema: la lista de incidencias agrupadas, clicables.
    const escHtml = (s) => $('<div>').text(s == null ? '' : s).html();
    const renderProblemLinks = (data) => {
        const $ref = $root.find('#problemLinkRef');
        if (data.problem_id && data.problem_subject) {
            $ref.html('<span class="li-status status-' + escHtml(data.problem_status || '') + '"></span>' +
                      '#' + data.problem_id + ' · ' + escHtml(data.problem_subject))
                .attr('href', window.location.pathname + '?id=' + data.problem_id)
                .off('click').on('click', (e) => { e.preventDefault(); openTicketTab(data.problem_id, data.problem_subject); })
                .show();
        } else {
            $ref.hide();
        }

        const incidents = data.incidents || [];
        const $group = $root.find('#linkedIncidentsGroup');
        const $list = $root.find('#linkedIncidentsList');
        if (data.tipo === 'problem' && incidents.length) {
            $root.find('#linkedIncidentsCount').text('(' + incidents.length + ')');
            $list.html(incidents.map(inc =>
                '<li><a href="' + window.location.pathname + '?id=' + inc.id + '" data-id="' + inc.id + '">' +
                '<span class="li-status status-' + escHtml(inc.status) + '"></span>' +
                '#' + inc.id + ' · ' + escHtml(inc.subject) + '</a></li>'
            ).join(''));
            $list.find('a').off('click').on('click', function (e) {
                e.preventDefault();
                openTicketTab($(this).data('id'), '');
            });
            $group.show();
        } else {
            $group.hide();
            $list.empty();
        }
    };

    // Cargar Información del Ticket si Existe
    if (ticketId) {
        fetch(`/api/tickets/${ticketId}/`)
            .then(response => response.json())
            .then(data => {
                $root.find('#producto').val(data.producto || '').trigger('change');
                $root.find('#empresa').val(data.empresa || '').trigger('change');
                $root.find('#asignado').val(data.asignado || '').trigger('change');
                $root.find('#grupo').val(data.grupo || '').trigger('change');
                $root.find('#tags').val(data.tags).trigger('change');
                $root.find('#tipo').val(data.tipo).trigger('change');
                $root.find('#problem_id').val(data.problem_id || '');
                toggleProblemLink();
                renderProblemLinks(data);
                $root.find('#prioridad').val(data.prioridad || '').trigger('change');
                $root.find('#servicio').val(data.servicio).trigger('change');
                $root.find('#canal').val(data.canal).trigger('change');
                $root.find('#idioma').val(data.idioma).trigger('change');
                // categoria es texto libre (la IA puede proponer una fuera del listado
                // fijo). Si el valor no existe como <option>, lo añadimos para que se
                // muestre y se conserve al guardar.
                if (data.categoria) {
                    const $cat = $root.find('#categoria');
                    if (!$cat.find(`option[value="${data.categoria}"]`).length) {
                        $cat.append(new Option(data.categoria, data.categoria, true, true));
                    }
                    $cat.val(data.categoria).trigger('change');
                } else {
                    $root.find('#categoria').val('').trigger('change');
                }
                $root.find('#security_related').prop('checked', !!data.security_related);
                $root.find('#monitoring').prop('checked', !!data.monitoring);
                $root.find('#approval_status').val(data.approval_status || '');
                $root.find('#subject').val(data.subject);

                // Selects AJAX: añadir la opción inicial programáticamente porque no hay
                // <option> pre-renderizadas. El template ya incluye la opción inicial si
                // el ticket tiene requester/ccs, pero la API puede corregirlos si difieren.
                if (data.solicitante) {
                    const $sel = $root.find('#solicitante');
                    if (!$sel.find(`option[value="${data.solicitante}"]`).length) {
                        $sel.append(new Option(data.solicitante_name || data.solicitante, data.solicitante, true, true));
                    }
                    $sel.val(data.solicitante).trigger('change');
                }
                if (data.ccs && data.ccs.length) {
                    const $ccs = $root.find('#ccs');
                    $ccs.find('option').remove();
                    data.ccs.forEach(cc => {
                        const id = cc.id !== undefined ? cc.id : cc;
                        const name = cc.name || id;
                        $ccs.append(new Option(name, id, true, true));
                    });
                    $ccs.trigger('change');
                }
                // Estado "tal como se cargó": referencia para saber al publicar
                // qué campos tocó el usuario.
                _formBaseline = serializeTicketForm();
            })
            .catch(error => console.error('Error al cargar el ticket:', error));
    }

    // El comienzo del último mensaje debe quedar visible, incluso si es largo.
    // Ir al final mostraba firmas/controles en vez de la respuesta del remitente.
    function scrollToLatestMessage(box) {
        const messages = box.querySelectorAll(':scope > .message');
        const last = messages[messages.length - 1];
        if (last) box.scrollTop += last.getBoundingClientRect().top - box.getBoundingClientRect().top - 12;
    }

    // ---- Scroll al último comentario ----
    const messagesBox = rootEl.querySelector('#messagesBox');
    if (messagesBox) scrollToLatestMessage(messagesBox);

    // ---- Avatares: color por nombre ----
    function avatarColor(name) {
        const palette = [
            '#5b67ca','#e06c75','#56b6c2','#98c379',
            '#d19a66','#c678dd','#61afef','#e5c07b',
        ];
        let hash = 0;
        for (let i = 0; i < name.length; i++) hash = name.charCodeAt(i) + ((hash << 5) - hash);
        return palette[Math.abs(hash) % palette.length];
    }

    rootEl.querySelectorAll('.msg-avatar-photo').forEach(photo => {
        photo.addEventListener('error', () => photo.remove(), {once:true});
        if (photo.complete && !photo.naturalWidth) photo.remove();
    });

    rootEl.querySelectorAll('.msg-avatar').forEach(el => {
        const authorName = el.closest('.message')?.querySelector('.msg-author')?.textContent.trim() || '?';
        el.style.background = avatarColor(authorName);
    });

    // Avatar del side panel del solicitante (gris neutro fijo, sin color por nombre)
    rootEl.querySelectorAll('.rq-avatar-initials').forEach(el => {
        el.style.background = '#8a9ba8';
    });

    // Auto-guardar notas del solicitante al perder el foco
    rootEl.querySelectorAll('.rq-notes-input').forEach(textarea => {
        let original = textarea.value;
        textarea.addEventListener('blur', () => {
            if (textarea.value === original) return;
            const userId = textarea.dataset.userId;
            fetch(`/api/users/${userId}/notes/`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-CSRFToken': getCookie('csrftoken'),
                },
                body: JSON.stringify({ notes: textarea.value }),
            })
            .then(r => { if (r.ok) original = textarea.value; })
            .catch(() => {});
        });
    });

    // El historial y las firmas usan <details> en la plantilla compartida.
    // Las citas normales e intercaladas siguen visibles y no requieren listeners.

    // ---- Macros panel ----
    const macroBtn    = rootEl.querySelector('#macro-btn');
    const macroPanel  = rootEl.querySelector('#macro-panel');
    const macroList   = rootEl.querySelector('#macro-list');
    const macroSearch = rootEl.querySelector('#macro-search');
    const macroCaret  = rootEl.querySelector('#macro-caret');

    if (macroBtn && macroPanel) {
        let macrosCache = null;

        function renderMacros(list) {
            macroList.innerHTML = '';
            if (!list.length) {
                macroList.innerHTML = '<li class="macro-empty">Sin resultados</li>';
                return;
            }
            list.forEach(m => {
                const li = document.createElement('li');
                li.className = 'macro-item';
                li.innerHTML = `<div class="macro-item-name">${m.name}</div>
                    ${m.description ? `<div class="macro-item-desc">${m.description}</div>` : ''}`;
                li.addEventListener('click', () => applyMacro(m));
                macroList.appendChild(li);
            });
        }

        function applyMacro(m) {
            const a = m.actions || {};

            // Status
            if (a.status) {
                const statusSpan = rootEl.querySelector('#selected-status');
                if (statusSpan) statusSpan.textContent = a.status;
            }

            // Priority
            if (a.priority && typeof $ !== 'undefined') {
                $root.find('#prioridad').val(a.priority).trigger('change');
            }

            // Assignee
            if (a.assignee_id != null && typeof $ !== 'undefined') {
                $root.find('#asignado').val(String(a.assignee_id)).trigger('change');
            }

            // Canned comment
            if (a.comment) {
                const composer = rootEl.querySelector('#quill-editor')?.__quillComposer;
                if (composer) {
                    // Insertar conserva el formato, las imágenes y el borrador existente.
                    const quill = composer.quill;
                    const index = quill.getSelection()?.index ?? Math.max(0, quill.getLength() - 1);
                    const text = (index > 0 ? '\n' : '') + a.comment + '\n';
                    quill.insertText(index, text, 'user');
                    quill.setSelection(index + text.length, 0);
                    quill.focus();
                } else {
                    const ta = rootEl.querySelector('#new-message');
                    if (ta) {
                        ta.value += (ta.value ? '\n' : '') + a.comment;
                        ta.dispatchEvent(new Event('input', {bubbles: true}));
                        ta.focus();
                    }
                }
            }

            closeMacroPanel();
        }

        function closeMacroPanel() {
            macroPanel.classList.remove('open');
            macroBtn.classList.remove('open');
        }

        macroBtn.addEventListener('click', async (e) => {
            e.stopPropagation();
            const isOpen = macroPanel.classList.toggle('open');
            macroBtn.classList.toggle('open', isOpen);
            if (isOpen && !macrosCache) {
                macroList.innerHTML = '<li class="macro-empty">Cargando…</li>';
                try {
                    const res = await fetch('/api/macros/');
                    const data = await res.json();
                    macrosCache = data.macros || [];
                    renderMacros(macrosCache);
                } catch {
                    macroList.innerHTML = '<li class="macro-empty">Error al cargar</li>';
                }
            } else if (isOpen) {
                renderMacros(macrosCache);
            }
        });

        macroSearch && macroSearch.addEventListener('input', () => {
            if (!macrosCache) return;
            const q = macroSearch.value.toLowerCase();
            renderMacros(macrosCache.filter(m =>
                m.name.toLowerCase().includes(q) ||
                (m.description || '').toLowerCase().includes(q)
            ));
        });

        document.addEventListener('click', (e) => {
            if (!macroPanel.contains(e.target) && !macroBtn.contains(e.target)) {
                closeMacroPanel();
            }
        });

        // ---- Gestión de macros (CRUD) ----
        const manageBtn   = rootEl.querySelector('#macro-manage-btn');
        const modal       = rootEl.querySelector('#macro-modal');
        if (manageBtn && modal) {
            const mList   = modal.querySelector('#macro-manage-list');
            const mSearch = modal.querySelector('#macro-manage-search');
            const form    = modal.querySelector('#macro-form');
            const fId      = modal.querySelector('#macro-form-id');
            const fName    = modal.querySelector('#macro-form-name');
            const fDesc    = modal.querySelector('#macro-form-desc');
            const fStatus  = modal.querySelector('#macro-form-status');
            const fPrio    = modal.querySelector('#macro-form-priority');
            const fComment = modal.querySelector('#macro-form-comment');
            const fActive  = modal.querySelector('#macro-form-active');
            const delBtn   = modal.querySelector('#macro-delete-btn');
            let manageCache = [];

            const toast = (kind, msg) => (window.toast && window.toast[kind]) ? window.toast[kind](msg) : null;
            const esc = (s) => String(s == null ? '' : s).replace(/[&<>"]/g, c => (
                { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

            function renderManageList() {
                const q = (mSearch.value || '').toLowerCase();
                const list = manageCache.filter(m =>
                    m.name.toLowerCase().includes(q) ||
                    (m.description || '').toLowerCase().includes(q));
                if (!list.length) {
                    mList.innerHTML = '<li class="macro-empty">Sin macros</li>';
                    return;
                }
                const sel = fId.value;
                mList.innerHTML = list.map(m => {
                    const a = m.actions || {};
                    const bits = [a.status, a.priority, a.comment ? 'comentario' : null]
                        .filter(Boolean).join(' · ');
                    return `<li class="macro-manage-item${String(m.id) === sel ? ' selected' : ''}${m.active ? '' : ' inactive'}" data-id="${m.id}">
                        <div class="mm-name">${esc(m.name)}${m.active ? '' : ' <span class="mm-badge">inactiva</span>'}</div>
                        ${bits ? `<div class="mm-actions">${esc(bits)}</div>` : ''}
                    </li>`;
                }).join('');
                mList.querySelectorAll('.macro-manage-item').forEach(li => {
                    li.addEventListener('click', () => {
                        const m = manageCache.find(x => String(x.id) === li.dataset.id);
                        if (m) loadForm(m);
                    });
                });
            }

            function loadForm(m) {
                const a = (m && m.actions) || {};
                fId.value      = m ? m.id : '';
                fName.value    = m ? m.name : '';
                fDesc.value    = m ? (m.description || '') : '';
                fStatus.value  = a.status || '';
                fPrio.value    = a.priority || '';
                fComment.value = a.comment || '';
                fActive.checked = m ? !!m.active : true;
                delBtn.style.display = m ? '' : 'none';
                form.style.display = '';
                renderManageList();
                fName.focus();
            }

            async function loadManage() {
                mList.innerHTML = '<li class="macro-empty">Cargando…</li>';
                try {
                    const res = await fetch('/api/macros/manage/');
                    const data = await res.json();
                    manageCache = data.macros || [];
                    renderManageList();
                } catch {
                    mList.innerHTML = '<li class="macro-empty">Error al cargar</li>';
                }
            }

            function openModal() {
                closeMacroPanel();
                modal.classList.add('open');
                form.style.display = 'none';
                fId.value = '';
                mSearch.value = '';
                loadManage();
            }
            function closeModal() { modal.classList.remove('open'); }

            manageBtn.addEventListener('click', (e) => { e.stopPropagation(); openModal(); });
            modal.querySelector('#macro-modal-close').addEventListener('click', closeModal);
            modal.querySelector('#macro-form-cancel').addEventListener('click', () => {
                form.style.display = 'none'; fId.value = ''; renderManageList();
            });
            modal.querySelector('#macro-new-btn').addEventListener('click', () => loadForm(null));
            modal.addEventListener('click', (e) => { if (e.target === modal) closeModal(); });
            mSearch.addEventListener('input', renderManageList);

            form.addEventListener('submit', async (e) => {
                e.preventDefault();
                const name = fName.value.trim();
                if (!name) { toast('error', 'El nombre es obligatorio'); return; }
                if (!fStatus.value && !fPrio.value && !fComment.value.trim()) {
                    toast('error', 'La macro debe tener al menos una acción'); return;
                }
                const isEdit = !!fId.value;
                const payload = {
                    id: isEdit ? Number(fId.value) : undefined,
                    name,
                    description: fDesc.value.trim(),
                    status: fStatus.value,
                    priority: fPrio.value,
                    comment: fComment.value.trim(),
                    active: fActive.checked,
                };
                try {
                    const res = await fetch('/api/macros/manage/', {
                        method: isEdit ? 'PUT' : 'POST',
                        headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCookie('csrftoken') },
                        body: JSON.stringify(payload),
                    });
                    const data = await res.json();
                    if (!res.ok || !data.ok) { toast('error', data.detail || (data.error && data.error.message) || 'Error al guardar'); return; }
                    toast('success', isEdit ? 'Macro actualizada' : 'Macro creada');
                    macrosCache = null;           // invalida la caché del desplegable
                    await loadManage();
                    loadForm(data.macro);         // mantiene la macro abierta tras crear/editar
                } catch { toast('error', 'Error de red'); }
            });

            delBtn.addEventListener('click', async () => {
                if (!fId.value) return;
                if (!window.confirm('¿Eliminar esta macro? No se puede deshacer.')) return;
                try {
                    const res = await fetch('/api/macros/manage/?id=' + encodeURIComponent(fId.value), {
                        method: 'DELETE',
                        headers: { 'X-CSRFToken': getCookie('csrftoken') },
                    });
                    const data = await res.json();
                    if (!res.ok || !data.ok) { toast('error', data.detail || (data.error && data.error.message) || 'Error al eliminar'); return; }
                    toast('success', 'Macro eliminada');
                    macrosCache = null;
                    form.style.display = 'none'; fId.value = '';
                    await loadManage();
                } catch { toast('error', 'Error de red'); }
            });
        }
    }

    // ---- Historial de interacciones: popup en hover ----
    // initTicketPane se ejecuta una vez por cada pestaña de ticket abierta:
    // sin esta guarda, cada pestaña añadía su propio #tl-popup al body y los
    // de pestañas anteriores quedaban huérfanos (nunca se ocultaban) si el
    // usuario cambiaba de pestaña con el ratón todavía sobre un .tl-item, ya
    // que entonces nunca llega a disparar el "mouseleave" que los cierra.
    let tlPopup = document.getElementById('tl-popup');
    if (!tlPopup) {
        tlPopup = document.createElement('div');
        tlPopup.id = 'tl-popup';
        tlPopup.innerHTML = `
            <div class="tlp-header">
                <span class="tlp-badge" id="tlp-badge"></span>
                <span class="tlp-ticket-num" id="tlp-ticket-num"></span>
                <button class="tlp-close" type="button" title="Cerrar">&#x2715;</button>
            </div>
            <div class="tlp-subject" id="tlp-subject"></div>
            <div class="tlp-desc" id="tlp-desc"></div>
            <div class="tlp-sep" id="tlp-sep">Latest comment</div>
            <div class="tlp-comment" id="tlp-comment"></div>
        `;
        document.body.appendChild(tlPopup);
        tlPopup.addEventListener('mouseenter', () => clearTimeout(window.__tfTlHideTimer));
        tlPopup.addEventListener('mouseleave', hideTlPopup);
        tlPopup.querySelector('.tlp-close').addEventListener('click', () => {
            clearTimeout(window.__tfTlHideTimer);
            tlPopup.classList.remove('visible');
        });
    }

    function hideTlPopup() {
        window.__tfTlHideTimer = setTimeout(() => tlPopup.classList.remove('visible'), 150);
    }

    function showTlPopup(item) {
        clearTimeout(window.__tfTlHideTimer);
        const status   = item.dataset.status  || '';
        const id       = item.dataset.id      || '';
        const zid      = item.dataset.zid     || '';
        const subject  = item.dataset.subject || '';
        const desc     = item.dataset.desc    || '';
        const comment  = item.dataset.comment || '';
        const displayId = zid ? `#${zid}` : `#${id}`;

        tlPopup.querySelector('#tlp-badge').textContent = status.toUpperCase();
        tlPopup.querySelector('#tlp-badge').className = `tlp-badge status-${status.toLowerCase()}`;
        tlPopup.querySelector('#tlp-ticket-num').textContent = `Ticket ${displayId}`;
        tlPopup.querySelector('#tlp-subject').textContent = subject;
        tlPopup.querySelector('#tlp-desc').textContent = desc;

        const sepEl     = tlPopup.querySelector('#tlp-sep');
        const commentEl = tlPopup.querySelector('#tlp-comment');
        if (comment) {
            commentEl.textContent = comment;
            sepEl.style.display = '';
            commentEl.style.display = '';
        } else {
            commentEl.textContent = '';
            sepEl.style.display = 'none';
            commentEl.style.display = 'none';
        }

        // Position to the left of the hovered item
        const rect = item.getBoundingClientRect();
        const popW = 320;
        const gap  = 10;
        let left = rect.left - popW - gap;
        if (left < 8) left = rect.right + gap; // fallback: show to the right
        let top = rect.top;
        const popH = tlPopup.offsetHeight || 260;
        if (top + popH > window.innerHeight - 8) top = window.innerHeight - popH - 8;
        if (top < 8) top = 8;

        tlPopup.style.left = left + 'px';
        tlPopup.style.top  = top  + 'px';
        tlPopup.classList.add('visible');
    }

    rootEl.querySelectorAll('.tl-item').forEach(item => {
        item.addEventListener('mouseenter', () => showTlPopup(item));
        item.addEventListener('mouseleave', hideTlPopup);
        // Al hacer clic se navega a ese ticket y este pane puede quedar
        // detached sin que el ratón llegue a salir del item (nunca dispara
        // "mouseleave"): cerrar la vista previa de inmediato evita que se
        // quede flotando sobre la pestaña de destino.
        item.addEventListener('click', () => {
            clearTimeout(window.__tfTlHideTimer);
            tlPopup.classList.remove('visible');
        });
    });

    // ===== Ajuste dinámico de altura del layout =====
    function fitLayout() {
        const layout = rootEl.querySelector('#ticketLayout');
        if (!layout) return;
        // Skip if pane is detached (tab inactive) or not laid out yet —
        // getBoundingClientRect returns zeros and we'd cache a wrong height
        // that pushes the compose box below the fixed footer on re-attach.
        if (!layout.isConnected || layout.offsetParent === null) return;
        const top     = layout.getBoundingClientRect().top;
        const footer  = rootEl.querySelector('.footer-bar');
        const footerH = footer ? footer.offsetHeight : 44;
        const h = Math.max(200, window.innerHeight - top - footerH);
        layout.style.height = h + 'px';
    }
    fitLayout();
    window.addEventListener('resize', fitLayout);
    // La presencia puede añadir varias filas a la cabecera después del montaje.
    // Medir su altura real mantiene el editor por encima del pie fijo.
    const layoutHeader = rootEl.querySelector('.title-bar');
    const layoutFooter = rootEl.querySelector('.footer-bar');
    const layoutObserver = new ResizeObserver(fitLayout);
    if (layoutHeader) layoutObserver.observe(layoutHeader);
    if (layoutFooter) layoutObserver.observe(layoutFooter);
    rootEl.addEventListener('tf:pane-dispose', () => {
        layoutObserver.disconnect();
        window.removeEventListener('resize', fitLayout);
    }, {once: true});
    // Expose so tabs.js can re-fit on tab re-activation (the inline height
    // cached at first mount can drift if the viewport changed while this
    // pane was detached).
    rootEl.__tfFitLayout = fitLayout;

    // ===== Panel collapse + resize =====
    (function initPanels() {
        const layout    = rootEl.querySelector('#ticketLayout');
        if (!layout) return;

        const panelLeft  = rootEl.querySelector('#panelLeft');
        const panelRight = rootEl.querySelector('#panelRight');
        const toggleLeft  = rootEl.querySelector('#toggleLeft');
        const toggleRight = rootEl.querySelector('#toggleRight');
        const iconLeft    = rootEl.querySelector('#iconLeft');
        const iconRight   = rootEl.querySelector('#iconRight');
        const labelLeft   = rootEl.querySelector('#labelLeft');
        const labelRight  = rootEl.querySelector('#labelRight');
        const resizerLeft  = rootEl.querySelector('#resizerLeft');
        const resizerRight = rootEl.querySelector('#resizerRight');

        const LS_KEY = 'tf_panel_state';

        function getState() {
            try { return JSON.parse(localStorage.getItem(LS_KEY)) || {}; } catch { return {}; }
        }
        function saveState(s) {
            try { localStorage.setItem(LS_KEY, JSON.stringify(s)); } catch {}
        }

        function applyCollapse(panel, icon, label, isRight, collapsed) {
            if (!panel) return;
            if (collapsed) {
                panel.classList.add('collapsed');
                if (icon) icon.style.transform = 'rotate(180deg)';
                if (label) label.textContent = 'Mostrar panel';
            } else {
                panel.classList.remove('collapsed');
                if (icon) icon.style.transform = '';
                if (label) label.textContent = 'Ocultar panel';
            }
        }

        // Restore saved state
        const saved = getState();
        if (saved.leftCollapsed)  applyCollapse(panelLeft,  iconLeft,  labelLeft,  false, true);
        if (saved.rightCollapsed) applyCollapse(panelRight, iconRight, labelRight, true,  true);
        if (saved.leftW)  layout.style.setProperty('--panel-left-w',  saved.leftW);
        if (saved.rightW) layout.style.setProperty('--panel-right-w', saved.rightW);

        // Auto-collapse on narrow viewport at load
        if (window.innerWidth <= 960 && panelRight) {
            applyCollapse(panelRight, iconRight, labelRight, true, true);
        }
        if (window.innerWidth <= 720 && panelLeft) {
            applyCollapse(panelLeft, iconLeft, labelLeft, false, true);
        }

        // Toggle buttons
        if (toggleLeft && panelLeft) {
            toggleLeft.addEventListener('click', () => {
                const col = panelLeft.classList.contains('collapsed');
                applyCollapse(panelLeft, iconLeft, labelLeft, false, !col);
                const s = getState(); s.leftCollapsed = !col; saveState(s);
            });
        }
        if (toggleRight && panelRight) {
            toggleRight.addEventListener('click', () => {
                const col = panelRight.classList.contains('collapsed');
                applyCollapse(panelRight, iconRight, labelRight, true, !col);
                const s = getState(); s.rightCollapsed = !col; saveState(s);
            });
        }

        // Drag-to-resize
        function makeResizable(handle, getCurrent, setWidth, minW, maxW, stateKey, invert) {
            if (!handle) return;
            let startX, startW;
            handle.addEventListener('mousedown', e => {
                e.preventDefault();
                startX = e.clientX;
                startW = getCurrent();
                handle.classList.add('dragging');
                document.body.style.cursor = 'col-resize';
                document.body.style.userSelect = 'none';

                function onMove(e) {
                    const delta = (e.clientX - startX) * (invert ? -1 : 1);
                    const newW = Math.min(maxW, Math.max(minW, startW + delta));
                    setWidth(newW + 'px');
                    const s = getState(); s[stateKey] = newW + 'px'; saveState(s);
                }
                function onUp() {
                    handle.classList.remove('dragging');
                    document.body.style.cursor = '';
                    document.body.style.userSelect = '';
                    document.removeEventListener('mousemove', onMove);
                    document.removeEventListener('mouseup', onUp);
                }
                document.addEventListener('mousemove', onMove);
                document.addEventListener('mouseup', onUp);
            });
        }

        makeResizable(
            resizerLeft,
            () => panelLeft ? panelLeft.getBoundingClientRect().width : 320,
            w  => layout.style.setProperty('--panel-left-w', w),
            200, 500, 'leftW'
        );
        makeResizable(
            resizerRight,
            () => panelRight ? panelRight.getBoundingClientRect().width : 360,
            w  => layout.style.setProperty('--panel-right-w', w),
            200, 520, 'rightW',
            true  // invert: dragging left = grow
        );

        // Keyboard shortcut: Alt+1 toggle left, Alt+3 toggle right
        document.addEventListener('keydown', e => {
            if (e.altKey && e.key === '1' && panelLeft && toggleLeft) toggleLeft.click();
            if (e.altKey && e.key === '3' && panelRight && toggleRight) toggleRight.click();
        });

        // ---- Compose area vertical resize ----
        const composeResizer = rootEl.querySelector('#composeResizer');
        const column2 = rootEl.querySelector('.column2');
        if (composeResizer && column2) {
            let startY, startH;
            const measureEl = rootEl.querySelector('#quill-editor') || rootEl.querySelector('#new-message');
            const MIN_H = 80, MAX_H = 500;

            composeResizer.addEventListener('mousedown', e => {
                e.preventDefault();
                startY = e.clientY;
                startH = measureEl ? measureEl.getBoundingClientRect().height : 190;
                composeResizer.classList.add('dragging');
                document.body.style.cursor = 'ns-resize';
                document.body.style.userSelect = 'none';

                function onMove(e) {
                    const delta = startY - e.clientY; // drag up = bigger compose
                    const newH = Math.min(MAX_H, Math.max(MIN_H, startH + delta));
                    column2.style.setProperty('--compose-h', newH + 'px');
                    const s = getState(); s.composeH = newH + 'px'; saveState(s);
                }
                function onUp() {
                    composeResizer.classList.remove('dragging');
                    document.body.style.cursor = '';
                    document.body.style.userSelect = '';
                    document.removeEventListener('mousemove', onMove);
                    document.removeEventListener('mouseup', onUp);
                }
                document.addEventListener('mousemove', onMove);
                document.addEventListener('mouseup', onUp);
            });

            // Restore saved compose height
            if (saved.composeH) column2.style.setProperty('--compose-h', saved.composeH);
        }
    })();

    // ---- Timestamps relativos ----
    function timeAgo(isoString) {
        const diff = Math.floor((Date.now() - new Date(isoString)) / 1000);
        if (diff < 60)        return 'hace un momento';
        if (diff < 3600)      return `hace ${Math.floor(diff / 60)} min`;
        if (diff < 86400)     return `hace ${Math.floor(diff / 3600)} h`;
        if (diff < 2592000)   return `hace ${Math.floor(diff / 86400)} días`;
        return null;
    }

    function updateRelativeTimestamps() {
        rootEl.querySelectorAll('[data-ts]').forEach(el => {
            const rel = timeAgo(el.dataset.ts);
            if (rel) el.textContent = rel;
        });
    }

    updateRelativeTimestamps();
    setInterval(updateRelativeTimestamps, 60000);

    // ---- Botón Take it ----
    const takeItBtn = rootEl.querySelector('#take-it-btn');
    if (takeItBtn) {
        takeItBtn.addEventListener('click', async () => {
            const ticketId = takeItBtn.dataset.ticketId;
            takeItBtn.disabled = true;
            try {
                const resp = await fetch(`/tickets/${ticketId}/take/`, {
                    method: 'POST',
                    headers: { 'X-CSRFToken': getCookie('csrftoken') },
                });
                const data = await resp.json();
                if (data.success) {
                    const hadPendingChanges = hasTicketFieldChanges();
                    const sel = rootEl.querySelector('#asignado');
                    if (sel) {
                        if (!sel.querySelector(`option[value="${data.assignee_id}"]`)) {
                            const opt = new Option(data.assignee_name, data.assignee_id, true, true);
                            sel.append(opt);
                        }
                        $(sel).val(data.assignee_id).trigger('change');
                    }
                    takeItBtn.innerHTML = '<i class="fas fa-check"></i> Asignado a ti';
                    takeItBtn.classList.add('take-it-done');
                    takeItBtn.disabled = true;
                    if (!hadPendingChanges) _formBaseline = serializeTicketForm();
                }
            } catch (e) {
                takeItBtn.disabled = false;
                console.error('take_ticket error', e);
            }
        });
    }

    // ---- Merge de tickets ----
    // Dos pasos: elegir destino y revisar los comentarios que deja la fusion en
    // cada ticket. El del origen es el que le llega al cliente al cerrarse, asi
    // que se puede editar para explicar el motivo sin tener que escribir otra
    // respuesta aparte.
    const mergeBtn = rootEl.querySelector('#merge-btn');
    const mergeModal = rootEl.querySelector('#merge-modal');

    if (mergeBtn && mergeModal) {
        const q$ = (sel) => mergeModal.querySelector(sel);
        const mergeModalClose = q$('#merge-modal-close');
        const mergeCancelBtn = q$('#merge-cancel-btn');
        const mergeBackBtn = q$('#merge-back-btn');
        const mergeNextBtn = q$('#merge-next-btn');
        const mergeConfirmBtn = q$('#merge-confirm-btn');
        const mergeSearchInput = q$('#merge-search-input');
        const mergeSearchResults = q$('#merge-search-results');
        const mergeSelected = q$('#merge-selected');
        const mergeSelectedLabel = q$('#merge-selected-label');
        const mergeTargetId = q$('#merge-target-id');
        const step1 = q$('#merge-step-1');
        const step2 = q$('#merge-step-2');
        const stepLabel = q$('#merge-step-label');
        const stepBar2 = q$('#merge-step-bar-2');
        const review = window.TFMergeReview.for(mergeModal);
        const source = {
            displayId: mergeModal.dataset.sourceId,
            subject: mergeModal.dataset.sourceSubject || '',
        };

        let searchTimer = null;
        let target = null;          // { id, displayId, subject }

        function showStep(n) {
            step1.hidden = n !== 1;
            step2.hidden = n !== 2;
            stepLabel.textContent = 'Paso ' + n + ' de 2';
            stepBar2.classList.toggle('is-done', n === 2);
            mergeCancelBtn.hidden = n !== 1;
            mergeNextBtn.hidden = n !== 1;
            mergeBackBtn.hidden = n !== 2;
            mergeConfirmBtn.hidden = n !== 2;
            mergeConfirmBtn.disabled = false;
        }

        function goToReview() {
            if (!target) return;
            review.show({ sources: [source], target });
            showStep(2);
        }

        function openMergeModal() {
            mergeModal.classList.add('open');
            mergeSearchInput.value = '';
            mergeSearchResults.innerHTML = '';
            mergeSelected.style.display = 'none';
            mergeTargetId.value = '';
            mergeNextBtn.disabled = true;
            target = null;
            review.reset();
            showStep(1);
            setTimeout(() => mergeSearchInput.focus(), 30);
        }

        function closeMergeModal() {
            mergeModal.classList.remove('open');
        }

        function selectTarget(t) {
            target = { id: t.id, displayId: t.display_id || t.id, subject: t.subject || '' };
            mergeTargetId.value = t.id;
            mergeSelectedLabel.textContent = '#' + target.displayId + ' — ' + target.subject;
            mergeSelected.style.display = 'block';
            mergeNextBtn.disabled = false;
            mergeSearchResults.innerHTML = '';
        }

        mergeBtn.addEventListener('click', openMergeModal);
        mergeModalClose.addEventListener('click', closeMergeModal);
        mergeCancelBtn.addEventListener('click', closeMergeModal);
        mergeBackBtn.addEventListener('click', () => showStep(1));
        mergeNextBtn.addEventListener('click', goToReview);
        mergeModal.addEventListener('click', (e) => { if (e.target === mergeModal) closeMergeModal(); });

        mergeSearchInput.addEventListener('input', () => {
            clearTimeout(searchTimer);
            const q = mergeSearchInput.value.trim();
            if (q.length < 2) { mergeSearchResults.innerHTML = ''; return; }
            searchTimer = setTimeout(async () => {
                const searchUrl = (ctx && ctx.searchUrl) || (window.urls && window.urls.search) || '/search/';
                try {
                    const res = await fetch(`${searchUrl}?q=${encodeURIComponent(q)}`);
                    const data = await res.json();
                    mergeSearchResults.innerHTML = '';
                    const tickets = (data.tickets || []).filter(t => t.id !== ctx.ticketId);
                    if (!tickets.length) {
                        mergeSearchResults.innerHTML = '<li class="merge-result-empty">Sin resultados</li>';
                        return;
                    }
                    tickets.forEach(t => {
                        // El asunto viene de correo: se inserta como texto.
                        const li = document.createElement('li');
                        li.className = 'merge-result-item';
                        li.dataset.id = t.id;
                        const id = document.createElement('span');
                        id.className = 'merge-result-id';
                        id.textContent = '#' + (t.display_id || t.id);
                        const subject = document.createElement('span');
                        subject.className = 'merge-result-subject';
                        subject.textContent = t.subject || '';
                        const status = document.createElement('span');
                        status.className = 'merge-result-status status-' + t.status;
                        status.textContent = t.status;
                        li.append(id, subject, status);
                        li.addEventListener('click', () => selectTarget(t));
                        mergeSearchResults.appendChild(li);
                    });
                } catch (e) {
                    console.error('[merge] search failed', e);
                    mergeSearchResults.innerHTML = '<li class="merge-result-empty" style="color:var(--color-danger);">Error al buscar</li>';
                }
            }, 300);
        });

        mergeConfirmBtn.addEventListener('click', async () => {
            if (!target) return;
            mergeConfirmBtn.disabled = true;
            try {
                const fd = new FormData();
                fd.append('target_ticket_id', target.id);
                const messages = review.payload();
                Object.keys(messages).forEach((key) => {
                    fd.append(key + '_comment', messages[key].content);
                    fd.append(key + '_comment_html', messages[key].html_body);
                    fd.append(key + '_is_public', messages[key].is_public ? 'true' : 'false');
                });
                // URL de ESTE pane, nunca window.urls.merge_ticket: esa global
                // la pisa cada pestaña de ticket al cargarse, así que llegaba
                // vacía (POST a «undefined») o con el ticket de otra pestaña.
                const mergeUrl = (ctx && ctx.mergeTicketUrl) || ('/tickets/' + ctx.ticketId + '/merge/');
                const res = await fetch(mergeUrl, {
                    method: 'POST',
                    headers: { 'X-CSRFToken': getCookie('csrftoken') },
                    body: fd,
                });
                const data = await res.json();
                if (!res.ok) {
                    const err = data.error;
                    window.toast.error((err && err.message) || err || 'Error al fusionar');
                    mergeConfirmBtn.disabled = false;
                    return;
                }
                closeMergeModal();
                window.location.href = `/tickets/create/?id=${data.target_id}`;
            } catch (e) {
                mergeConfirmBtn.disabled = false;
                console.error('merge error', e);
            }
        });
    }

    // ---- Refresco automatico (el poller vive en ticket_live.js) ------------
    // El intervalo NO se crea aqui a proposito: tabs.js conserva los panes
    // inactivos detached-pero-vivos en su cache, asi que un intervalo por pane
    // seguiria sondeando tickets que ya nadie mira. Este pane solo publica lo
    // que el poller necesita; el decide a cual sondear.
    let _lastCommentId = 0;
    rootEl.querySelectorAll('[data-comment-id]').forEach(el => {
        const id = parseInt(el.getAttribute('data-comment-id'), 10);
        if (Number.isFinite(id) && id > _lastCommentId) _lastCommentId = id;
    });
    rootEl.__tfLive = {
        ticketId: ctx.ticketId,
        lastCommentId: _lastCommentId,
        serverStatus: (rootEl.querySelector('#selected-status')?.dataset.serverStatus
            || rootEl.querySelector('#selected-status')?.textContent || '').trim().toLowerCase(),
        appendComment: appendPublishedComment,
        hasPendingFieldChanges: hasTicketFieldChanges,
        syncAssignee(ticket) {
            if (_formBaseline === null || hasTicketFieldChanges()
                || !Object.prototype.hasOwnProperty.call(ticket, 'assignee_id')) return;
            const select = rootEl.querySelector('#asignado');
            const value = ticket.assignee_id == null ? '' : String(ticket.assignee_id);
            if (!select || select.value === value) return;
            if (!Array.from(select.options).some(option => option.value === value)) {
                select.append(new Option(ticket.assignee_name || 'Sin asignar', value));
            }
            $(select).val(value).trigger('change');
            _formBaseline = serializeTicketForm();
        },
    };

};

// Fallback bootstrap for the deep-link page load.
// Primary bootstrap now lives in tabs.js initOnce (single source of truth so
// the server-rendered and fragment-fetched paths share the same code). This
// handler stays as a safety net in case tabs.js fails to load or fails to
// bootstrap. The tfInitialized guard prevents double init.
document.addEventListener('DOMContentLoaded', () => {
    const pane = document.querySelector('#workspace > .ticket-pane');
    if (!pane) return;
    if (pane.dataset.tfInitialized === '1') return;
    console.warn('[create_ticket] tabs.js did not bootstrap pane — running fallback');
    pane.dataset.tfInitialized = '1';
    const tid = pane.getAttribute('data-ticket-id');
    const parsedTid = tid && tid !== 'null' && tid !== '' ? (parseInt(tid, 10) || tid) : null;
    window.initTicketPane(window.jQuery ? window.jQuery(pane) : pane, {
        ticketId: parsedTid,
        currentUserId: window.currentUserId,
        addCommentUrl: parsedTid ? ('/tickets/' + parsedTid + '/add_comment/') : null,
        mergeTicketUrl: parsedTid ? ('/tickets/' + parsedTid + '/merge/') : null,
        searchUrl: '/search/',
    });
});
