// app/static/tickets/create_ticket.js

document.addEventListener('DOMContentLoaded', () => {
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

    // Manejo de Popups
    const togglePopup = (popupId, relatedPopupId) => {
        const popup = document.getElementById(popupId);
        const relatedPopup = relatedPopupId ? document.getElementById(relatedPopupId) : null;
        const relatedIcon = relatedPopupId ? document.querySelector(`.${relatedPopupId === 'userPopup' ? 'notifications' : 'user-profile'}`) : null;

        if (popup.style.display === 'block') {
            popup.style.display = 'none';
            document.querySelector(`.${popupId === 'userPopup' ? 'user-profile' : 'notifications'}`).classList.remove('active');
        } else {
            popup.style.display = 'block';
            document.querySelector(`.${popupId === 'userPopup' ? 'user-profile' : 'notifications'}`).classList.add('active');

            if (relatedPopup && relatedPopup.style.display === 'block') {
                relatedPopup.style.display = 'none';
                relatedIcon.classList.remove('active');
            }
        }
    };

    // Evento Click en Perfil de Usuario
    document.querySelector('.user-profile').addEventListener('click', () => {
        togglePopup('userPopup', 'notificationsPopup');
    });

    // Evento Click en Notificaciones
    document.querySelector('.notifications').addEventListener('click', () => {
        togglePopup('notificationsPopup', 'userPopup');
        loadNotifications();
    });

    // Cerrar Popup de Notificaciones
    document.getElementById('closePopup').addEventListener('click', () => {
        document.getElementById('notificationsPopup').style.display = 'none';
        document.querySelector('.notifications').classList.remove('active');
    });

    // Función para Cargar Notificaciones
    const loadNotifications = () => {
        const notifications = [
            'Notificación 1',
            'Notificación 2',
            'Notificación 3'
        ];

        const notificationsList = document.getElementById('notificationsList');
        notificationsList.innerHTML = '';
        notifications.forEach(notification => {
            const li = document.createElement('li');
            li.textContent = notification;
            notificationsList.appendChild(li);
        });
    };

    // Logout Button
    document.getElementById('logoutButton').addEventListener('click', () => {
        window.location.href = window.urls.login;
    });

    // Manejo de Envío de Mensajes
    const sendMessage = async (ev) => {
        if (ev) {
            ev.preventDefault();
            ev.stopPropagation();
        }
        const content = document.getElementById('new-message').value.trim();

        // Si aún no existe id real, validar como en "Publicar"
        if (!window.ticketId) {
            const errors = [];
            const brand = (document.getElementById('empresa')?.value || '').trim();
            const subject = (document.getElementById('subject')?.value || '').trim();

            if (!brand) errors.push('Please provide a ticket brand');
            if (!content) errors.push('Please provide a ticket description');
            if (!subject) errors.push('Please provide a ticket subject');

            if (errors.length) {
                alert(errors.join('\n'));
                return; // NO enviamos nada
            }

            // Inyecta status y ahora sí crea/redirige
            const ticketForm = document.getElementById('ticket-form');
            const selectedStatus = document.getElementById('selected-status').textContent.trim();
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

        // Ticket existente: solo validar contenido
        if (!content) {
            alert('El contenido no puede estar vacío.');
            return;
        }

        try {
            const response = await fetch(`/tickets/${window.ticketId}/add_comment/`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-CSRFToken': getCookie('csrftoken')
                },
                body: JSON.stringify({ content })
            });

            const data = await response.json();
            if (response.ok) {
                const messagesBox = document.getElementById('messagesBox');
                const newComment = document.createElement('div');
                const isMe = Number(data.user_id) === Number(window.currentUserId);
                newComment.className = 'message' + (isMe ? ' me' : '');
                newComment.innerHTML = `
        <p><strong>${data.username}:</strong> ${data.content}</p>
        <span class="timestamp">${data.created_at}</span>
      `;
                messagesBox.appendChild(newComment);
                document.getElementById('new-message').value = '';
                messagesBox.scrollTop = messagesBox.scrollHeight;
            } else {
                alert(data.error || 'No se pudo enviar el mensaje.');
            }
        } catch (error) {
            console.error('Error al enviar el mensaje:', error);
        }
    };

    const sendMessageButton = document.getElementById('send-message');
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
