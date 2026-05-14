// app/static/tickets/create_ticket.js

document.addEventListener('DOMContentLoaded', () => {
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
    // Inicialización de Select2 para todos los selects
    const initializeSelect2 = () => {
        $('select.select2').select2({
            width: '100%',
            placeholder: function () {
                return $(this).attr('placeholder') || "Selecciona una opción";
            },
            allowClear: false
        });
    };

    // Inicializar Select2
    initializeSelect2();
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

    const attachBtn = document.getElementById('attach-btn');
    const attachInput = document.getElementById('attach-input');
    const pendingBox = document.getElementById('pending-attachments');

    function isImageType(t) { return t && t.startsWith('image/'); }

    if (attachBtn && attachInput) {
        attachBtn.addEventListener('click', () => {
            if (!window.ticketId) {
                alert('Guarda el ticket antes de adjuntar archivos.');
                return;
            }
            attachInput.click();
        });

        attachInput.addEventListener('change', async (e) => {
            const files = Array.from(e.target.files || []);
            for (const file of files) {
                const fd = new FormData();
                fd.append('file', file);
                try {
                    const res = await fetch(`/tickets/${window.ticketId}/attachments/upload/`, {
                        method: 'POST',
                        headers: { 'X-CSRFToken': getCookie('csrftoken') }, // NO pongas Content-Type aquí
                        body: fd
                    });
                    const data = await res.json();
                    if (!res.ok) { alert(data.error || 'Error al subir adjunto'); continue; }

                    // guardamos id para el comentario
                    window.pendingAttachmentIds.push(data.id);
                    // pinta píldora con nombre (elipsis medio) y botón de quitar
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

                    const removeBtn = pill.querySelector('.remove');
                    removeBtn.addEventListener('click', () => {
                        window.pendingAttachmentIds = window.pendingAttachmentIds.filter(id => id !== data.id);
                        pill.remove();
                    });

                    pendingBox && pendingBox.appendChild(pill);

                } catch (err) {
                    console.error('Upload error', err);
                    alert('No se pudo subir el adjunto.');
                }
            }
            attachInput.value = '';
        });
    }


    // ---- Desplegable Público/Interno ----
    const isPublicInput = document.getElementById('is_public_input');
    const visBtn = document.getElementById('visBtn');
    const visMenu = document.getElementById('visMenu');

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

        const textarea = document.getElementById('new-message');
        const content = (textarea?.value || '').trim();

        // Si aún no existe id real, validar como en "Publicar"
        if (!window.ticketId) {
            const errors = [];
            const brand = (document.getElementById('empresa')?.value || '').trim();
            const subject = (document.getElementById('subject')?.value || '').trim();
            if (!brand) errors.push('Please provide a ticket brand');
            if (!content) errors.push('Please provide a ticket description');
            if (!subject) errors.push('Please provide a ticket subject');
            if (errors.length) { alert(errors.join('\n')); return; }

            // Inyecta status y crea/redirige
            const ticketForm = document.getElementById('ticket-form');
            const selectedStatus = document.getElementById('selected-status')?.textContent?.trim() || '';
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
        if (!content) { alert('El contenido no puede estar vacío.'); return; }

        const isPublic = (typeof isPublicInput !== 'undefined') ? (isPublicInput.value === 'true') : true;
        const payload = { content, is_public: isPublic };
        if (window.pendingAttachmentIds?.length) payload.attachment_ids = window.pendingAttachmentIds;

        try {
            const response = await fetch(`/tickets/${window.ticketId}/add_comment/`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-CSRFToken': getCookie('csrftoken')
                },
                body: JSON.stringify(payload)
            });

            const data = await response.json();

            if (!response.ok) {
                alert(data.error || 'No se pudo enviar el mensaje.');
                return;
            }

            const messagesBox = document.getElementById('messagesBox');
            const newComment = document.createElement('div');
            const isMe = Number(data.user_id) === Number(window.currentUserId);
            newComment.className = 'message' + (isMe ? ' me' : '') + (data.is_public ? '' : ' internal');

            const internalBadge = data.is_public ? '' : '<span class="badge-internal">Interno</span>';

            newComment.innerHTML = `
      ${internalBadge}
      <p><strong>${data.username}:</strong> ${data.content}</p>
      <span class="timestamp">${data.created_at}</span>
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
            alert('Error de red al enviar el mensaje.');
        }
    };

    async function publishComment(ev) {
        if (ev) { ev.preventDefault(); ev.stopPropagation(); }

        if (!window.ticketId) {
            // Reutiliza tu flujo de creación
            const ticketForm = document.getElementById('ticket-form');
            const selectedStatus = document.getElementById('selected-status')?.textContent?.trim() || 'open';
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

        const textarea = document.getElementById('new-message');
        const content = (textarea?.value || '').trim();
        if (!content) { alert('El contenido no puede estar vacío.'); return; }

        const isPublic = (typeof isPublicInput !== 'undefined') ? (isPublicInput.value === 'true') : true;
        const newStatus = (document.getElementById('selected-status')?.textContent || 'open').trim().toLowerCase();

        const payload = { content, is_public: isPublic, new_status: newStatus };
        if (window.pendingAttachmentIds?.length) payload.attachment_ids = window.pendingAttachmentIds;

        try {
            const response = await fetch(`/tickets/${window.ticketId}/add_comment/`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCookie('csrftoken') },
                body: JSON.stringify(payload)
            });
            const data = await response.json();
            if (!response.ok) { alert(data.error || 'No se pudo publicar.'); return; }

            // Pinta el nuevo comentario (idéntico a sendMessage)
            const messagesBox = document.getElementById('messagesBox');
            const newComment = document.createElement('div');
            const isMe = Number(data.user_id) === Number(window.currentUserId);
            newComment.className = 'message' + (isMe ? ' me' : '') + (data.is_public ? '' : ' internal');
            const internalBadge = data.is_public ? '' : '<span class="badge-internal">Interno</span>';
            newComment.innerHTML = `
      ${internalBadge}
      <p><strong>${data.username}:</strong> ${data.content}</p>
      <span class="timestamp">${data.created_at}</span>
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
            const statusSpan = document.getElementById('selected-status');
            if (statusSpan && data.new_status) statusSpan.textContent = data.new_status;

            // Limpia composer
            textarea.value = '';
            if (typeof pendingBox !== 'undefined' && pendingBox) pendingBox.innerHTML = '';
            window.pendingAttachmentIds = [];

        } catch (err) {
            console.error('Error al publicar:', err);
            alert('Error de red al publicar.');
        }
    }


    const sendMessageButton = document.getElementById('send-message-btn');
    if (sendMessageButton) {
        sendMessageButton.addEventListener('click', sendMessage);
    }

    const ticketForm = document.getElementById('ticket-form');
    if (ticketForm) {
        ticketForm.addEventListener('submit', (e) => {
            // Solo bloqueamos el submit automático en tickets NUEVOS si faltan datos
            if (!window.ticketId) {
                const brand = (document.getElementById('empresa')?.value || '').trim();
                const subject = (document.getElementById('subject')?.value || '').trim();
                const description = (document.getElementById('new-message')?.value || '').trim();

                const errors = [];
                if (!brand) errors.push('Please provide a ticket brand');
                if (!description) errors.push('Please provide a ticket description');
                if (!subject) errors.push('Please provide a ticket subject');

                if (errors.length) {
                    e.preventDefault();
                    e.stopPropagation();
                    alert(errors.join('\n'));
                }
            }
        });
    }

    // Manejo del Dropdown de Estado
    const manageStatusDropdown = () => {
        const publishButton = document.getElementById('publish-button');
        const dropdownButton = document.getElementById('dropdown-button');
        const dropdownContent = document.getElementById('dropdown-content');
        const selectedStatus = document.getElementById('selected-status');
        const ticketForm = document.getElementById('ticket-form');

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
            if (window.ticketId) {
                publishComment(event);
                return;
            }

            // Ticket nuevo → validación + submit (tu flujo actual)
            const errors = [];
            const brand = (document.getElementById('empresa')?.value || '').trim();
            const subject = (document.getElementById('subject')?.value || '').trim();
            const description = (document.getElementById('new-message')?.value || '').trim();
            if (!brand) errors.push('Please provide a ticket brand');
            if (!description) errors.push('Please provide a ticket description');
            if (!subject) errors.push('Please provide a ticket subject');
            if (errors.length) { alert(errors.join('\n')); return; }

            const selectedStatus = document.getElementById('selected-status').textContent.trim();
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

    // Manejo de Creación de Etiquetas en Select2
    $('#tags').select2({
        tags: true,
        tokenSeparators: [','],
        placeholder: "Selecciona o añade etiquetas",
        allowClear: true,
        minimumInputLength: 1,
        ajax: {
            url: '/api/tags/',
            dataType: 'json',
            delay: 250,
            data: function (params) {
                return { q: params.term };
            },
            processResults: function (data) {
                return { results: data.results };
            },
            cache: true
        },
        createTag: function (params) {
            var term = $.trim(params.term);
            if (term === '') return null;
            return { id: term, text: term, newTag: true };
        }
    }).on('select2:select', function (e) {
        const data = e.params.data;
        if (data.newTag) {
            const newTag = data.text;
            const isValidTag = /^[a-zA-Z0-9]+$/.test(newTag);
            if (newTag && isValidTag) {
                let exists = false;
                $('#tags option').each(function () {
                    if ($(this).val() === newTag) {
                        exists = true;
                        return false;
                    }
                });

                if (!exists) {
                    const newOption = new Option(newTag, newTag, true, true);
                    $('#tags').append(newOption).trigger('change');
                }

                $('#tags').find('option[value="create_new_tag"]').remove();
            } else {
                alert('El tag solo puede contener letras y números, sin espacios en blanco.');
                $('#tags').find('option[value="create_new_tag"]').remove();
            }
        }
    });

    // Manejo de Guardar y Cargar Datos del Formulario en localStorage
    const ticketId = new URLSearchParams(window.location.search).get('id');

    const saveFormData = () => {
        if (!ticketId) return;
        const formData = {};
        $('select.select2').each(function () {
            formData[this.id] = $(this).val();
        });
        localStorage.setItem(`ticketFormData_${ticketId}`, JSON.stringify(formData));
    };

    const loadFormData = () => {
        if (!ticketId) return;
        const formData = JSON.parse(localStorage.getItem(`ticketFormData_${ticketId}`));
        if (formData) {
            $('select.select2').each(function () {
                if (formData[this.id]) {
                    $(this).val(formData[this.id]).trigger('change');
                }
            });
        }
    };

    $('select.select2').on('change', saveFormData);
    loadFormData();

    // Cargar Información del Ticket si Existe
    if (ticketId) {
        fetch(`/api/tickets/${ticketId}/`)
            .then(response => response.json())
            .then(data => {
                $('#empresa').val(data.empresa || '').trigger('change');
                $('#solicitante').val(data.solicitante).trigger('change');
                $('#asignado').val(data.asignado || '').trigger('change');
                $('#grupo').val(data.grupo || '').trigger('change');
                $('#ccs').val(data.ccs).trigger('change');
                $('#tags').val(data.tags).trigger('change');
                $('#tipo').val(data.tipo).trigger('change');
                $('#prioridad').val(data.prioridad || '').trigger('change');
                $('#servicio').val(data.servicio).trigger('change');
                $('#canal').val(data.canal).trigger('change');
                $('#idioma').val(data.idioma).trigger('change');
                $('#categoria').val(data.categoria).trigger('change');
                $('#security_related').prop('checked', !!data.security_related);
                $('#monitoring').prop('checked', !!data.monitoring);
                $('#approval_status').val(data.approval_status || '');
                $('#subject').val(data.subject);
                
            })
            .catch(error => console.error('Error al cargar el ticket:', error));
    }

    // ---- Scroll al último comentario ----
    const messagesBox = document.getElementById('messagesBox');
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

    document.querySelectorAll('.msg-avatar').forEach(el => {
        const authorName = el.closest('.message')?.querySelector('.msg-author')?.textContent.trim() || '?';
        el.style.background = avatarColor(authorName);
    });

    // Avatar del side panel del solicitante (gris neutro fijo, sin color por nombre)
    document.querySelectorAll('.rq-avatar-initials').forEach(el => {
        el.style.background = '#8a9ba8';
    });

    // Auto-guardar notas del solicitante al perder el foco
    document.querySelectorAll('.rq-notes-input').forEach(textarea => {
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
    document.querySelectorAll('.msg-body').forEach(body => {
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
    const macroBtn    = document.getElementById('macro-btn');
    const macroPanel  = document.getElementById('macro-panel');
    const macroList   = document.getElementById('macro-list');
    const macroSearch = document.getElementById('macro-search');
    const macroCaret  = document.getElementById('macro-caret');

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
                const statusSpan = document.getElementById('selected-status');
                if (statusSpan) statusSpan.textContent = a.status;
            }

            // Priority
            if (a.priority && typeof $ !== 'undefined') {
                $('#prioridad').val(a.priority).trigger('change');
            }

            // Assignee
            if (a.assignee_id != null && typeof $ !== 'undefined') {
                $('#asignado').val(String(a.assignee_id)).trigger('change');
            }

            // Canned comment
            if (a.comment) {
                const ta = document.getElementById('new-message');
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

    document.querySelectorAll('.tl-item').forEach(item => {
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
        const layout = document.getElementById('ticketLayout');
        if (!layout) return;
        const top     = layout.getBoundingClientRect().top;
        const footer  = document.querySelector('.footer-bar');
        const footerH = footer ? footer.offsetHeight : 44;
        const h = Math.max(200, window.innerHeight - top - footerH);
        layout.style.height = h + 'px';
    }
    fitLayout();
    window.addEventListener('resize', fitLayout);

    // ===== Panel collapse + resize =====
    (function initPanels() {
        const layout    = document.getElementById('ticketLayout');
        if (!layout) return;

        const panelLeft  = document.getElementById('panelLeft');
        const panelRight = document.getElementById('panelRight');
        const toggleLeft  = document.getElementById('toggleLeft');
        const toggleRight = document.getElementById('toggleRight');
        const iconLeft    = document.getElementById('iconLeft');
        const iconRight   = document.getElementById('iconRight');
        const labelLeft   = document.getElementById('labelLeft');
        const labelRight  = document.getElementById('labelRight');
        const resizerLeft  = document.getElementById('resizerLeft');
        const resizerRight = document.getElementById('resizerRight');

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
                if (label) label.textContent = 'Show panel';
            } else {
                panel.classList.remove('collapsed');
                if (icon) icon.style.transform = '';
                if (label) label.textContent = 'Hide panel';
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
        const composeResizer = document.getElementById('composeResizer');
        const column2 = document.querySelector('.column2');
        if (composeResizer && column2) {
            let startY, startH;
            const textarea = document.getElementById('new-message');
            const MIN_H = 80, MAX_H = 500;

            composeResizer.addEventListener('mousedown', e => {
                e.preventDefault();
                startY = e.clientY;
                startH = textarea ? textarea.getBoundingClientRect().height : 190;
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
        document.querySelectorAll('[data-ts]').forEach(el => {
            const rel = timeAgo(el.dataset.ts);
            if (rel) el.textContent = rel;
        });
    }

    updateRelativeTimestamps();
    setInterval(updateRelativeTimestamps, 60000);

    // ---- Botón Take it ----
    const takeItBtn = document.getElementById('take-it-btn');
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
                    const sel = document.getElementById('asignado');
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

});
