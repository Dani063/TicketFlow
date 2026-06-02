/* ============================================================
   TF.bindFilters — debounced search input + immediate-change
   selects + URL persistence (history.replaceState).
   Usage:
     TF.bindFilters({
         searchEl: '#customersSearch',
         selectEls: ['#customersRoleFilter', '#customersGroupFilter'],
         urlKeys:   { search: 'q', selects: ['role_id', 'group_id'] },
         onChange:  () => loadCustomers(),
         debounceMs: 260,        // optional, default 260
         restoreFromUrl: true,   // optional, default true
     });
   Returns: { snapshot() }  — current { q, selects: [...] } values.
   ============================================================ */
(function () {
    'use strict';
    const TF = window.TF || (window.TF = {});

    function $(sel) {
        if (!sel) return null;
        if (typeof sel === 'string') return document.querySelector(sel);
        return sel;
    }

    TF.bindFilters = function (opts) {
        opts = opts || {};
        const searchEl  = $(opts.searchEl);
        const selectEls = (opts.selectEls || []).map($);
        const urlKeys   = opts.urlKeys || {};
        const onChange  = opts.onChange || function () {};
        const debounceMs = opts.debounceMs || 260;
        const restoreFromUrl = opts.restoreFromUrl !== false;

        function persistUrl() {
            const u = new URL(window.location);
            if (urlKeys.search && searchEl) {
                const v = (searchEl.value || '').trim();
                if (v) u.searchParams.set(urlKeys.search, v);
                else   u.searchParams.delete(urlKeys.search);
            }
            (urlKeys.selects || []).forEach((key, i) => {
                const sel = selectEls[i];
                if (!sel) return;
                if (sel.value) u.searchParams.set(key, sel.value);
                else           u.searchParams.delete(key);
            });
            window.history.replaceState({}, '', u);
        }

        if (restoreFromUrl) {
            const sp = new URLSearchParams(window.location.search);
            if (urlKeys.search && searchEl) {
                const v = sp.get(urlKeys.search);
                if (v != null) searchEl.value = v;
            }
            (urlKeys.selects || []).forEach((key, i) => {
                const sel = selectEls[i];
                if (!sel) return;
                const v = sp.get(key);
                if (v != null) sel.value = v;
            });
        }

        if (searchEl) {
            const handler = TF.debounce(() => { persistUrl(); onChange(); }, debounceMs);
            searchEl.addEventListener('input', handler);
        }
        selectEls.forEach(sel => {
            if (!sel) return;
            sel.addEventListener('change', () => { persistUrl(); onChange(); });
        });

        return {
            snapshot() {
                return {
                    q: searchEl ? (searchEl.value || '').trim() : '',
                    selects: selectEls.map(s => s ? s.value : ''),
                };
            },
        };
    };
})();
