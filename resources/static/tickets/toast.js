/* ============================================================
 * Toast notification system
 * Usage: window.toast.success(msg), .error(msg), .warning(msg), .info(msg)
 *        Optional opts: { duration: 4000, dismissible: true }
 *        Returns the toast element so callers can dismiss manually.
 * ============================================================ */
(function () {
    'use strict';

    const DEFAULT_DURATION = 4000;
    const ICONS = {
        success: 'fa-circle-check',
        error:   'fa-circle-exclamation',
        warning: 'fa-triangle-exclamation',
        info:    'fa-circle-info',
    };

    let _container = null;

    function ensureContainer() {
        if (_container && document.body.contains(_container)) return _container;
        _container = document.createElement('div');
        _container.className = 'toast-container';
        _container.setAttribute('role', 'region');
        _container.setAttribute('aria-live', 'polite');
        _container.setAttribute('aria-label', 'Notificaciones');
        document.body.appendChild(_container);
        return _container;
    }

    function dismiss(el) {
        if (!el || el.dataset.dismissing === '1') return;
        el.dataset.dismissing = '1';
        el.classList.add('toast--leaving');
        const finish = () => { if (el.parentNode) el.parentNode.removeChild(el); };
        el.addEventListener('transitionend', finish, { once: true });
        // Fallback in case transitionend doesn't fire (e.g. reduced motion)
        setTimeout(finish, 400);
    }

    function show(variant, message, opts) {
        if (!message) return null;
        opts = opts || {};
        const container = ensureContainer();

        const toast = document.createElement('div');
        toast.className = `toast toast--${variant}`;
        toast.setAttribute('role', variant === 'error' ? 'alert' : 'status');

        const icon = document.createElement('i');
        icon.className = `toast__icon fas ${ICONS[variant] || ICONS.info}`;
        icon.setAttribute('aria-hidden', 'true');

        const body = document.createElement('div');
        body.className = 'toast__body';
        body.textContent = String(message);

        toast.appendChild(icon);
        toast.appendChild(body);

        if (opts.dismissible !== false) {
            const closeBtn = document.createElement('button');
            closeBtn.type = 'button';
            closeBtn.className = 'toast__close';
            closeBtn.setAttribute('aria-label', 'Cerrar');
            closeBtn.innerHTML = '&times;';
            closeBtn.addEventListener('click', () => dismiss(toast));
            toast.appendChild(closeBtn);
        }

        container.appendChild(toast);
        // Force reflow then animate in
        // eslint-disable-next-line no-unused-expressions
        toast.offsetHeight;
        toast.classList.add('toast--visible');

        const duration = typeof opts.duration === 'number' ? opts.duration : DEFAULT_DURATION;
        if (duration > 0) {
            let timer = null;
            let remaining = duration;
            let startTime = 0;
            const startTimer = () => {
                startTime = Date.now();
                timer = setTimeout(() => dismiss(toast), remaining);
            };
            const pauseTimer = () => {
                if (!timer) return;
                clearTimeout(timer);
                timer = null;
                remaining -= (Date.now() - startTime);
            };
            startTimer();
            toast.addEventListener('mouseenter', pauseTimer);
            toast.addEventListener('mouseleave', () => { if (remaining > 0) startTimer(); });
        }

        return toast;
    }

    window.toast = {
        success: (msg, opts) => show('success', msg, opts),
        error:   (msg, opts) => show('error',   msg, opts),
        warning: (msg, opts) => show('warning', msg, opts),
        info:    (msg, opts) => show('info',    msg, opts),
        dismiss,
    };
})();
