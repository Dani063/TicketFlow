(function () {
    'use strict';
    var input = document.getElementById('attachments');
    var list = document.getElementById('attachment-list');
    if (input && list) {
        input.addEventListener('change', function () {
            list.textContent = '';
            Array.prototype.forEach.call(input.files || [], function (file) {
                var item = document.createElement('li');
                item.textContent = file.name + ' · ' + Math.max(1, Math.round(file.size / 1024)) + ' KB';
                list.appendChild(item);
            });
        });
    }
    var emailLinks = document.querySelectorAll('.hc-email[data-hc-mail]');
    Array.prototype.forEach.call(emailLinks, function (link) {
        var address = link.getAttribute('data-hc-mail').split('').reverse().join('');
        link.href = 'mailto:' + address;
        link.textContent = address;
    });
    var form = document.getElementById('public-ticket-form');
    if (form) {
        form.addEventListener('submit', function () {
            var button = form.querySelector('button[type="submit"]');
            if (!button || button.disabled) return;
            button.disabled = true;
            button.textContent = button.getAttribute('data-sending') || 'Sending...';
        });
    }
}());
