/* ============================================================
   Tabla del dashboard (home) — paginación, orden y acciones en
   bloque contra la misma API que /tickets/ (`/tickets/filter/`).

   Los botones de estadísticas (YOU, GROUPS, BIEN, MAL, SOLVENTADO,
   SATISFACCIÓN) son vistas de servidor `home_*`, así que el filtro
   se compone con la paginación y con el orden: cambiar de página no
   pierde el filtro, igual que el filtro de empresa en /tickets/.

   RE-ENTRANTE: tabs.js re-ejecuta los scripts del fragmento al
   volver a la pestaña, así que todo va en un IIFE y los listeners
   se registran sobre elementos recién montados.
   ============================================================ */
(function () {
    const $ = (id) => document.getElementById(id);
    if (!$('homeTicketsTableBody')) return; // no estamos en la home

    const data = window.__homeData || {};
    const CREATE_TICKET_URL = data.createTicketUrl || '/tickets/create/';
    const BULK_DELETE_URL = data.bulkDeleteUrl || '/tickets/bulk_delete/';
    const BULK_MERGE_URL = data.bulkMergeUrl || '/tickets/bulk_merge/';

    const DEFAULT_VIEW = 'home_all';
    const COLSPAN = 8;

    // Título del encabezado por vista, para que se vea qué filtro está puesto.
    const VIEW_TITLES = {
        home_all: 'Tickets que requieren tu atención',
        home_you: 'Tus tickets abiertos',
        home_groups: 'Tickets abiertos de tu grupo',
        home_bien: 'Tus tickets con valoración buena',
        home_mal: 'Tus tickets con valoración mala',
        home_solventado: 'Tus tickets solventados',
        home_satisfaccion: 'Tus tickets valorados',
    };

    let _view = DEFAULT_VIEW;
    let _page = 1;
    let _pageSize = 50;
    let _sortBy = '';
    let _sortDir = 'asc';
    let _selected = new Set();
    let _reqSeq = 0;

    function getCookie(name) {
        for (const c of document.cookie.split(';')) {
            const [k, v] = c.trim().split('=');
            if (k === name) return decodeURIComponent(v || '');
        }
        return '';
    }

    function emptyRow(text, color) {
        const style = color ? ` style="color: ${color};"` : '';
        return `<tr><td colspan="${COLSPAN}"><div class="tf-empty tf-empty--compact">` +
            `<span class="tf-empty-icon"><i class="fas fa-inbox"></i></span>` +
            `<span class="tf-empty-title"${style}>Sin tickets que requieran atención</span>` +
            `<span class="tf-empty-message"${style}>${text}</span></div></td></tr>`;
    }

    function openTicket(id, subject) {
        const tabUrl = CREATE_TICKET_URL + '?id=' + id;
        if (window.Tabs && typeof window.Tabs.addTab === 'function') {
            window.Tabs.addTab(subject || ('Ticket ' + id), tabUrl);
        } else {
            window.location.href = tabUrl;
        }
    }

    // ---- Selección y barra de acciones ----------------------------------

    function updateActionBar() {
        const countEl = $('homeTabActionCount');
        if (countEl) countEl.textContent = _selected.size;
        const bar = $('homeTicketActionBar');
        if (bar) bar.classList.toggle('visible', _selected.size > 0);
    }

    function clearSelection() {
        _selected.clear();
        document.querySelectorAll('#homeTicketsTableBody tr.row-selected')
            .forEach(r => r.classList.remove('row-selected'));
        document.querySelectorAll('#homeTicketsTableBody .ticket-checkbox')
            .forEach(cb => { cb.checked = false; });
        const all = $('homeSelectAll');
        if (all) all.checked = false;
        updateActionBar();
    }

    function syncSelectAll() {
        const all = $('homeSelectAll');
        if (!all) return;
        const boxes = document.querySelectorAll('#homeTicketsTableBody .ticket-checkbox:not(:disabled)');
        const checked = document.querySelectorAll('#homeTicketsTableBody .ticket-checkbox:not(:disabled):checked');
        all.checked = boxes.length > 0 && boxes.length === checked.length;
    }

    // ---- Alto de la tabla -------------------------------------------------
    // La barra de paginación tiene que quedar siempre a la vista, así que la
    // tabla scrollea por dentro. El alto se calcula desde su posición real en
    // lugar de fijarlo en CSS: la cabecera de la home (actualizaciones, caja de
    // estadísticas) cambia de alto según el ancho de ventana, y el
    // `max-height: calc(100vh - 150px)` que trae .tabla se queda largo aquí y
    // empuja la barra fuera del viewport.
    function fitTableHeight() {
        const tabla = document.querySelector('.tabla');
        if (!tabla) return;
        const bar = $('homePaginationBar');
        const top = tabla.getBoundingClientRect().top;
        const barH = bar && bar.style.display !== 'none' ? bar.offsetHeight : 0;
        const avail = window.innerHeight - top - barH - 24;   // 24 = respiro inferior
        tabla.style.maxHeight = Math.max(200, Math.round(avail)) + 'px';
    }

    // ---- Paginación ------------------------------------------------------

    function pageRange(current, total) {
        if (total <= 7) return Array.from({ length: total }, (_, i) => i + 1);
        if (current <= 4) return [1, 2, 3, 4, 5, '...', total];
        if (current >= total - 3) return [1, '...', total - 4, total - 3, total - 2, total - 1, total];
        return [1, '...', current - 1, current, current + 1, '...', total];
    }

    function renderPagination(p) {
        const bar = $('homePaginationBar');
        const box = $('homePageNumbers');
        if (!bar || !box) return;
        box.innerHTML = '';

        const total = p.total_pages || 1;
        const cur = p.page || 1;

        if (cur > 1) {
            const prev = document.createElement('button');
            prev.className = 'page-num-btn page-arrow';
            prev.innerHTML = '&#8249;';
            prev.title = 'Página anterior';
            prev.onclick = () => load(_view, cur - 1);
            box.appendChild(prev);
        }

        pageRange(cur, total).forEach(n => {
            if (n === '...') {
                const dots = document.createElement('span');
                dots.className = 'page-ellipsis';
                dots.textContent = '...';
                box.appendChild(dots);
                return;
            }
            const b = document.createElement('button');
            b.className = 'page-num-btn' + (n === cur ? ' active' : '');
            b.textContent = n;
            b.onclick = () => load(_view, n);
            box.appendChild(b);
        });

        if (p.has_next) {
            const next = document.createElement('button');
            next.className = 'page-num-btn page-arrow';
            next.innerHTML = '&#8250;';
            next.title = 'Página siguiente';
            next.onclick = () => load(_view, cur + 1);
            box.appendChild(next);
        }

        // Con fast=1 el backend puede no saber el total exacto; en ese caso
        // enseñamos el rango de la página en vez de inventar un total.
        const info = $('homeRegistrosInfo');
        if (info) {
            const from = (cur - 1) * p.page_size + 1;
            const to = (cur - 1) * p.page_size + (p.returned || 0);
            info.textContent = p.exact && p.total !== null && p.total !== undefined
                ? `${p.total} registro${p.total === 1 ? '' : 's'}`
                : (p.returned ? `${from}–${to}` : '');
        }

        bar.style.display = (total > 1 || (p.returned || 0) > 0) ? '' : 'none';
    }

    function updateSortHeaders() {
        document.querySelectorAll('.tabla th[data-sort]').forEach(th => {
            const icon = th.querySelector('.sort-icon');
            if (!icon) return;
            th.classList.remove('sort-asc', 'sort-desc');
            if (th.dataset.sort === _sortBy) {
                th.classList.add(_sortDir === 'asc' ? 'sort-asc' : 'sort-desc');
                icon.className = `sort-icon fas fa-sort-${_sortDir === 'asc' ? 'up' : 'down'}`;
            } else {
                icon.className = 'sort-icon fas fa-sort';
            }
        });
    }

    // ---- Render ----------------------------------------------------------

    function satChip(score) {
        if (score === 'good') return '<span class="sat-chip sat-chip-good" title="Buena valoración"><i class="fas fa-thumbs-up"></i></span>';
        if (score === 'bad') return '<span class="sat-chip sat-chip-bad" title="Mala valoración"><i class="fas fa-thumbs-down"></i></span>';
        return '<span class="sat-chip-none">—</span>';
    }

    function buildRow(t) {
        const isClosed = (t.status === 'closed' || t.status === 'resolved');
        const row = document.createElement('tr');
        row.dataset.ticketId = t.id;
        row.dataset.status = t.status;
        row.onclick = (e) => {
            if (e.target.closest('.td-checkbox')) return;
            if (_selected.size > 0) return;
            openTicket(t.id, t.subject);
        };

        const cbCell = document.createElement('td');
        cbCell.className = 'td-checkbox';
        cbCell.addEventListener('click', (e) => e.stopPropagation());
        const cb = document.createElement('input');
        cb.type = 'checkbox';
        cb.className = 'ticket-checkbox';
        cb.disabled = isClosed;
        if (!isClosed) {
            cb.dataset.ticketId = t.id;
            cb.addEventListener('change', (e) => {
                e.stopPropagation();
                if (cb.checked) { _selected.add(t.id); row.classList.add('row-selected'); }
                else { _selected.delete(t.id); row.classList.remove('row-selected'); }
                updateActionBar();
                syncSelectAll();
            });
        }
        cbCell.appendChild(cb);

        // Celdas de texto por textContent, no por innerHTML: el asunto y los
        // nombres vienen de datos de usuario.
        const cells = [t.id, '', t.requester, t.updated_at, t.service, t.assignee];
        cells.forEach((val, i) => {
            const td = document.createElement('td');
            if (i !== 1) td.textContent = val == null ? '-' : String(val);
            row.appendChild(td);
        });

        const subjectCell = row.cells[1];
        const dot = document.createElement('span');
        dot.className = 'status-dot status-' + (t.status || '').toLowerCase();
        const span = document.createElement('span');
        span.className = 'subject-text';
        span.textContent = t.subject || '';
        span.dataset.status = t.status || '';
        span.dataset.subject = t.subject || '';
        span.dataset.priority = t.priority || '';
        span.dataset.description = t.description || '';
        span.dataset.lcAuthor = t.last_comment ? t.last_comment.author : '';
        span.dataset.lcDate = t.last_comment ? t.last_comment.date : '';
        span.dataset.lcBody = t.last_comment ? t.last_comment.body : '';
        subjectCell.appendChild(dot);
        subjectCell.appendChild(span);

        const satCell = document.createElement('td');
        satCell.innerHTML = satChip(t.satisfaction);
        row.appendChild(satCell);

        row.insertBefore(cbCell, row.firstChild);
        return row;
    }

    // Con fast=1 el backend evita el COUNT y devuelve exact:false en cuanto hay
    // más de una página. Pedimos el total aparte para que el encabezado enseñe
    // la cifra real en vez de "10+", igual que hace /tickets/.
    async function refreshTotal(view, page, pageSize, seq) {
        try {
            const res = await fetch(
                `/tickets/filter/?view=${encodeURIComponent(view)}&total_only=1&page_size=${pageSize}`
            );
            const json = await res.json();
            if (seq !== _reqSeq) return;   // el usuario ya ha cambiado de vista/página
            const total = (json.pagination || {}).total;
            if (total === null || total === undefined) return;

            const countEl = $('homeTableCount');
            if (countEl) countEl.textContent = total;
            const totalPages = Math.max(1, Math.ceil(total / pageSize));
            renderPagination({
                total: total,
                page: page,
                total_pages: totalPages,
                page_size: pageSize,
                has_next: page < totalPages,
                exact: true,
                returned: document.querySelectorAll('#homeTicketsTableBody tr[data-ticket-id]').length,
            });
        } catch (e) {
            /* el total es cosmético: si falla, se queda el aproximado */
        }
    }

    async function load(view, page) {
        _view = view || DEFAULT_VIEW;
        _page = page || 1;
        const seq = ++_reqSeq;

        const spinner = $('homeLoadingSpinner');
        const tbody = $('homeTicketsTableBody');
        if (spinner) spinner.style.display = 'flex';
        clearSelection();

        try {
            let url = `/tickets/filter/?view=${encodeURIComponent(_view)}` +
                `&page=${_page}&page_size=${_pageSize}&fast=1`;
            if (_sortBy) url += `&sort_by=${_sortBy}&sort_dir=${_sortDir}`;

            const res = await fetch(url);
            const json = await res.json();
            if (seq !== _reqSeq) return;   // llegó tarde: hay otra carga en curso

            if (!res.ok) {
                tbody.innerHTML = emptyRow(json.detail || 'Error al cargar tickets.', 'var(--color-danger)');
                return;
            }

            const tickets = json.tickets || [];
            if (!tickets.length) {
                tbody.innerHTML = emptyRow('No hay tickets.');
            } else {
                const frag = document.createDocumentFragment();
                tickets.forEach(t => frag.appendChild(buildRow(t)));
                tbody.innerHTML = '';
                tbody.appendChild(frag);
            }

            const p = json.pagination || {};
            renderPagination(p);
            updateSortHeaders();
            fitTableHeight();

            const countEl = $('homeTableCount');
            if (countEl) {
                countEl.textContent = (p.exact && p.total !== null && p.total !== undefined)
                    ? p.total
                    : `${p.returned || 0}+`;
            }
            if (!p.exact) refreshTotal(_view, _page, _pageSize, seq);
            const titleEl = $('homeTableTitle');
            if (titleEl) titleEl.textContent = VIEW_TITLES[_view] || VIEW_TITLES[DEFAULT_VIEW];
            const clearBtn = $('homeClearFilter');
            if (clearBtn) clearBtn.style.display = (_view === DEFAULT_VIEW) ? 'none' : '';
        } catch (e) {
            console.error('Error cargando la tabla de la home:', e);
            if (seq === _reqSeq) {
                tbody.innerHTML = emptyRow('Error de red al cargar tickets.', 'var(--color-danger)');
            }
        } finally {
            if (spinner && seq === _reqSeq) spinner.style.display = 'none';
        }
    }

    // ---- Modal de fusión --------------------------------------------------

    function openMergeModal() {
        const ov = $('homeBulkMergeOverlay');
        if (!ov) return;
        ov.classList.add('open');
        $('homeBmmSearch').value = '';
        $('homeBmmResults').innerHTML = '';
        $('homeBmmSelectedTarget').classList.remove('show');
        const confirm = $('homeBmmConfirm');
        confirm.disabled = true;
        delete confirm.dataset.targetId;
        setTimeout(() => $('homeBmmSearch').focus(), 50);
    }

    function closeMergeModal() {
        const ov = $('homeBulkMergeOverlay');
        if (ov) ov.classList.remove('open');
    }

    function selectMergeTarget(id, subject, li) {
        document.querySelectorAll('#homeBmmResults li').forEach(el => el.classList.remove('selected'));
        li.classList.add('selected');
        const target = $('homeBmmSelectedTarget');
        target.textContent = `Destino: #${id} — ${subject}`;
        target.classList.add('show');
        const confirm = $('homeBmmConfirm');
        confirm.disabled = false;
        confirm.dataset.targetId = id;
    }

    async function searchMergeTarget(q) {
        const list = $('homeBmmResults');
        if (!list) return;
        if (!q) { list.innerHTML = ''; return; }
        list.innerHTML = '<li style="color:var(--color-text-disabled);cursor:default;">Buscando...</li>';
        try {
            const res = await fetch(`/search/?q=${encodeURIComponent(q)}`);
            const json = await res.json();
            const tickets = (json.tickets || []).filter(t => !_selected.has(t.id)).slice(0, 15);
            if (!tickets.length) {
                list.innerHTML = '<li style="color:var(--color-text-disabled);cursor:default;">Sin resultados</li>';
                return;
            }
            list.innerHTML = '';
            tickets.forEach(t => {
                const li = document.createElement('li');
                li.dataset.id = t.id;
                const id = document.createElement('span');
                id.className = 'bmm-id';
                id.textContent = '#' + t.id;
                const subj = document.createElement('span');
                subj.className = 'bmm-subject';
                subj.textContent = t.subject || '';
                li.appendChild(id);
                li.appendChild(subj);
                li.addEventListener('click', () => selectMergeTarget(t.id, t.subject, li));
                list.appendChild(li);
            });
        } catch (e) {
            list.innerHTML = '<li style="color:var(--color-danger);cursor:default;">Error al buscar</li>';
        }
    }

    // ---- Vista previa al pasar por el asunto -----------------------------
    // Por delegación: las filas se crean y se destruyen en cada carga.

    function initHoverPreview() {
        const card = $('ticket-preview-card');
        const tbody = $('homeTicketsTableBody');
        if (!card || !tbody) return;
        let timer = null, mx = 0, my = 0;

        function position() {
            const cw = card.offsetWidth, ch = card.offsetHeight, m = 16;
            let x = mx + 14, y = my + 14;
            if (x + cw + m > window.innerWidth) x = mx - cw - 14;
            if (y + ch + m > window.innerHeight) y = my - ch - 14;
            card.style.left = Math.max(0, x) + 'px';
            card.style.top = Math.max(0, y) + 'px';
        }

        function show(span) {
            const s = span.dataset;
            card.querySelector('.tpc-status').textContent = s.status || '';
            card.querySelector('.tpc-status').className = 'tpc-status status-' + (s.status || '').toLowerCase();
            card.querySelector('.tpc-subject').textContent = s.subject || '';
            card.querySelector('.tpc-priority').textContent = s.priority ? '(' + s.priority + ')' : '';
            card.querySelector('.tpc-desc').textContent = s.description || '';
            const hasComment = s.lcBody && s.lcBody.trim();
            card.querySelector('.tpc-comment-section').style.display = hasComment ? '' : 'none';
            if (hasComment) {
                card.querySelector('.tpc-comment-author').textContent = s.lcAuthor || '';
                card.querySelector('.tpc-comment-date').textContent = s.lcDate || '';
                card.querySelector('.tpc-comment-body').textContent = s.lcBody || '';
            }
            card.style.display = 'block';
            position();
        }

        document.addEventListener('mousemove', (e) => {
            mx = e.clientX; my = e.clientY;
            if (card.style.display === 'block') position();
        });

        tbody.addEventListener('mouseover', (e) => {
            const span = e.target.closest('.subject-text');
            if (!span) return;
            clearTimeout(timer);
            timer = setTimeout(() => show(span), 250);
        });
        tbody.addEventListener('mouseout', (e) => {
            if (!e.target.closest('.subject-text')) return;
            clearTimeout(timer);
            card.style.display = 'none';
        });
    }

    // ---- Arranque ---------------------------------------------------------

    function init() {
        // Botones de estadísticas: cada uno es una vista de servidor. Volver a
        // pulsar el que ya está activo quita el filtro.
        document.querySelectorAll('.stat[data-view]').forEach(stat => {
            stat.setAttribute('role', 'button');
            stat.setAttribute('tabindex', '0');
            stat.addEventListener('click', () => {
                const target = stat.dataset.view;
                const isActive = stat.classList.contains('is-active');
                document.querySelectorAll('.stat').forEach(s => s.classList.remove('is-active'));
                if (isActive) {
                    load(DEFAULT_VIEW, 1);
                } else {
                    stat.classList.add('is-active');
                    load(target, 1);
                }
            });
            stat.addEventListener('keydown', (event) => {
                if (event.key === 'Enter' || event.key === ' ') {
                    event.preventDefault();
                    stat.click();
                }
            });
        });

        const clearBtn = $('homeClearFilter');
        if (clearBtn) clearBtn.addEventListener('click', () => {
            document.querySelectorAll('.stat').forEach(s => s.classList.remove('is-active'));
            load(DEFAULT_VIEW, 1);
        });

        // Orden por columna. Cambiar de orden vuelve a la página 1 pero
        // conserva el filtro activo.
        document.querySelectorAll('.tabla th[data-sort]').forEach(th => {
            th.addEventListener('click', () => {
                const field = th.dataset.sort;
                if (_sortBy === field) {
                    _sortDir = _sortDir === 'asc' ? 'desc' : 'asc';
                } else {
                    _sortBy = field;
                    _sortDir = 'asc';
                }
                load(_view, 1);
            });
        });

        const sizeSel = $('homePageSizeSelect');
        if (sizeSel) sizeSel.addEventListener('change', () => {
            _pageSize = parseInt(sizeSel.value, 10) || 50;
            load(_view, 1);
        });

        const selectAll = $('homeSelectAll');
        if (selectAll) selectAll.addEventListener('change', function () {
            document.querySelectorAll('#homeTicketsTableBody .ticket-checkbox:not(:disabled)').forEach(cb => {
                cb.checked = selectAll.checked;
                const id = parseInt(cb.dataset.ticketId, 10);
                const row = cb.closest('tr');
                if (selectAll.checked) { _selected.add(id); if (row) row.classList.add('row-selected'); }
                else { _selected.delete(id); if (row) row.classList.remove('row-selected'); }
            });
            updateActionBar();
        });

        const delBtn = $('homeActionDelete');
        if (delBtn) delBtn.addEventListener('click', async () => {
            if (!_selected.size) return;
            const ok = await window.dialog.confirm({
                title: 'Eliminar tickets',
                message: `${_selected.size} ticket(s) serán eliminados. ¿Continuar?`,
                confirmText: 'Eliminar',
                cancelText: 'Cancelar',
                variant: 'danger',
            });
            if (!ok) return;
            try {
                const res = await fetch(BULK_DELETE_URL, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCookie('csrftoken') },
                    body: JSON.stringify({ ids: [..._selected] }),
                });
                const json = await res.json();
                if (json.ok) {
                    window.toast.success(`${json.deleted || 0} ticket(s) eliminado(s)`);
                    load(_view, _page);
                } else {
                    window.toast.error(json.error || 'Error al eliminar');
                }
            } catch (e) { window.toast.error('Error de red'); }
        });

        const mergeBtn = $('homeActionMerge');
        if (mergeBtn) mergeBtn.addEventListener('click', () => {
            if (!_selected.size) return;
            openMergeModal();
        });

        const bmmClose = $('homeBmmClose');
        if (bmmClose) bmmClose.addEventListener('click', closeMergeModal);
        const bmmCancel = $('homeBmmCancel');
        if (bmmCancel) bmmCancel.addEventListener('click', closeMergeModal);
        const overlay = $('homeBulkMergeOverlay');
        if (overlay) overlay.addEventListener('click', (e) => {
            if (e.target === overlay) closeMergeModal();
        });

        let searchTimer = null;
        const bmmSearch = $('homeBmmSearch');
        if (bmmSearch) bmmSearch.addEventListener('input', function () {
            clearTimeout(searchTimer);
            const q = this.value.trim();
            searchTimer = setTimeout(() => searchMergeTarget(q), 300);
        });

        const bmmConfirm = $('homeBmmConfirm');
        if (bmmConfirm) bmmConfirm.addEventListener('click', async () => {
            const targetId = parseInt(bmmConfirm.dataset.targetId || '0', 10);
            if (!targetId) return;
            try {
                const res = await fetch(BULK_MERGE_URL, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCookie('csrftoken') },
                    body: JSON.stringify({ ids: [..._selected], target_id: targetId }),
                });
                const json = await res.json();
                if (json.ok) {
                    window.toast.success(`${json.merged || 0} ticket(s) fusionado(s)`);
                    closeMergeModal();
                    load(_view, _page);
                } else {
                    window.toast.error(json.error || 'Error al fusionar');
                }
            } catch (e) { window.toast.error('Error de red'); }
        });

        // Listener singleton: tabs.js re-ejecuta este script al volver a la
        // pestaña y si no, se irían acumulando handlers de resize.
        if (window.__tfHomeResizeHandler) {
            window.removeEventListener('resize', window.__tfHomeResizeHandler);
        }
        window.__tfHomeResizeHandler = fitTableHeight;
        window.addEventListener('resize', window.__tfHomeResizeHandler);

        initHoverPreview();
        fitTableHeight();
        load(DEFAULT_VIEW, 1);
    }

    init();
})();
