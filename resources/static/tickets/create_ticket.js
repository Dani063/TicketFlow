// app/static/tickets/create_ticket.js
//
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

// Shared Select2 config. Used by initTicketPane (first mount) and by
// tfApplyPaneSelect2 (defensive re-init after a pane was detached and
// re-attached by tabs.js — Select2 widget state doesn't survive that round-trip
// reliably, so we destroy + re-init).
const _tfUserAjaxConfig = {
    url: '/api/users/search/',
    dataType: 'json',
    delay: 200,
    data: params => ({ q: params.term || '', page: params.page || 1 }),
    processResults: data => data,
    cache: true,
};

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
            if (isAjaxUser) {
                window.jQuery(this).select2({
                    width: '100%',
                    placeholder: '-',
                    allowClear: true,
                    minimumInputLength: 0,
                    ajax: _tfUserAjaxConfig,
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
                window.jQuery(this).select2({
                    width: '100%',
                    placeholder: window.jQuery(this).attr('placeholder') || 'Selecciona una opción',
                    allowClear: false,
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
    if (window.QuillComposer && typeof window.QuillComposer.initFor === 'function') {
        window.QuillComposer.initFor(rootEl);
    }
    function middleEllipsis(filename, max = 26, filler = '…') {
        if (!filename) return '';
        // separa extensión
        const lastDot = filename.lastIndexOf('.');
        const ext = lastDot > 0 ? filename.slice(lastDot) : '';
        const base = lastDot > 0 ? filename.slice(0, lastDot) : filename;

        if (base.length + ext.length <= max) return filename;

        const keep = Math.max(1, max - ext.length - 1); // 1 para el filler
        const start = Math.ceil(keep / 2);
        const end = Math.floor(keep / 2);
        return base.slice(0, start) + filler + base.slice(base.length - end) + ext;
    }

    // ---- Adjuntos: subir y preparar para el comentario ----
    window.pendingAttachmentIds = [];

    const attachBtn = rootEl.querySelector('#attach-btn');
    const attachInput = rootEl.querySelector('#attach-input');
    const pendingBox = rootEl.querySelector('#pending-attachments');
    const composerArea = rootEl.querySelector('#messageInputArea');
    const composerTextarea = rootEl.querySelector('#new-message');

    function isImageType(t) { return t && t.startsWith('image/'); }

    async function uploadFiles(files) {
        if (!files || !files.length) return;
        if (!ctx.ticketId) {
            window.toast.warning('Guarda el ticket antes de adjuntar archivos.');
            return;
        }
        for (const file of files) {
            const fd = new FormData();
            fd.append('file', file);
            try {
                const res = await fetch(`/tickets/${ctx.ticketId}/attachments/upload/`, {
                    method: 'POST',
                    headers: { 'X-CSRFToken': getCookie('csrftoken') }, // NO pongas Content-Type aquí
                    body: fd
                });
                const data = await res.json();
                if (!res.ok) { window.toast.error(data.error || 'Error al subir adjunto'); continue; }

                window.pendingAttachmentIds.push(data.id);
                const pill = document.createElement('div');
                pill.className = 'pending-pill';
                pill.dataset.id = String(data.id);

                const originalName = data.filename || (data.file_url || '').split('/').pop() || 'archivo';
                const displayName = middleEllipsis(originalName, 26, '…');

                pill.innerHTML = `
  <i class="fas fa-paperclip" aria-hidden="true"></i>
  <span class="name" title="${originalName}">${displayName}</span>
  <button type="button" class="remove" aria-label="Quitar adjunto">&times;</button>
`;

                pill.querySelector('.remove').addEventListener('click', () => {
                    window.pendingAttachmentIds = window.pendingAttachmentIds.filter(id => id !== data.id);
                    pill.remove();
                });

                pendingBox && pendingBox.appendChild(pill);
            } catch (err) {
                console.error('Upload error', err);
                window.toast.error('No se pudo subir el adjunto.');
            }
        }
    }

    if (attachBtn && attachInput) {
        attachBtn.addEventListener('click', () => {
            if (!ctx.ticketId) {
                window.toast.warning('Guarda el ticket antes de adjuntar archivos.');
                return;
            }
            attachInput.click();
        });

        attachInput.addEventListener('change', async (e) => {
            await uploadFiles(Array.from(e.target.files || []));
            attachInput.value = '';
        });
    }

    // Drag & drop sobre el composer
    if (composerArea) {
        let _dragDepth = 0;
        const hasFiles = (e) => e.dataTransfer && Array.from(e.dataTransfer.types || []).includes('Files');

        composerArea.addEventListener('dragenter', (e) => {
            if (!hasFiles(e)) return;
            e.preventDefault();
            _dragDepth++;
            composerArea.classList.add('dragover');
        });
        composerArea.addEventListener('dragover', (e) => {
            if (!hasFiles(e)) return;
            e.preventDefault();
            e.dataTransfer.dropEffect = 'copy';
        });
        composerArea.addEventListener('dragleave', () => {
            _dragDepth = Math.max(0, _dragDepth - 1);
            if (_dragDepth === 0) composerArea.classList.remove('dragover');
        });
        composerArea.addEventListener('drop', async (e) => {
            if (!hasFiles(e)) return;
            e.preventDefault();
            _dragDepth = 0;
            composerArea.classList.remove('dragover');
            await uploadFiles(Array.from(e.dataTransfer.files || []));
        });
    }

    // Pegar imagen desde el portapapeles
    if (composerTextarea) {
        composerTextarea.addEventListener('paste', async (e) => {
            const items = (e.clipboardData && e.clipboardData.items) || [];
            const files = [];
            for (const it of items) {
                if (it.kind === 'file') {
                    const f = it.getAsFile();
                    if (f) files.push(f);
                }
            }
            if (files.length) {
                e.preventDefault();
                await uploadFiles(files);
            }
        });
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

    const sendMessage = async (ev) => {
        if (ev) { ev.preventDefault(); ev.stopPropagation(); }

        const textarea = rootEl.querySelector('#new-message');
        const content = (textarea?.value || '').trim();

        // Si aún no existe id real, validar como en "Publicar"
        if (!ctx.ticketId) {
            const errors = [];
            const brand = (rootEl.querySelector('#empresa')?.value || '').trim();
            const subject = (rootEl.querySelector('#subject')?.value || '').trim();
            if (!brand) errors.push('Please provide a ticket brand');
            if (!content) errors.push('Please provide a ticket description');
            if (!subject) errors.push('Please provide a ticket subject');
            if (errors.length) { errors.forEach(e => window.toast.warning(e)); return; }

            // Inyecta status y crea/redirige
            const ticketForm = rootEl.querySelector('#ticket-form');
            const selectedStatus = rootEl.querySelector('#selected-status')?.textContent?.trim() || '';
            let statusInput = ticketForm.querySelector('input[name="status"]');
            if (!statusInput) {
                statusInput = document.createElement('input');
                statusInput.type = 'hidden';
                statusInput.name = 'status';
                ticketForm.appendChild(statusInput);
            }
            statusInput.value = selectedStatus;
            if (window.Tabs && typeof window.Tabs.closeDraftNewTicketTabs === 'function') {
                window.Tabs.closeDraftNewTicketTabs();
            }
            ticketForm.requestSubmit();
            return;
        }

        // Ticket existente: validar contenido
        if (!content) { window.toast.warning('El contenido no puede estar vacío.'); return; }

        const isPublic = (typeof isPublicInput !== 'undefined') ? (isPublicInput.value === 'true') : true;
        const payload = { content, is_public: isPublic };
        const _html = window.QuillComposer?.getHtml?.();
        if (_html) payload.html_body = _html;
        if (window.pendingAttachmentIds?.length) payload.attachment_ids = window.pendingAttachmentIds;

        try {
            const response = await fetch(`/tickets/${ctx.ticketId}/add_comment/`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-CSRFToken': getCookie('csrftoken')
                },
                body: JSON.stringify(payload)
            });

            const data = await response.json();

            if (!response.ok) {
                window.toast.error(data.error || 'No se pudo enviar el mensaje.');
                return;
            }

            const messagesBox = rootEl.querySelector('#messagesBox');
            const newComment = document.createElement('div');
            const isMe = Number(data.user_id) === Number(window.currentUserId);
            newComment.className = 'message' + (isMe ? ' me' : '') + (data.is_public ? '' : ' internal');
            newComment.dataset.commentId = data.id;

            // Calcular fecha relativa ("hace un momento", "hace X minutos", etc.)
            const getRelativeTime = (isoDate) => {
                const date = new Date(isoDate);
                const now = new Date();
                const diffMs = now - date;
                const diffMins = Math.floor(diffMs / 60000);
                const diffHours = Math.floor(diffMs / 3600000);
                const diffDays = Math.floor(diffMs / 86400000);

                if (diffMins < 1) return 'hace un momento';
                if (diffMins < 60) return `hace ${diffMins} minuto${diffMins > 1 ? 's' : ''}`;
                if (diffHours < 24) return `hace ${diffHours} hora${diffHours > 1 ? 's' : ''}`;
                if (diffDays < 7) return `hace ${diffDays} día${diffDays > 1 ? 's' : ''}`;
                return date.toLocaleDateString('es-ES');
            };

            const internalBadge = data.is_public ? '' : '<span class="badge-internal">Interno</span>';
            const initials = (data.username || '?').charAt(0).toUpperCase();
            const roleHtml = data.user_role ? `<span class="msg-role">${data.user_role}</span>` : '';
            const profileUrl = (window.urls && window.urls.customer_profile) ? `${window.urls.customer_profile}?id=${data.user_id}` : '#';
            const userLink = `<a class="msg-author" href="${profileUrl}">${data.username || 'Usuario'}</a>`;
            const relativeTime = getRelativeTime(data.created_at_iso);
            const _bodyHtml = data.html_body
                ? `<div class="email-html">${data.html_body}</div>`
                : `${(data.content || '').replace(/\n/g, '<br>')}`;

            // Calcular color del avatar usando la misma lógica que existe en la página
            const avatarColor = (name) => {
                const palette = ['#5b67ca','#e06c75','#56b6c2','#98c379','#d19a66','#c678dd','#61afef','#e5c07b'];
                let hash = 0;
                for (let i = 0; i < name.length; i++) hash = name.charCodeAt(i) + ((hash << 5) - hash);
                return palette[Math.abs(hash) % palette.length];
            };
            const bgColor = avatarColor(data.username || '?');

            newComment.innerHTML = `
      <div class="msg-header">
        <div class="msg-avatar" data-initials="${initials}" style="background: ${bgColor};">${initials}</div>
        <div class="msg-meta">
          ${userLink}
          ${roleHtml}
        </div>
        <div class="msg-right">
          ${internalBadge}
          <span class="msg-timestamp" title="${data.created_at}">${relativeTime}</span>
        </div>
      </div>
      <div class="msg-body">${_bodyHtml}</div>
    `;

            // PINTAR ADJUNTOS DEVUELTOS (si hay)
            if (data.attachments && data.attachments.length) {
                const at = document.createElement('div');
                at.className = 'attachments';
                data.attachments.forEach(a => {
                    if (a.file_type && a.file_type.startsWith('image/')) {
                        const link = document.createElement('a');
                        link.href = a.file_url; link.target = '_blank';
                        const img = document.createElement('img');
                        img.src = a.file_url; link.appendChild(img);
                        at.appendChild(link);
                    } else {
                        const link = document.createElement('a');
                        link.href = a.file_url; link.target = '_blank';
                        link.className = 'file-pill';
                        link.innerHTML = '<i class="fas fa-paperclip"></i> Archivo';
                        at.appendChild(link);
                    }
                });
                newComment.appendChild(at);
            }

            messagesBox.appendChild(newComment);

            // limpiar composer
            textarea.value = '';
            if (typeof pendingBox !== 'undefined' && pendingBox) pendingBox.innerHTML = '';
            window.pendingAttachmentIds = [];
            messagesBox.scrollTop = messagesBox.scrollHeight;

        } catch (err) {
            console.error('Error al enviar el comentario:', err);
            window.toast.error('Error de red al enviar el mensaje.');
        }
    };

    async function publishComment(ev) {
        if (ev) { ev.preventDefault(); ev.stopPropagation(); }

        if (!ctx.ticketId) {
            // Reutiliza tu flujo de creación
            const ticketForm = rootEl.querySelector('#ticket-form');
            const selectedStatus = rootEl.querySelector('#selected-status')?.textContent?.trim() || 'open';
            let statusInput = ticketForm.querySelector('input[name="status"]');
            if (!statusInput) {
                statusInput = document.createElement('input');
                statusInput.type = 'hidden';
                statusInput.name = 'status';
                ticketForm.appendChild(statusInput);
            }
            statusInput.value = selectedStatus;
            ticketForm.requestSubmit();
            return;
        }

        const textarea = rootEl.querySelector('#new-message');
        const content = (textarea?.value || '').trim();
        if (!content) { window.toast.warning('El contenido no puede estar vacío.'); return; }

        const isPublic = (typeof isPublicInput !== 'undefined') ? (isPublicInput.value === 'true') : true;
        const newStatus = (rootEl.querySelector('#selected-status')?.textContent || 'open').trim().toLowerCase();

        const payload = { content, is_public: isPublic, new_status: newStatus };
        const _html2 = window.QuillComposer?.getHtml?.();
        if (_html2) payload.html_body = _html2;
        if (window.pendingAttachmentIds?.length) payload.attachment_ids = window.pendingAttachmentIds;

        try {
            const response = await fetch(`/tickets/${ctx.ticketId}/add_comment/`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCookie('csrftoken') },
                body: JSON.stringify(payload)
            });
            const data = await response.json();
            if (!response.ok) { window.toast.error(data.error || 'No se pudo publicar.'); return; }

            // Pinta el nuevo comentario (idéntico a sendMessage)
            const messagesBox = rootEl.querySelector('#messagesBox');
            const newComment = document.createElement('div');
            const isMe = Number(data.user_id) === Number(window.currentUserId);
            newComment.className = 'message' + (isMe ? ' me' : '') + (data.is_public ? '' : ' internal');
            newComment.dataset.commentId = data.id;

            // Reutilizar la función getRelativeTime (definida en sendMessage)
            const getRelativeTime2 = (isoDate) => {
                const date = new Date(isoDate);
                const now = new Date();
                const diffMs = now - date;
                const diffMins = Math.floor(diffMs / 60000);
                const diffHours = Math.floor(diffMs / 3600000);
                const diffDays = Math.floor(diffMs / 86400000);

                if (diffMins < 1) return 'hace un momento';
                if (diffMins < 60) return `hace ${diffMins} minuto${diffMins > 1 ? 's' : ''}`;
                if (diffHours < 24) return `hace ${diffHours} hora${diffHours > 1 ? 's' : ''}`;
                if (diffDays < 7) return `hace ${diffDays} día${diffDays > 1 ? 's' : ''}`;
                return date.toLocaleDateString('es-ES');
            };

            // Construir estructura correcta del mensaje
            const internalBadge = data.is_public ? '' : '<span class="badge-internal">Interno</span>';
            const initials = (data.username || '?').charAt(0).toUpperCase();
            const roleHtml = data.user_role ? `<span class="msg-role">${data.user_role}</span>` : '';
            const profileUrl = (window.urls && window.urls.customer_profile) ? `${window.urls.customer_profile}?id=${data.user_id}` : '#';
            const userLink = `<a class="msg-author" href="${profileUrl}">${data.username || 'Usuario'}</a>`;
            const relativeTime = getRelativeTime2(data.created_at_iso);
            const _bodyHtml2 = data.html_body
                ? `<div class="email-html">${data.html_body}</div>`
                : `${(data.content || '').replace(/\n/g, '<br>')}`;

            // Calcular color del avatar
            const avatarColor2 = (name) => {
                const palette = ['#5b67ca','#e06c75','#56b6c2','#98c379','#d19a66','#c678dd','#61afef','#e5c07b'];
                let hash = 0;
                for (let i = 0; i < name.length; i++) hash = name.charCodeAt(i) + ((hash << 5) - hash);
                return palette[Math.abs(hash) % palette.length];
            };
            const bgColor2 = avatarColor2(data.username || '?');

            newComment.innerHTML = `
      <div class="msg-header">
        <div class="msg-avatar" data-initials="${initials}" style="background: ${bgColor2};">${initials}</div>
        <div class="msg-meta">
          ${userLink}
          ${roleHtml}
        </div>
        <div class="msg-right">
          ${internalBadge}
          <span class="msg-timestamp" title="${data.created_at}">${relativeTime}</span>
        </div>
      </div>
      <div class="msg-body">${_bodyHtml2}</div>
    `;
            if (data.attachments && data.attachments.length) {
                const at = document.createElement('div');
                at.className = 'attachments';
                data.attachments.forEach(a => {
                    if (a.file_type && a.file_type.startsWith('image/')) {
                        const link = document.createElement('a');
                        link.href = a.file_url; link.target = '_blank';
                        const img = document.createElement('img');
                        img.src = a.file_url; link.appendChild(img);
                        at.appendChild(link);
                    } else {
                        const link = document.createElement('a');
                        link.href = a.file_url; link.target = '_blank';
                        link.className = 'file-pill';
                        link.innerHTML = '<i class="fas fa-paperclip"></i> Archivo';
                        at.appendChild(link);
                    }
                });
                newComment.appendChild(at);
            }
            messagesBox.appendChild(newComment);
            messagesBox.scrollTop = messagesBox.scrollHeight;

            // Actualiza el estado en la UI (span del footer y, si tienes, badge/campo de estado)
            const statusSpan = rootEl.querySelector('#selected-status');
            if (statusSpan && data.new_status) statusSpan.textContent = data.new_status;

            // Limpia composer
            textarea.value = '';
            if (typeof pendingBox !== 'undefined' && pendingBox) pendingBox.innerHTML = '';
            window.pendingAttachmentIds = [];

        } catch (err) {
            console.error('Error al publicar:', err);
            window.toast.error('Error de red al publicar.');
        }
    }


    const sendMessageButton = rootEl.querySelector('#send-message-btn');
    if (sendMessageButton) {
        sendMessageButton.addEventListener('click', sendMessage);
    }

    const ticketForm = rootEl.querySelector('#ticket-form');
    if (ticketForm) {
        ticketForm.addEventListener('submit', (e) => {
            // Solo bloqueamos el submit automático en tickets NUEVOS si faltan datos
            if (!ctx.ticketId) {
                const brand = (rootEl.querySelector('#empresa')?.value || '').trim();
                const subject = (rootEl.querySelector('#subject')?.value || '').trim();
                const description = (rootEl.querySelector('#new-message')?.value || '').trim();

                const errors = [];
                if (!brand) errors.push('Please provide a ticket brand');
                if (!description) errors.push('Please provide a ticket description');
                if (!subject) errors.push('Please provide a ticket subject');

                if (errors.length) {
                    e.preventDefault();
                    e.stopPropagation();
                    errors.forEach(err => window.toast.warning(err));
                }
            }
        });
    }

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
                dropdownContent.classList.remove('show');
                console.log('Publicar ticket con estado:', status);
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

        publishButton.addEventListener('click', (event) => {
            event.preventDefault();
            // Si hay ticketId → publicar vía AJAX con cambio de estado
            if (ctx.ticketId) {
                publishComment(event);
                return;
            }

            // Ticket nuevo → validación + submit (tu flujo actual)
            const errors = [];
            const brand = (rootEl.querySelector('#empresa')?.value || '').trim();
            const subject = (rootEl.querySelector('#subject')?.value || '').trim();
            const description = (rootEl.querySelector('#new-message')?.value || '').trim();
            if (!brand) errors.push('Please provide a ticket brand');
            if (!description) errors.push('Please provide a ticket description');
            if (!subject) errors.push('Please provide a ticket subject');
            if (errors.length) { errors.forEach(err => window.toast.warning(err)); return; }

            const selectedStatus = rootEl.querySelector('#selected-status').textContent.trim();
            let statusInput = ticketForm.querySelector('input[name="status"]');
            if (!statusInput) {
                statusInput = document.createElement('input');
                statusInput.type = 'hidden';
                statusInput.name = 'status';
                ticketForm.appendChild(statusInput);
            }
            statusInput.value = selectedStatus;
            ticketForm.submit();
        });
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
            })
            .catch(error => console.error('Error al cargar el ticket:', error));
    }

    // ---- Scroll al último comentario ----
    const messagesBox = rootEl.querySelector('#messagesBox');
    if (messagesBox) messagesBox.scrollTop = messagesBox.scrollHeight;

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

    // ---- Blockquotes colapsables ----
    rootEl.querySelectorAll('.msg-body').forEach(body => {
        const quotes = body.querySelectorAll('blockquote');
        if (!quotes.length) return;
        quotes.forEach(q => {
            const btn = document.createElement('button');
            btn.type = 'button';
            btn.className = 'quote-toggle';
            btn.innerHTML = '<i class="fas fa-ellipsis-h"></i> Ver mensaje anterior';
            btn.addEventListener('click', () => {
                const expanded = q.classList.toggle('quote-expanded');
                btn.innerHTML = expanded
                    ? '<i class="fas fa-minus"></i> Ocultar'
                    : '<i class="fas fa-ellipsis-h"></i> Ver mensaje anterior';
            });
            q.before(btn);
        });
    });

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
                const ta = rootEl.querySelector('#new-message');
                if (ta) { ta.value = a.comment; ta.focus(); }
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
    const tlPopup = document.createElement('div');
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

    let tlHideTimer = null;

    function showTlPopup(item) {
        clearTimeout(tlHideTimer);
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

    function hideTlPopup() {
        tlHideTimer = setTimeout(() => tlPopup.classList.remove('visible'), 150);
    }

    rootEl.querySelectorAll('.tl-item').forEach(item => {
        item.addEventListener('mouseenter', () => showTlPopup(item));
        item.addEventListener('mouseleave', hideTlPopup);
    });
    tlPopup.addEventListener('mouseenter', () => clearTimeout(tlHideTimer));
    tlPopup.addEventListener('mouseleave', hideTlPopup);
    tlPopup.querySelector('.tlp-close').addEventListener('click', () => {
        clearTimeout(tlHideTimer);
        tlPopup.classList.remove('visible');
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
                }
            } catch (e) {
                takeItBtn.disabled = false;
                console.error('take_ticket error', e);
            }
        });
    }

    // ---- Merge de tickets ----
    const mergeBtn = rootEl.querySelector('#merge-btn');
    const mergeModal = rootEl.querySelector('#merge-modal');
    const mergeModalClose = rootEl.querySelector('#merge-modal-close');
    const mergeCancelBtn = rootEl.querySelector('#merge-cancel-btn');
    const mergeConfirmBtn = rootEl.querySelector('#merge-confirm-btn');
    const mergeSearchInput = rootEl.querySelector('#merge-search-input');
    const mergeSearchResults = rootEl.querySelector('#merge-search-results');
    const mergeSelected = rootEl.querySelector('#merge-selected');
    const mergeSelectedLabel = rootEl.querySelector('#merge-selected-label');
    const mergeTargetId = rootEl.querySelector('#merge-target-id');

    if (mergeBtn && mergeModal) {
        let searchTimer = null;

        function openMergeModal() {
            mergeModal.classList.add('open');
            mergeSearchInput.value = '';
            mergeSearchResults.innerHTML = '';
            mergeSelected.style.display = 'none';
            mergeTargetId.value = '';
            mergeConfirmBtn.disabled = true;
            setTimeout(() => mergeSearchInput.focus(), 30);
        }

        function closeMergeModal() {
            mergeModal.classList.remove('open');
        }

        mergeBtn.addEventListener('click', openMergeModal);
        mergeModalClose.addEventListener('click', closeMergeModal);
        mergeCancelBtn.addEventListener('click', closeMergeModal);
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
                        const li = document.createElement('li');
                        li.className = 'merge-result-item';
                        li.dataset.id = t.id;
                        li.innerHTML = `<span class="merge-result-id">#${t.id}</span> <span class="merge-result-subject">${t.subject}</span> <span class="merge-result-status status-${t.status}">${t.status}</span>`;
                        li.addEventListener('click', () => {
                            mergeTargetId.value = t.id;
                            mergeSelectedLabel.textContent = `#${t.id} — ${t.subject}`;
                            mergeSelected.style.display = 'block';
                            mergeConfirmBtn.disabled = false;
                            mergeSearchResults.innerHTML = '';
                        });
                        mergeSearchResults.appendChild(li);
                    });
                } catch (e) {
                    console.error('[merge] search failed', e);
                    mergeSearchResults.innerHTML = '<li class="merge-result-empty" style="color:var(--color-danger);">Error al buscar</li>';
                }
            }, 300);
        });

        mergeConfirmBtn.addEventListener('click', async () => {
            const targetId = mergeTargetId.value;
            if (!targetId) return;
            const ok = await window.dialog.confirm({
                title: 'Fusionar ticket',
                message: '¿Seguro que quieres fusionar este ticket? La acción no se puede deshacer.',
                confirmText: 'Fusionar',
                cancelText: 'Cancelar',
                variant: 'danger',
            });
            if (!ok) return;
            mergeConfirmBtn.disabled = true;
            try {
                const fd = new FormData();
                fd.append('target_ticket_id', targetId);
                const res = await fetch(window.urls.merge_ticket, {
                    method: 'POST',
                    headers: { 'X-CSRFToken': getCookie('csrftoken') },
                    body: fd,
                });
                const data = await res.json();
                if (!res.ok) { window.toast.error(data.error || 'Error al fusionar'); mergeConfirmBtn.disabled = false; return; }
                closeMergeModal();
                window.location.href = `/tickets/create/?id=${data.target_id}`;
            } catch (e) {
                mergeConfirmBtn.disabled = false;
                console.error('merge error', e);
            }
        });
    }


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
