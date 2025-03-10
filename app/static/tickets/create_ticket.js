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

    // Funciones de Manejo de Pestañas
    const loadTabs = () => {
        const tabs = JSON.parse(localStorage.getItem('tabs')) || [];
        tabs.forEach(tab => {
            addTab(tab.text, tab.url, false);
        });
    };

    const saveTabs = () => {
        const tabs = [];
        document.querySelectorAll('.navbar-left .tab').forEach(tab => {
            tabs.push({ text: tab.innerText.replace(/\sX$/, ''), url: tab.dataset.url });
        });
        localStorage.setItem('tabs', JSON.stringify(tabs));
    };

    const addTab = (text, url, select = true) => {
        const tab = document.createElement('div');
        tab.className = 'tab';
        tab.dataset.url = url;

        const img = document.createElement('img');
        img.src = window.static_urls.logo;
        img.alt = 'Icono';
        img.className = 'tab-icon';

        const tabText = document.createElement('span');
        tabText.className = 'tab-text';
        tabText.innerText = text;

        const closeButton = document.createElement('button');
        closeButton.innerText = 'X';
        closeButton.className = 'close-tab';
        closeButton.addEventListener('click', (e) => {
            e.stopPropagation();
            const nextTab = tab.nextElementSibling || tab.previousElementSibling;
            tab.remove();
            saveTabs();
            if (nextTab) {
                window.location.href = nextTab.dataset.url;
            } else {
                window.location.href = window.urls.tickets_list;
            }
        });

        tab.appendChild(img);
        tab.appendChild(tabText);
        tab.appendChild(closeButton);

        tab.addEventListener('click', () => {
            document.querySelectorAll('.navbar-left .tab').forEach(t => t.classList.remove('active'));
            tab.classList.add('active');
            window.location.href = url;
        });

        document.querySelector('.navbar-left').appendChild(tab);
        if (select) {
            tab.click();
        }
        saveTabs();
    };

    // Evento para Añadir Nueva Pestaña
    document.querySelector('.add-tab').addEventListener('click', () => {
        const tabText = "Nuevo ticket";
        const tabId = `ticket-${Date.now()}`;
        addTab(tabText, `${window.urls.create_ticket}?id=${tabId}`);
    });

    const highlightActiveTab = () => {
        const urlParams = new URLSearchParams(window.location.search);
        const activeTabId = urlParams.get('id');

        if (activeTabId) {
            document.querySelectorAll('.navbar-left .tab').forEach(tab => {
                if (tab.dataset.url.includes(`id=${activeTabId}`)) {
                    tab.classList.add('active');
                } else {
                    tab.classList.remove('active');
                }
            });
        }
    };

    // Inicializar Pestañas al Cargar
    loadTabs();
    highlightActiveTab();

    // Manejo de Envío de Mensajes
    const sendMessage = async () => {
        const content = document.getElementById('new-message').value.trim();
        if (!content) {
            alert('El contenido no puede estar vacío.');
            return;
        }

        const urlParams = new URLSearchParams(window.location.search);
        const ticketId = urlParams.get('id');

        if (!ticketId) {
            alert('No se pudo obtener el ID del ticket.');
            return;
        }

        try {
            const response = await fetch(`/tickets/create/?id=${ticketId}/add_comment/`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-CSRFToken': getCookie('csrftoken')
                },
                body: JSON.stringify({
                    ticket_id: ticketId.split('-')[1],
                    content: content
                })
            });

            const data = await response.json();
            if (response.ok) {
                const messagesBox = document.querySelector('.messages-box');
                const newComment = document.createElement('div');
                newComment.classList.add('message');
                newComment.innerHTML = `
                    <p><strong>${data.username}:</strong> ${data.content}</p>
                    <span class="timestamp">${data.created_at}</span>
                `;
                messagesBox.appendChild(newComment);
                document.getElementById('new-message').value = '';
            } else {
                alert(data.error);
            }
        } catch (error) {
            console.error('Error al enviar el mensaje:', error);
        }
    };

    const getCookie = (name) => {
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
    };

    // Asignar Evento al Botón de Enviar Mensaje
    const sendMessageButton = document.getElementById('send-message');
    if (sendMessageButton) {
        sendMessageButton.addEventListener('click', sendMessage);
    }

    // Manejo del Dropdown de Estado
    const manageStatusDropdown = () => {
        const publishButton = document.getElementById('publish-button');
        const dropdownButton = document.getElementById('dropdown-button');
        const dropdownContent = document.getElementById('dropdown-content');
        const selectedStatus = document.getElementById('selected-status');
        const ticketForm = document.getElementById('ticket-form');

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
            const status = selectedStatus.textContent.trim();
            const statusInput = document.createElement('input');
            statusInput.type = 'hidden';
            statusInput.name = 'status';
            statusInput.value = status;
            ticketForm.appendChild(statusInput);
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
                // Rellenar otros campos si es necesario
            })
            .catch(error => console.error('Error al cargar el ticket:', error));
    }
});
