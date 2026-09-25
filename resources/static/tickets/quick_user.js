/* Alta rápida de solicitante desde el desplegable «Solicitante» del ticket.
 *
 *   const user = await window.TFQuickUser.open({ email: 'prefill@ejemplo.com', name: '' });
 *   if (user) { ... user.id, user.text ... }
 *
 * Pide el email y, opcionalmente, el nombre; el backend (/api/users/quick-create/)
 * lo da de alta como end user (sin nombre, lo saca del email). Si ya existía,
 * devuelve ese usuario.
 * Mismo aspecto que window.dialog.confirm (dialog.css).
 *
 * Re-entrante: base.html lo carga una vez, pero tabs.js puede re-ejecutarlo.
 */
(function () {
    'use strict';
    if (window.TFQuickUser) return;

    function getCookie(name) {
        const v = document.cookie.match('(^|;) ?' + name + '=([^;]*)(;|$)');
        return v ? decodeURIComponent(v[2]) : null;
    }

    function open(opts) {
        opts = opts || {};
        return new Promise((resolve) => {
            const backdrop = document.createElement('div');
            backdrop.className = 'dialog-backdrop';
            backdrop.innerHTML = `
                <div class="dialog dialog--primary dialog--form" role="dialog" aria-modal="true" aria-labelledby="tfQuickUserTitle">
                    <div class="dialog__header">
                        <div class="dialog__icon"><i class="fas fa-user-plus" aria-hidden="true"></i></div>
                        <h3 id="tfQuickUserTitle" class="dialog__title">Añadir usuario</h3>
                    </div>
                    <form class="dialog__body" novalidate>
                        <div class="tf-field">
                            <label for="tfQuickUserEmail">Email</label>
                            <input type="email" id="tfQuickUserEmail" class="tf-input" autocomplete="off" placeholder="nombre@empresa.com" required>
                            <small class="tf-quick-user__error" role="alert" hidden></small>
                        </div>
                        <div class="tf-field">
                            <label for="tfQuickUserName">Nombre <span class="tf-quick-user__optional">(opcional)</span></label>
                            <input type="text" id="tfQuickUserName" class="tf-input" autocomplete="off" maxlength="255" placeholder="Si lo dejas vacío, se toma del email">
                        </div>
                        <small class="tf-quick-user__hint">Se dará de alta como end user. Podrás cambiar sus datos después desde el panel de usuarios.</small>
                    </form>
                    <div class="dialog__footer">
                        <button type="button" class="dialog__btn dialog__btn--cancel">Cancelar</button>
                        <button type="button" class="dialog__btn dialog__btn--confirm dialog__btn--primary">Añadir</button>
                    </div>
                </div>
            `;
            document.body.appendChild(backdrop);

            const form = backdrop.querySelector('form');
            const input = backdrop.querySelector('#tfQuickUserEmail');
            const nameInput = backdrop.querySelector('#tfQuickUserName');
            const error = backdrop.querySelector('.tf-quick-user__error');
            const confirmBtn = backdrop.querySelector('.dialog__btn--confirm');
            const cancelBtn = backdrop.querySelector('.dialog__btn--cancel');
            const prevFocus = document.activeElement;
            input.value = opts.email || '';
            nameInput.value = opts.name || '';

            // eslint-disable-next-line no-unused-expressions
            backdrop.offsetHeight;
            backdrop.classList.add('open');

            let closed = false;
            function close(result) {
                if (closed) return;
                closed = true;
                backdrop.classList.remove('open');
                backdrop.classList.add('closing');
                document.removeEventListener('keydown', onKey);
                const finish = () => {
                    if (backdrop.parentNode) backdrop.parentNode.removeChild(backdrop);
                    if (!result && prevFocus && typeof prevFocus.focus === 'function') {
                        try { prevFocus.focus(); } catch (e) { /* noop */ }
                    }
                };
                backdrop.addEventListener('transitionend', finish, { once: true });
                setTimeout(finish, 350);
                resolve(result);
            }

            function showError(msg) {
                error.textContent = msg;
                error.hidden = !msg;
                input.classList.toggle('is-invalid', !!msg);
            }

            async function submit() {
                const email = input.value.trim();
                if (!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(email)) {
                    showError('Introduce un email válido.');
                    input.focus();
                    return;
                }
                showError('');
                confirmBtn.disabled = true;
                try {
                    const res = await fetch('/api/users/quick-create/', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCookie('csrftoken') },
                        body: JSON.stringify({ email, name: nameInput.value.trim() }),
                    });
                    const data = await res.json();
                    if (!res.ok || !data.ok) {
                        showError((data.error && data.error.message) || 'No se ha podido añadir el usuario.');
                        confirmBtn.disabled = false;
                        return;
                    }
                    if (window.toast) {
                        if (data.created) window.toast.success('Usuario añadido: ' + data.user.email);
                        else window.toast.info('Ya existía, se ha seleccionado: ' + data.user.email);
                    }
                    close(data.user);
                } catch (e) {
                    showError('Error de red. Inténtalo de nuevo.');
                    confirmBtn.disabled = false;
                }
            }

            form.addEventListener('submit', (e) => { e.preventDefault(); submit(); });
            confirmBtn.addEventListener('click', submit);
            cancelBtn.addEventListener('click', () => close(null));
            backdrop.addEventListener('click', (e) => { if (e.target === backdrop) close(null); });
            const onKey = (e) => {
                if (e.key === 'Escape') { e.preventDefault(); close(null); }
            };
            document.addEventListener('keydown', onKey);

            // Al primer campo por rellenar: sin email, el email; con él, el nombre.
            setTimeout(() => (input.value ? nameInput : input).focus(), 30);
        });
    }

    window.TFQuickUser = { open };
})();
