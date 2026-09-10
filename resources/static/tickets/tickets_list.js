/* ============================================================
   Tickets list view — left-column filters + sortable table + bulk
   action bar + merge modal + subject hover preview.
   Loaded from tickets_list.html via {% block extra_js %}.
   Reads runtime data from window.__ticketsData (set by template).
   ============================================================ */
(function () {
    const data = window.__ticketsData || {};
    const CREATE_TICKET_URL = data.createTicketUrl || '/tickets/create/';

    let _currentView = null;
    let _currentPage = 1;
    let _totalPages  = 1;
    let _pageSize    = 50;
    let _sortBy      = '';
    let _sortDir     = 'asc';
    let _brandId     = '';
    let _fromDate    = '';
    let _toDate      = '';
    let _lastExactTotal = null;
    let _selectedIds = new Set();
    let _lastRenderedCount = 0;
    let _totalRequestSeq = 0;

    const COLSPAN = 10;

    const CHANNEL_LABELS = {
        email: 'Email', web: 'Web', phone: 'Teléfono', api: 'API', chat: 'Chat',
        twitter: 'Twitter', twitter_dm: 'Twitter DM', twitter_like: 'Twitter Like', internal: 'Interno',
    };
    function channelLabel(c) { return CHANNEL_LABELS[c] || c || '-'; }

    function slaBadge(sla) {
        if (!sla) return '-';
        if (sla.breached) {
            const title = sla.next_due ? `SLA incumplido · venció ${sla.next_due}` : 'SLA incumplido';
            return `<span class="sla-badge sla-breached" title="${title}">Incumplido</span>`;
        }
        if (sla.at_risk) return `<span class="sla-badge sla-risk" title="Vence ${sla.next_due}">En riesgo</span>`;
        if (sla.next_due) return `<span class="sla-badge sla-ok">${sla.next_due}</span>`;
        return '-';
    }

    function fmtDate(date) {
        const pad = (n) => String(n).padStart(2, '0');
        return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
    }

    function setPreset(days) {
        const to = new Date();
        const from = new Date();
        from.setDate(to.getDate() - days);
        _fromDate = fmtDate(from);
        _toDate = fmtDate(to);
        const fromInput = document.getElementById('ticketsFrom');
        const toInput = document.getElementById('ticketsTo');
        if (fromInput) fromInput.value = _fromDate;
        if (toInput) toInput.value = _toDate;
    }

    function normalizeRange() {
        if (_fromDate && _toDate && _toDate < _fromDate) {
            [_fromDate, _toDate] = [_toDate, _fromDate];
            document.getElementById('ticketsFrom').value = _fromDate;
            document.getElementById('ticketsTo').value = _toDate;
        }
    }

    function filterParams() {
        const p = new URLSearchParams();
        if (_brandId) p.set('brand_id', _brandId);
        if (_fromDate) p.set('from', _fromDate);
        if (_toDate) p.set('to', _toDate);
        const query = p.toString();
        return query ? '&' + query : '';
    }

    function exportParams() {
        const p = new URLSearchParams();
        p.set('view', _currentView || 'mis_tickets');
        if (_brandId) p.set('brand_id', _brandId);
        if (_fromDate) p.set('from', _fromDate);
        if (_toDate) p.set('to', _toDate);
        if (_sortBy) {
            p.set('sort_by', _sortBy);
            p.set('sort_dir', _sortDir);
        }
        return p.toString();
    }

    function updateFilterSummary() {
        const summary = document.getElementById('ticketsFilterSummary');
        if (!summary) return;
        const brand = document.getElementById('brandFilter');
        const brandLabel = brand && brand.value
            ? brand.options[brand.selectedIndex].text
            : 'Todas las empresas';
        const active = document.querySelector('#filters li[data-view].active .filter-name');
        const viewLabel = active ? active.textContent.trim() : 'Todos los tickets';
        const totalLabel = Number.isFinite(_lastExactTotal)
            ? `${_lastExactTotal} ticket${_lastExactTotal === 1 ? '' : 's'}`
            : 'Calculando tickets';
        summary.textContent = `${totalLabel} · ${brandLabel} · ${viewLabel} · ${_fromDate} → ${_toDate}`;
    }

    function getUrlParam(name) {
        return new URLSearchParams(window.location.search).get(name);
    }

    function getPageRange(current, total) {
        if (total <= 7) return Array.from({length: total}, (_, i) => i + 1);
        if (current <= 4) return [1, 2, 3, 4, 5, '...', total];
        if (current >= total - 3) return [1, '...', total-4, total-3, total-2, total-1, total];
        return [1, '...', current-1, current, current+1, '...', total];
    }

    function renderPageNumbers(current, total, options) {
        const container = document.getElementById("pageNumbers");
        container.innerHTML = "";
        const exact = !options || options.exact !== false;
        const hasNext = options && Object.prototype.hasOwnProperty.call(options, 'hasNext')
            ? options.hasNext
            : current < total;

        if (current > 1) {
            const prev = document.createElement("button");
            prev.className = "page-num-btn page-arrow";
            prev.innerHTML = "&#8249;";
            prev.title = "Página anterior";
            prev.onclick = () => loadTickets(_currentView, current - 1);
            container.appendChild(prev);
        }

        if (!exact) {
            if (current > 2) {
                const first = document.createElement("button");
                first.className = "page-num-btn";
                first.textContent = "1";
                first.onclick = () => loadTickets(_currentView, 1);
                container.appendChild(first);

                const span = document.createElement("span");
                span.className = "page-ellipsis";
                span.textContent = "...";
                container.appendChild(span);
            }

            const currentBtn = document.createElement("button");
            currentBtn.className = "page-num-btn active";
            currentBtn.textContent = current;
            container.appendChild(currentBtn);

            const next = document.createElement("button");
            next.className = "page-num-btn page-arrow";
            next.innerHTML = "&#8250;";
            next.title = "Página siguiente";
            next.disabled = !hasNext;
            next.onclick = () => { if (hasNext) loadTickets(_currentView, current + 1); };
            container.appendChild(next);
            return;
        }

        getPageRange(current, total).forEach(p => {
            if (p === '...') {
                const span = document.createElement("span");
                span.className = "page-ellipsis";
                span.textContent = "...";
                container.appendChild(span);
            } else {
                const btn = document.createElement("button");
                btn.className = "page-num-btn" + (p === current ? " active" : "");
                btn.textContent = p;
                btn.onclick = () => loadTickets(_currentView, p);
                container.appendChild(btn);
            }
        });

        const next = document.createElement("button");
        next.className = "page-num-btn page-arrow";
        next.innerHTML = "&#8250;";
        next.title = "Página siguiente";
        next.disabled = current >= total;
        next.onclick = () => { if (current < total) loadTickets(_currentView, current + 1); };
        container.appendChild(next);
    }

    function updatePagination(p) {
        const exact = p.exact !== false && Number.isFinite(p.total);
        const returned = Number.isFinite(p.returned) ? p.returned : _lastRenderedCount;
        _totalPages  = p.total_pages || p.page || 1;
        _currentPage = p.page;
        _pageSize    = p.page_size;
        _lastExactTotal = exact ? p.total : null;

        renderPageNumbers(p.page, _totalPages, { exact, hasNext: !!p.has_next });

        const info = document.getElementById("registrosInfo");
        if (returned <= 0 && exact && p.total === 0) {
            info.textContent = "registros (0)";
        } else {
            const from = returned > 0 ? (p.page - 1) * p.page_size + 1 : 0;
            const to = returned > 0 ? from + returned - 1 : 0;
            info.textContent = exact
                ? `registros (mostrando ${from}–${to} de ${p.total})`
                : `registros (mostrando ${from}–${to}; hay más)`;
        }

        const sel = document.getElementById("pageSizeSelect");
        if (sel) sel.value = p.page_size;

        document.getElementById("paginationBar").style.display = "flex";
        updateFilterSummary();
    }

    function applyExactTotalFromCounts(filtros) {
        if (!_currentView || !filtros) return;
        const total = Number(filtros[_currentView]);
        if (!Number.isFinite(total)) return;

        const lowerBound = (_currentPage - 1) * _pageSize + _lastRenderedCount;
        if (total < lowerBound) return;

        const totalPages = Math.max(1, Math.ceil(total / _pageSize));
        if (_currentPage > totalPages) return;

        updatePagination({
            total,
            page: _currentPage,
            total_pages: totalPages,
            page_size: _pageSize,
            has_next: _currentPage < totalPages,
            exact: true,
            returned: _lastRenderedCount,
        });
    }

    async function refreshCurrentTotal(view, page, pageSize) {
        const seq = ++_totalRequestSeq;
        try {
            const response = await fetch(
                `/tickets/filter/?view=${encodeURIComponent(view)}&total_only=1&page_size=${pageSize}${filterParams()}`
            );
            const json = await response.json();
            const total = Number(json.pagination && json.pagination.total);
            if (seq !== _totalRequestSeq || view !== _currentView || page !== _currentPage || pageSize !== _pageSize) {
                return;
            }
            if (!Number.isFinite(total)) return;

            updatePagination({
                total,
                page,
                total_pages: Math.max(1, Math.ceil(total / pageSize)),
                page_size: pageSize,
                has_next: page < Math.max(1, Math.ceil(total / pageSize)),
                exact: true,
                returned: _lastRenderedCount,
            });
        } catch (err) {
            console.error("Error cargando total de tickets:", err);
        }
    }

    function changePageSize(size) {
        _pageSize = parseInt(size, 10);
        loadTickets(_currentView, 1);
    }
    window.changePageSize = changePageSize;

    function toggleSort(field) {
        if (_sortBy === field) {
            _sortDir = _sortDir === 'asc' ? 'desc' : 'asc';
        } else {
            _sortBy  = field;
            _sortDir = 'asc';
        }
        loadTickets(_currentView, 1);
    }
    window.toggleSort = toggleSort;

    function updateSortHeaders(activeSortBy, activeSortDir) {
        document.querySelectorAll('.tabla th[data-sort]').forEach(th => {
            const field = th.dataset.sort;
            const icon  = th.querySelector('.sort-icon');
            th.classList.remove('sort-asc', 'sort-desc');
            if (field === activeSortBy) {
                th.classList.add(activeSortDir === 'asc' ? 'sort-asc' : 'sort-desc');
                icon.className = `sort-icon fas fa-sort-${activeSortDir === 'asc' ? 'up' : 'down'}`;
            } else {
                icon.className = 'sort-icon fas fa-sort';
            }
        });
    }

    async function refreshFilterCounts(force) {
        const refreshIcon = document.getElementById("refreshIcon");
        try {
            refreshIcon.classList.add("spin-once");
            const forceParam = force === true ? '&force_counts=1' : '';
            const response = await fetch(`/tickets/filter/?counts=1&counts_only=1&page_size=${_pageSize}${forceParam}${filterParams()}`);
            const json = await response.json();
            if (json.filtros) {
                for (const [key, value] of Object.entries(json.filtros)) {
                    const span = document.querySelector(`#filters li[data-view="${key}"] .filter-count`);
                    if (span) span.textContent = value;
                }
                applyExactTotalFromCounts(json.filtros);
            }
        } catch (err) {
            console.error("Error refrescando filtros:", err);
        } finally {
            setTimeout(() => refreshIcon.classList.remove("spin-once"), 1500);
        }
    }

    function emptyRow(colspan, text, color, title) {
        const style = color ? ` style="color: ${color};"` : '';
        return `<tr><td colspan="${colspan}"><div class="tf-empty tf-empty--compact">` +
            `<span class="tf-empty-icon"><i class="fas fa-filter"></i></span>` +
            `<span class="tf-empty-title"${style}>${title || 'Sin tickets en esta vista'}</span>` +
            `<span class="tf-empty-message"${style}>${text}</span></div></td></tr>`;
    }

    async function loadTickets(view, page, updateUrl) {
        if (page === undefined) page = 1;
        if (updateUrl === undefined) updateUrl = true;

        _currentView = view;
        _currentPage = page;
        _totalRequestSeq++;

        const spinner = document.getElementById("loadingSpinner");
        const tbody   = document.getElementById("ticketsTableBody");

        try {
            spinner.style.display = "flex";
            tbody.innerHTML = "";
            clearSelection();

            let url = `/tickets/filter/?view=${encodeURIComponent(view)}&page=${page}&page_size=${_pageSize}&fast=1${filterParams()}`;
            if (_sortBy) url += `&sort_by=${_sortBy}&sort_dir=${_sortDir}`;
            const response = await fetch(url);
            const json = await response.json();
            _lastRenderedCount = Array.isArray(json.tickets) ? json.tickets.length : 0;

            if (!json.tickets || json.tickets.length === 0) {
                tbody.innerHTML = emptyRow(COLSPAN, 'Prueba otra vista, empresa o combinación de filtros.');
            } else {
                const fragment = document.createDocumentFragment();
                const groupBy = json.group_by;
                const groupLabel = {
                    assignee:  'Asignado',
                    status:    'Estado',
                    group:     'Grupo',
                    requester: 'Solicitante',
                }[groupBy] || '';
                let _prevGroup = null;
                json.tickets.forEach(t => {
                    if (groupBy && t.group_value !== _prevGroup) {
                        const headerRow = document.createElement('tr');
                        headerRow.className = 'group-header-row';
                        const headerCell = document.createElement('td');
                        headerCell.colSpan = COLSPAN;
                        headerCell.className = 'group-header';
                        headerCell.textContent = `${groupLabel}: ${t.group_value}`;
                        headerRow.appendChild(headerCell);
                        fragment.appendChild(headerRow);
                        _prevGroup = t.group_value;
                    }
                    const isClosed = (t.status === 'closed');
                    const row = document.createElement("tr");
                    row.dataset.ticketId = t.id;
                    row.dataset.status = t.status;
                    row.onclick = (e) => {
                        if (e.target.closest('.td-checkbox')) return;
                        if (_selectedIds.size > 0) return;
                        openTicket(t.id, t.subject);
                    };

                    const cbCell = document.createElement('td');
                    cbCell.className = 'td-checkbox';
                    const cb = document.createElement('input');
                    cb.type = 'checkbox';
                    cb.className = 'ticket-checkbox';
                    cb.disabled = isClosed;
                    if (!isClosed) {
                        cb.dataset.ticketId = t.id;
                        cb.addEventListener('change', (e) => {
                            e.stopPropagation();
                            if (cb.checked) { _selectedIds.add(t.id); row.classList.add('row-selected'); }
                            else            { _selectedIds.delete(t.id); row.classList.remove('row-selected'); }
                            updateActionBar();
                            syncSelectAll();
                        });
                    }
                    cbCell.addEventListener('click', (e) => e.stopPropagation());
                    cbCell.appendChild(cb);

                    const subjectSpan = document.createElement("span");
                    subjectSpan.className = "subject-text";
                    subjectSpan.textContent = t.subject;
                    subjectSpan.dataset.ticketId = t.id;
                    subjectSpan.dataset.status = t.status;
                    subjectSpan.dataset.priority = t.priority || "";
                    subjectSpan.dataset.subject = t.subject;
                    subjectSpan.dataset.description = t.description || "";
                    subjectSpan.dataset.lcAuthor = t.last_comment ? t.last_comment.author : "";
                    subjectSpan.dataset.lcDate = t.last_comment ? t.last_comment.date : "";
                    subjectSpan.dataset.lcBody = t.last_comment ? t.last_comment.body : "";

                    row.innerHTML = `
                      <td>${t.id}</td>
                      <td><span class="status-dot status-${t.status}"></span></td>
                      <td>${t.requester}</td>
                      <td>${t.updated_at}</td>
                      <td>${t.service}</td>
                      <td>${t.brand || '-'}</td>
                      <td>${channelLabel(t.channel)}</td>
                      <td>${slaBadge(t.sla)}</td>
                      <td>${t.assignee}</td>
                    `;
                    row.insertBefore(cbCell, row.firstChild);
                    row.cells[2].appendChild(subjectSpan);
                    fragment.appendChild(row);
                });
                tbody.appendChild(fragment);
            }

            if (json.pagination) updatePagination(json.pagination);
            if (json.pagination && json.pagination.exact === false) {
                refreshCurrentTotal(view, page, _pageSize);
            }
            if (json.sort) updateSortHeaders(json.sort.sort_by, json.sort.sort_dir);

            if (updateUrl) {
                const u = new URL(window.location);
                u.searchParams.set("view", view);
                u.searchParams.set("page", page);
                u.searchParams.set("page_size", _pageSize);
                if (_sortBy) { u.searchParams.set("sort_by", _sortBy); u.searchParams.set("sort_dir", _sortDir); }
                else { u.searchParams.delete("sort_by"); u.searchParams.delete("sort_dir"); }
                if (_brandId) u.searchParams.set("brand_id", _brandId);
                else u.searchParams.delete("brand_id");
                if (_fromDate) u.searchParams.set("from", _fromDate);
                else u.searchParams.delete("from");
                if (_toDate) u.searchParams.set("to", _toDate);
                else u.searchParams.delete("to");
                window.history.replaceState({}, "", u);
            }
        } catch (err) {
            console.error("Error cargando tickets:", err);
            tbody.innerHTML = emptyRow(COLSPAN, 'Error al cargar tickets.', 'var(--color-danger)');
        } finally {
            spinner.style.display = "none";
        }
    }
    window.loadTickets = loadTickets;

    function init() {
        document.getElementById("refreshFilters").addEventListener("click", () => refreshFilterCounts(true));

        const viewFilters = document.querySelectorAll("#filters li[data-view]");
        const filterSections = Array.from(document.querySelectorAll(
            '#filters .filter-section-label[data-filter-section]'
        ));
        const filtersPanel = document.getElementById('ticketFiltersPanel');
        const filtersToggle = document.getElementById('ticketFiltersToggle');
        const viewTitle = document.getElementById('ticketsViewTitle');
        const filterSearch = document.getElementById('filterViewsSearch');
        const collapsedSectionsStorageKey = 'ticketflow.filterSections.v1';
        const reduceFilterMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
        let filterAnimationSequence = 0;
        let collapsedSections = new Set();

        try {
            const savedSections = JSON.parse(sessionStorage.getItem(collapsedSectionsStorageKey) || '[]');
            if (Array.isArray(savedSections)) collapsedSections = new Set(savedSections);
        } catch (error) {
            console.warn('No se pudo recuperar el estado de los filtros.', error);
        }

        function getSectionItems(section) {
            const items = [];
            let sibling = section.nextElementSibling;
            while (sibling && !sibling.classList.contains('filter-section-label')) {
                if (sibling.dataset.view) items.push(sibling);
                sibling = sibling.nextElementSibling;
            }
            return items;
        }

        function saveCollapsedSections() {
            try {
                sessionStorage.setItem(collapsedSectionsStorageKey, JSON.stringify([...collapsedSections]));
            } catch (error) {
                console.warn('No se pudo guardar el estado de los filtros.', error);
            }
        }

        function stopSectionAnimations(section, items) {
            section.dataset.filterAnimationToken = String(++filterAnimationSequence);
            items.forEach(item => item.getAnimations().forEach(animation => animation.cancel()));
        }

        function animateSectionItems(section, items, isCollapsed) {
            if (reduceFilterMotion || !items.length || typeof items[0].animate !== 'function') {
                stopSectionAnimations(section, items);
                items.forEach(item => { item.hidden = isCollapsed; });
                return;
            }

            stopSectionAnimations(section, items);
            if (!isCollapsed) items.forEach(item => { item.hidden = false; });

            const visibleItems = items.filter(item => !item.hidden);
            const animationOrder = isCollapsed ? [...visibleItems].reverse() : visibleItems;
            const token = String(++filterAnimationSequence);
            section.dataset.filterAnimationToken = token;
            const animations = animationOrder.map((item, index) => {
                const styles = window.getComputedStyle(item);
                const expandedFrame = {
                    boxSizing: 'border-box',
                    height: `${item.getBoundingClientRect().height}px`,
                    marginBottom: styles.marginBottom,
                    paddingTop: styles.paddingTop,
                    paddingBottom: styles.paddingBottom,
                    opacity: '1',
                    overflow: 'hidden',
                    transform: 'translateY(0)',
                };
                const collapsedFrame = {
                    boxSizing: 'border-box',
                    height: '0px',
                    marginBottom: '0px',
                    paddingTop: '0px',
                    paddingBottom: '0px',
                    opacity: '0',
                    overflow: 'hidden',
                    transform: 'translateY(-4px)',
                };
                return item.animate(
                    isCollapsed ? [expandedFrame, collapsedFrame] : [collapsedFrame, expandedFrame],
                    {
                        duration: isCollapsed ? 120 : 170,
                        delay: Math.min(index * 8, 32),
                        easing: isCollapsed ? 'ease-in' : 'cubic-bezier(.2,.8,.2,1)',
                        fill: 'both',
                    }
                );
            });

            Promise.allSettled(animations.map(animation => animation.finished)).then(() => {
                if (section.dataset.filterAnimationToken !== token) return;
                const hasSearch = !!filterSearch?.value.trim();
                const remainsCollapsed = collapsedSections.has(section.dataset.filterSection) && !hasSearch;
                animations.forEach(animation => animation.cancel());
                items.forEach(item => { item.hidden = remainsCollapsed; });
            });
        }

        function renderFilterSections(animatedSectionKey = null) {
            const term = filterSearch?.value.trim().toLocaleLowerCase('es') || '';

            if (!filterSections.length) {
                viewFilters.forEach(item => {
                    const label = item.textContent.toLocaleLowerCase('es');
                    item.hidden = !!term && !label.includes(term);
                });
                return;
            }

            filterSections.forEach(section => {
                const button = section.querySelector('.filter-section-toggle');
                const items = getSectionItems(section);

                if (term) {
                    stopSectionAnimations(section, items);
                    const sectionMatches = button.textContent.toLocaleLowerCase('es').includes(term);
                    let hasVisibleItem = false;
                    items.forEach(item => {
                        const itemMatches = sectionMatches
                            || item.textContent.toLocaleLowerCase('es').includes(term);
                        item.hidden = !itemMatches;
                        hasVisibleItem ||= itemMatches;
                    });
                    section.hidden = !hasVisibleItem;
                    section.classList.remove('is-collapsed');
                    button.setAttribute('aria-expanded', 'true');
                    return;
                }

                const isCollapsed = collapsedSections.has(section.dataset.filterSection);
                section.hidden = false;
                section.classList.toggle('is-collapsed', isCollapsed);
                button.setAttribute('aria-expanded', isCollapsed ? 'false' : 'true');
                if (section.dataset.filterSection === animatedSectionKey) {
                    animateSectionItems(section, items, isCollapsed);
                } else {
                    stopSectionAnimations(section, items);
                    items.forEach(item => { item.hidden = isCollapsed; });
                }
            });
        }

        filterSections.forEach((section, sectionIndex) => {
            const button = section.querySelector('.filter-section-toggle');
            const items = getSectionItems(section);
            const controlledIds = items.map((item, itemIndex) => {
                if (!item.id) item.id = `filter-section-${sectionIndex + 1}-item-${itemIndex + 1}`;
                return item.id;
            });
            button.setAttribute('aria-controls', controlledIds.join(' '));
            button.addEventListener('click', () => {
                const shouldCollapse = button.getAttribute('aria-expanded') === 'true';
                if (filterSearch) filterSearch.value = '';
                if (shouldCollapse) collapsedSections.add(section.dataset.filterSection);
                else collapsedSections.delete(section.dataset.filterSection);
                saveCollapsedSections();
                renderFilterSections(section.dataset.filterSection);
            });
        });

        let activeView = getUrlParam("view");
        let activePage = parseInt(getUrlParam("page") || "1", 10) || 1;
        let savedSize  = parseInt(getUrlParam("page_size") || "50", 10);
        if ([10,20,50,100,150].includes(savedSize)) {
            _pageSize = savedSize;
            const sel = document.getElementById("pageSizeSelect");
            if (sel) sel.value = savedSize;
        }
        const savedSort = getUrlParam("sort_by");
        if (savedSort) { _sortBy = savedSort; _sortDir = getUrlParam("sort_dir") || 'asc'; }

        setPreset(30);
        _fromDate = getUrlParam('from') || _fromDate;
        _toDate = getUrlParam('to') || _toDate;
        normalizeRange();
        document.getElementById('ticketsFrom').value = _fromDate;
        document.getElementById('ticketsTo').value = _toDate;

        const presetButtons = document.querySelectorAll('.tf-ticket-presets button');
        function setActivePreset(days) {
            presetButtons.forEach(button => {
                const active = days !== null && parseInt(button.dataset.days, 10) === days;
                button.classList.toggle('is-active', active);
                button.setAttribute('aria-pressed', active ? 'true' : 'false');
            });
        }
        function syncPresetFromRange() {
            const today = fmtDate(new Date());
            const parse = (value) => {
                const [year, month, day] = value.split('-').map(Number);
                return Date.UTC(year, month - 1, day);
            };
            const days = Math.round((parse(_toDate) - parse(_fromDate)) / 86400000);
            setActivePreset(_toDate === today && [7, 30, 90, 365].includes(days) ? days : null);
        }
        syncPresetFromRange();

        const brandSel = document.getElementById('brandFilter');
        const savedBrand = getUrlParam('brand_id');
        if (brandSel) {
            if (savedBrand) brandSel.value = savedBrand;
            _brandId = brandSel.value || '';
            brandSel.addEventListener('change', () => {
                _brandId = brandSel.value || '';
                _lastExactTotal = null;
                clearSelection();
                loadTickets(_currentView, 1);
                refreshFilterCounts(true);
            });
        }

        presetButtons.forEach(button => {
            button.addEventListener('click', () => {
                const days = parseInt(button.dataset.days, 10);
                setPreset(days);
                setActivePreset(days);
                _lastExactTotal = null;
                clearSelection();
                loadTickets(_currentView, 1);
                refreshFilterCounts(true);
            });
        });

        [document.getElementById('ticketsFrom'), document.getElementById('ticketsTo')].forEach(input => {
            input.addEventListener('change', () => {
                _fromDate = document.getElementById('ticketsFrom').value;
                _toDate = document.getElementById('ticketsTo').value;
                if (!_fromDate || !_toDate) setPreset(30);
                normalizeRange();
                syncPresetFromRange();
                _lastExactTotal = null;
                clearSelection();
                loadTickets(_currentView, 1);
                refreshFilterCounts(true);
            });
        });

        document.getElementById('ticketsExportCsv').addEventListener('click', () => {
            window.location.href = '/tickets/export.csv?' + exportParams();
        });
        document.getElementById('ticketsExportPdf').addEventListener('click', () => {
            window.location.href = '/tickets/export.pdf?' + exportParams();
        });

        if (!activeView && viewFilters.length > 0) activeView = viewFilters[0].dataset.view;

        function setActiveView(filter) {
            viewFilters.forEach(x => {
                x.classList.remove('active');
                x.removeAttribute('aria-current');
            });
            if (!filter) return;
            filter.classList.add('active');
            filter.setAttribute('aria-current', 'true');
            const label = filter.querySelector('.filter-name')?.textContent?.trim();
            if (viewTitle && label) viewTitle.textContent = label;
            updateFilterSummary();
        }

        if (filtersToggle && filtersPanel) {
            filtersToggle.addEventListener('click', () => {
                const open = filtersPanel.classList.toggle('is-open');
                filtersToggle.setAttribute('aria-expanded', open ? 'true' : 'false');
            });
        }

        const toolbarToggle = document.getElementById('ticketsToolbarToggle');
        const toolbar = document.getElementById('ticketsToolbar');
        if (toolbarToggle && toolbar) {
            toolbarToggle.addEventListener('click', () => {
                const open = toolbar.classList.toggle('is-open');
                toolbarToggle.setAttribute('aria-expanded', open ? 'true' : 'false');
            });
        }

        if (filterSearch) {
            filterSearch.addEventListener('input', renderFilterSections);
        }

        renderFilterSections();

        viewFilters.forEach(f => {
            f.addEventListener("click", () => {
                setActiveView(f);
                clearSelection();
                loadTickets(f.dataset.view, 1);
                if (window.matchMedia('(max-width: 768px)').matches && filtersPanel) {
                    filtersPanel.classList.remove('is-open');
                    if (filtersToggle) filtersToggle.setAttribute('aria-expanded', 'false');
                }
            });
            if (f.dataset.view === activeView) setActiveView(f);
        });

        if (activeView) loadTickets(activeView, activePage, true);

        setTimeout(() => refreshFilterCounts(), 600);

        document.getElementById('selectAllCheckbox').addEventListener('change', function() {
            const checkAll = this.checked;
            document.querySelectorAll('.ticket-checkbox:not(:disabled)').forEach(cb => {
                cb.checked = checkAll;
                const id = parseInt(cb.dataset.ticketId, 10);
                const row = cb.closest('tr');
                if (checkAll) { _selectedIds.add(id); row.classList.add('row-selected'); }
                else          { _selectedIds.delete(id); row.classList.remove('row-selected'); }
            });
            updateActionBar();
        });

        const _elActionDelete = document.getElementById('actionDelete');
        if (_elActionDelete) _elActionDelete.addEventListener('click', async () => {
            if (_selectedIds.size === 0) return;
            const ok = await window.dialog.confirm({
                title: 'Eliminar tickets',
                message: `¿Eliminar ${_selectedIds.size} ticket(s)? Esta acción se puede revertir.`,
                confirmText: 'Eliminar',
                cancelText: 'Cancelar',
                variant: 'danger',
            });
            if (!ok) return;
            try {
                const resp = await fetch('/tickets/bulk_delete/', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json', 'X-CSRFToken': getCookie('csrftoken')},
                    body: JSON.stringify({ids: [..._selectedIds]}),
                });
                const json = await resp.json();
                if (json.ok) {
                    window.toast.success(`${json.deleted || 0} ticket(s) eliminado(s)`);
                    clearSelection();
                    loadTickets(_currentView, _currentPage);
                    refreshFilterCounts(true);
                } else {
                    window.toast.error(json.error || 'Error al eliminar');
                }
            } catch (e) { window.toast.error('Error de red'); }
        });

        const _elActionMerge = document.getElementById('actionMerge');
        if (_elActionMerge) _elActionMerge.addEventListener('click', () => {
            if (_selectedIds.size === 0) return;
            openBulkMergeModal();
        });

        const _elBmmClose = document.getElementById('bmmClose');
        if (_elBmmClose) _elBmmClose.addEventListener('click', closeBulkMergeModal);
        const _elBmmCancel = document.getElementById('bmmCancel');
        if (_elBmmCancel) _elBmmCancel.addEventListener('click', closeBulkMergeModal);
        const _elBmmOverlay = document.getElementById('bulkMergeOverlay');
        if (_elBmmOverlay) _elBmmOverlay.addEventListener('click', (e) => {
            if (e.target === _elBmmOverlay) closeBulkMergeModal();
        });

        let _bmmSearchTimer = null;
        const _elBmmSearch = document.getElementById('bmmSearch');
        if (_elBmmSearch) _elBmmSearch.addEventListener('input', function() {
            clearTimeout(_bmmSearchTimer);
            _bmmSearchTimer = setTimeout(() => bmmSearch(this.value.trim()), 300);
        });

        const _elBmmConfirm = document.getElementById('bmmConfirm');
        if (_elBmmConfirm) _elBmmConfirm.addEventListener('click', async () => {
            const targetId = parseInt(_elBmmConfirm.dataset.targetId || '0', 10);
            if (!targetId) return;
            try {
                const resp = await fetch('/tickets/bulk_merge/', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json', 'X-CSRFToken': getCookie('csrftoken')},
                    body: JSON.stringify({ids: [..._selectedIds], target_id: targetId}),
                });
                const json = await resp.json();
                if (json.ok) {
                    window.toast.success(`${json.merged || 0} ticket(s) fusionado(s)`);
                    closeBulkMergeModal();
                    clearSelection();
                    loadTickets(_currentView, _currentPage);
                    refreshFilterCounts(true);
                } else {
                    window.toast.error(json.error || 'Error al fusionar');
                }
            } catch (e) { window.toast.error('Error de red'); }
        });
    }

    function updateActionBar() {
        const count = _selectedIds.size;
        const countEl = document.getElementById('tabActionCount');
        if (countEl) countEl.textContent = count;
        const bar = document.getElementById('ticketActionBar');
        if (bar) {
            if (count > 0) bar.classList.add('visible');
            else           bar.classList.remove('visible');
        }
    }

    function syncSelectAll() {
        const all     = document.querySelectorAll('.ticket-checkbox:not(:disabled)');
        const checked = document.querySelectorAll('.ticket-checkbox:not(:disabled):checked');
        const sa = document.getElementById('selectAllCheckbox');
        if (!sa) return;
        if (all.length === 0 || checked.length === 0) {
            sa.checked = false; sa.indeterminate = false;
        } else if (checked.length === all.length) {
            sa.checked = true; sa.indeterminate = false;
        } else {
            sa.checked = false; sa.indeterminate = true;
        }
    }

    function clearSelection() {
        _selectedIds.clear();
        document.querySelectorAll('.ticket-checkbox:checked').forEach(cb => { cb.checked = false; });
        document.querySelectorAll('.row-selected').forEach(r => r.classList.remove('row-selected'));
        const sa = document.getElementById('selectAllCheckbox');
        if (sa) { sa.checked = false; sa.indeterminate = false; }
        updateActionBar();
    }

    function getCookie(name) {
        const v = document.cookie.match('(^|;) ?' + name + '=([^;]*)(;|$)');
        return v ? v[2] : null;
    }

    function openBulkMergeModal() {
        const overlay = document.getElementById('bulkMergeOverlay');
        overlay.classList.add('open');
        document.getElementById('bmmSearch').value = '';
        document.getElementById('bmmResults').innerHTML = '';
        document.getElementById('bmmSelectedTarget').classList.remove('show');
        document.getElementById('bmmConfirm').disabled = true;
        delete document.getElementById('bmmConfirm').dataset.targetId;
        setTimeout(() => document.getElementById('bmmSearch').focus(), 50);
    }

    function closeBulkMergeModal() {
        document.getElementById('bulkMergeOverlay').classList.remove('open');
    }

    async function bmmSearch(q) {
        const list = document.getElementById('bmmResults');
        if (!q) {
            list.innerHTML = '';
            return;
        }
        list.innerHTML = '<li style="color:var(--color-text-disabled);cursor:default;">Buscando...</li>';
        try {
            const resp = await fetch(`/search/?q=${encodeURIComponent(q)}`);
            const json = await resp.json();
            const tickets = (json.tickets || []).filter(t => !_selectedIds.has(t.id)).slice(0, 15);
            if (!tickets.length) {
                list.innerHTML = '<li style="color:var(--color-text-disabled);cursor:default;">Sin resultados</li>';
                return;
            }
            list.innerHTML = '';
            tickets.forEach(t => {
                const li = document.createElement('li');
                li.dataset.id = t.id;
                li.innerHTML = `<span class="bmm-id">#${t.id}</span><span class="bmm-subject">${t.subject}</span>`;
                li.addEventListener('click', () => bmmSelectTarget(t.id, t.subject, li));
                list.appendChild(li);
            });
        } catch(e) {
            list.innerHTML = '<li style="color:var(--color-danger);cursor:default;">Error al buscar</li>';
        }
    }

    function bmmSelectTarget(id, subject, li) {
        document.querySelectorAll('#bmmResults li').forEach(el => el.classList.remove('selected'));
        li.classList.add('selected');
        const target = document.getElementById('bmmSelectedTarget');
        target.textContent = `Destino: #${id} — ${subject}`;
        target.classList.add('show');
        const confirm = document.getElementById('bmmConfirm');
        confirm.disabled = false;
        confirm.dataset.targetId = id;
    }

    function openTicket(ticketId, subject) {
        const tabText = subject || "Ticket " + ticketId;
        const tabUrl = CREATE_TICKET_URL + "?id=" + ticketId;
        if (window.Tabs && typeof window.Tabs.addTab === 'function') {
            window.Tabs.addTab(tabText, tabUrl);
        } else {
            window.location.href = tabUrl;
        }
    }

    // ---- Subject hover preview ----
    const _card = document.getElementById("ticket-preview-card");
    let _hoverTimer = null;

    function _showCard(span, e) {
        const s = span.dataset;
        _card.querySelector(".tpc-status").textContent = s.status;
        _card.querySelector(".tpc-status").className = "tpc-status status-" + s.status;
        _card.querySelector(".tpc-subject").textContent = s.subject;
        _card.querySelector(".tpc-priority").textContent = s.priority ? "(" + s.priority + ")" : "";
        _card.querySelector(".tpc-desc").textContent = s.description || "";

        const hasComment = s.lcBody && s.lcBody.trim() !== "";
        _card.querySelector(".tpc-comment-section").style.display = hasComment ? "" : "none";
        if (hasComment) {
            _card.querySelector(".tpc-comment-author").textContent = s.lcAuthor;
            _card.querySelector(".tpc-comment-date").textContent = s.lcDate;
            _card.querySelector(".tpc-comment-body").textContent = s.lcBody;
        }

        _card.style.display = "block";
        _positionCard(e);
    }

    function _positionCard(e) {
        const margin = 16;
        const cw = _card.offsetWidth;
        const ch = _card.offsetHeight;
        let x = e.clientX + 14;
        let y = e.clientY + 14;
        if (x + cw + margin > window.innerWidth)  x = e.clientX - cw - 14;
        if (y + ch + margin > window.innerHeight) y = e.clientY - ch - 14;
        _card.style.left = Math.max(0, x) + "px";
        _card.style.top  = Math.max(0, y) + "px";
    }

    document.addEventListener("mouseover", function(e) {
        const span = e.target.closest(".subject-text");
        if (!span) return;
        clearTimeout(_hoverTimer);
        _hoverTimer = setTimeout(() => _showCard(span, e), 250);
    });

    document.addEventListener("mouseout", function(e) {
        const span = e.target.closest(".subject-text");
        if (!span) return;
        clearTimeout(_hoverTimer);
        _card.style.display = "none";
    });

    document.addEventListener("mousemove", function(e) {
        if (_card.style.display === "block") _positionCard(e);
    });

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
