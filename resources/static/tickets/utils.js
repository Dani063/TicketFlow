/* ============================================================
   TicketFlow shared JS utilities — exposed as window.TF.*
   Centralizes patterns previously copy-pasted across views:
   CSRF token, fetch wrapper, escapeHtml, debounce, timeAgo,
   avatarColor.
   ============================================================ */
(function () {
    'use strict';

    const TF = window.TF || (window.TF = {});

    /* ---- CSRF cookie ---- */
    TF.getCSRF = function () {
        for (const c of document.cookie.split(';')) {
            const [k, v] = c.trim().split('=');
            if (k === 'csrftoken') return decodeURIComponent(v || '');
        }
        return '';
    };

    /* ---- HTML escape ---- */
    TF.escapeHtml = function (s) {
        return String(s == null ? '' : s)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#39;');
    };

    /* ---- Debounce ---- */
    TF.debounce = function (fn, wait) {
        let t = null;
        return function () {
            const ctx = this, args = arguments;
            clearTimeout(t);
            t = setTimeout(() => fn.apply(ctx, args), wait || 200);
        };
    };

    /* ---- Fetch wrapper with CSRF + JSON helpers ----
       Usage:
         await TF.fetch('/api/x/', { method: 'POST', body: {...} })
       - body as object → auto JSON-stringify + Content-Type
       - adds X-CSRFToken for unsafe methods
       - parses JSON response; throws on non-OK
    */
    TF.fetch = async function (url, opts) {
        opts = opts || {};
        const method = (opts.method || 'GET').toUpperCase();
        const headers = Object.assign({}, opts.headers || {});

        let body = opts.body;
        if (body && typeof body === 'object' && !(body instanceof FormData)) {
            body = JSON.stringify(body);
            if (!headers['Content-Type']) headers['Content-Type'] = 'application/json';
        }
        if (!['GET', 'HEAD', 'OPTIONS'].includes(method) && !headers['X-CSRFToken']) {
            headers['X-CSRFToken'] = TF.getCSRF();
        }

        const res = await fetch(url, { method, headers, body, credentials: 'same-origin' });
        const ct = res.headers.get('Content-Type') || '';
        let data = null;
        if (ct.includes('application/json')) {
            try { data = await res.json(); } catch (e) { data = null; }
        } else {
            data = await res.text();
        }
        if (!res.ok) {
            const err = new Error((data && data.error) || `HTTP ${res.status}`);
            err.status = res.status;
            err.data = data;
            throw err;
        }
        return data;
    };

    /* ---- Relative-time formatter (e.g. "hace 5 min") ---- */
    TF.timeAgo = function (input) {
        if (!input) return '';
        const d = (input instanceof Date) ? input : new Date(input);
        if (isNaN(d.getTime())) return String(input);
        const diff = Math.floor((Date.now() - d.getTime()) / 1000);
        if (diff < 60)        return 'hace un momento';
        if (diff < 3600)      return `hace ${Math.floor(diff/60)} min`;
        if (diff < 86400)     return `hace ${Math.floor(diff/3600)} h`;
        if (diff < 604800)    return `hace ${Math.floor(diff/86400)} d`;
        return d.toLocaleDateString();
    };

    /* ---- Avatar color hash (deterministic per name) ---- */
    const _palette = ['#5b67ca','#e06c75','#56b6c2','#98c379','#d19a66','#c678dd','#61afef','#e5c07b'];
    TF.avatarColor = function (name) {
        const s = String(name || '');
        let hash = 0;
        for (let i = 0; i < s.length; i++) hash = s.charCodeAt(i) + ((hash << 5) - hash);
        return _palette[Math.abs(hash) % _palette.length];
    };
})();
