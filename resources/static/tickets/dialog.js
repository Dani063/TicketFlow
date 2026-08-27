/* ============================================================
 * Confirm dialog component
 * Usage:
 *   const ok = await window.dialog.confirm({
 *       title: 'Eliminar tickets',
 *       message: '¿Eliminar 3 ticket(s)?',
 *       confirmText: 'Eliminar',
 *       cancelText: 'Cancelar',
 *       variant: 'danger',  // 'danger' | 'primary' (default)
 *   });
 *   if (!ok) return;
 * ============================================================ */
(function () {
    'use strict';

    const ICONS = {
        danger:  'fa-triangle-exclamation',
        primary: 'fa-circle-question',
        info:    'fa-circle-info',
    };

    function escapeHtml(s) {
        return String(s == null ? '' : s)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#039;');
    }

    function confirmDialog(opts) {
        opts = opts || {};
        const title       = opts.title       || '¿Confirmar acción?';
        const message     = opts.message     || '';
        const confirmText = opts.confirmText || 'Confirmar';
        const cancelText  = opts.cancelText  || 'Cancelar';
        const variant     = opts.variant === 'danger' ? 'danger' : 'primary';
        const icon        = ICONS[variant] || ICONS.primary;

        return new Promise((resolve) => {
            const backdrop = document.createElement('div');
            backdrop.className = 'dialog-backdrop';

            backdrop.innerHTML = `
                <div class="dialog dialog--${variant}" role="dialog" aria-modal="true" aria-labelledby="dialogTitle">
                    <div class="dialog__header">
                        <div class="dialog__icon"><i class="fas ${icon}" aria-hidden="true"></i></div>
                        <h3 id="dialogTitle" class="dialog__title">${escapeHtml(title)}</h3>
                    </div>
                    <div class="dialog__body">${escapeHtml(message)}</div>
                    <div class="dialog__footer">
                        <button type="button" class="dialog__btn dialog__btn--cancel">${escapeHtml(cancelText)}</button>
                        <button type="button" class="dialog__btn dialog__btn--confirm dialog__btn--${variant}">${escapeHtml(confirmText)}</button>
                    </div>
                </div>
            `;

            document.body.appendChild(backdrop);

            const modal      = backdrop.querySelector('.dialog');
            const confirmBtn = backdrop.querySelector('.dialog__btn--confirm');
            const cancelBtn  = backdrop.querySelector('.dialog__btn--cancel');
            const prevFocus  = document.activeElement;

            // Force reflow then animate in
            // eslint-disable-next-line no-unused-expressions
            backdrop.offsetHeight;
            backdrop.classList.add('open');

            let closed = false;
            const close = (result) => {
                if (closed) return;
                closed = true;
                backdrop.classList.remove('open');
                backdrop.classList.add('closing');
                document.removeEventListener('keydown', onKey);
                const finish = () => {
                    if (backdrop.parentNode) backdrop.parentNode.removeChild(backdrop);
                    if (prevFocus && typeof prevFocus.focus === 'function') {
                        try { prevFocus.focus(); } catch (e) { /* noop */ }
                    }
                };
                backdrop.addEventListener('transitionend', finish, { once: true });
                setTimeout(finish, 350); // fallback
                resolve(result);
            };

            confirmBtn.addEventListener('click', () => close(true));
            cancelBtn.addEventListener('click',  () => close(false));
            backdrop.addEventListener('click', (e) => {
                if (e.target === backdrop) close(false);
            });

            // Basic focus trap + ESC / Enter shortcuts
            const focusables = [cancelBtn, confirmBtn];
            const onKey = (e) => {
                if (e.key === 'Escape') {
                    e.preventDefault();
                    close(false);
                } else if (e.key === 'Enter' && document.activeElement === confirmBtn) {
                    e.preventDefault();
                    close(true);
                } else if (e.key === 'Tab') {
                    const i = focusables.indexOf(document.activeElement);
                    if (i !== -1) {
                        e.preventDefault();
                        const next = e.shiftKey ? (i - 1 + focusables.length) % focusables.length
                                                : (i + 1) % focusables.length;
                        focusables[next].focus();
                    }
                }
            };
            document.addEventListener('keydown', onKey);

            // Focus cancel by default — safer for destructive flows
            setTimeout(() => cancelBtn.focus(), 30);
        });
    }

    window.dialog = window.dialog || {};
    window.dialog.confirm = confirmDialog;
})();
