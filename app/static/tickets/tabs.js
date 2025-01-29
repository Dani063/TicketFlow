// Función para cargar las pestañas desde localStorage
function loadTabs() {
    const tabs = JSON.parse(localStorage.getItem('tabs')) || [];
    tabs.forEach(tab => {
        addTab(tab.text, tab.url, false);
    });
}

// Función para guardar las pestañas en localStorage
function saveTabs() {
    const tabs = [];
    document.querySelectorAll('.navbar-left .tab').forEach(tab => {
        tabs.push({ text: tab.innerText.replace(/\sX$/, ''), url: tab.dataset.url });
    });
    localStorage.setItem('tabs', JSON.stringify(tabs));
}

// Función para añadir una pestaña
function addTab(text, url, select = true) {
    const tab = document.createElement('div');
    tab.className = 'tab';
    tab.dataset.url = url;

    const img = document.createElement('img');
    img.src = '{% static "tickets/logo.png" %}';
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
}

// Evento para añadir una nueva pestaña
document.querySelector('.add-tab').addEventListener('click', () => {
    const tabText = "Nuevo ticket";
    const tabId = `ticket-${Date.now()}`;
    addTab(tabText, `{% url "create_ticket" %}?id=${tabId}`);
});

// Función para marcar la pestaña activa
function highlightActiveTab() {
    const urlParams = new URLSearchParams(window.location.search);
    const activeTabId = urlParams.get('id'); // Obtiene el ID del ticket de la URL

    if (activeTabId) {
        document.querySelectorAll('.navbar-left .tab').forEach(tab => {
            if (tab.dataset.url.includes(`id=${activeTabId}`)) {
                tab.classList.add('active'); // Marca la pestaña activa
            } else {
                tab.classList.remove('active'); // Quita la clase de las demás
            }
        });
    }
}

// Llama a la función después de cargar las pestañas
document.addEventListener('DOMContentLoaded', () => {
    loadTabs();
    highlightActiveTab();
});