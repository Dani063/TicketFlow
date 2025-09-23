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

            const errors = [];
            const brand = (document.getElementById('empresa')?.value || '').trim();
            const subject = (document.getElementById('subject')?.value || '').trim();
            const description = (document.getElementById('new-message')?.value || '').trim();

            if (!brand) errors.push('Please provide a ticket brand');
            if (!description) errors.push('Please provide a ticket description');
            if (!subject) errors.push('Please provide a ticket subject');

            if (errors.length) {
                // puedes reemplazar alert por tu toaster si tienes uno
                alert(errors.join('\n'));
                return; // no enviamos
            }

            // inyecta <input hidden name="status"> con el valor del span
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
                $('#empresa').val(data.empresa).trigger('change');
                $('#solicitante').val(data.solicitante).trigger('change');
                $('#asignado').val(data.asignado).trigger('change');
                $('#ccs').val(data.ccs).trigger('change');
                $('#tags').val(data.tags).trigger('change');
                $('#tipo').val(data.tipo).trigger('change');
                $('#prioridad').val(data.prioridad).trigger('change');
                $('#servicio').val(data.servicio).trigger('change');
                $('#canal').val(data.canal).trigger('change');
                $('#idioma').val(data.idioma).trigger('change');
                $('#categoria').val(data.categoria).trigger('change');
                $('#subject').val(data.subject);
                
            })
            .catch(error => console.error('Error al cargar el ticket:', error));
    }
});
