document.querySelector('.notifications').addEventListener('click', () => {
    const notificationsPopup = document.getElementById('notificationsPopup');
    const notificationsIcon = document.querySelector('.notifications');
    if (notificationsPopup.style.display === 'block') {
        notificationsPopup.style.display = 'none';
        notificationsIcon.classList.remove('active');
    } else {
        notificationsPopup.style.display = 'block';
        notificationsIcon.classList.add('active');
        loadNotifications();
    }
});

document.getElementById('closePopup').addEventListener('click', () => {
    document.getElementById('notificationsPopup').style.display = 'none';
    document.querySelector('.notifications').classList.remove('active');
});

function loadNotifications() {
    // Aquí puedes cargar las notificaciones del usuario, por ejemplo, haciendo una llamada AJAX
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
}