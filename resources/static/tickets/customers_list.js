/* ============================================================
   Customers list view — sidebar filters + toolbar + sortable table.
   Loaded from customers_list.html via {% block extra_js %}.
   Reads runtime data from window.__customersData (set by template).
   ============================================================ */
(function () {
    const data = window.__customersData || {};
    const CUSTOMER_PROFILE_URL = data.customerProfileUrl || '/customer-profile/';
    const INITIAL_VIEW = data.initialView || 'all';
    const VIEW_LABELS = {
        all: 'Todos los clientes',
        suspended: 'Usuarios suspendidos',
    };

    let _currentView = null;
    let _currentPage = 1;
    let _totalPages  = 1;
    let _pageSize    = 50;
    let _sortBy      = '';
    let _sortDir     = 'asc';

    function getUrlParam(name) {
        return new URLSearchParams(window.location.search).get(name);
    }

    function updateViewHeading(view) {
        const label = VIEW_LABELS[view] || 'Clientes';
        const heading = document.getElementById('customersViewTitle');
        if (heading) heading.textContent = label;
        document.title = `TicketFlow – ${label}`;
    }

    function getPageRange(current, total) {
        if (total <= 7) return Array.from({length: total}, (_, i) => i + 1);
        if (current <= 4) return [1, 2, 3, 4, 5, '...', total];
        if (current >= total - 3) return [1, '...', total-4, total-3, total-2, total-1, total];
        return [1, '...', current-1, current, current+1, '...', total];
    }

    function renderPageNumbers(current, total) {
        const container = document.getElementById("pageNumbers");
        container.innerHTML = "";

        if (current > 1) {
            const prev = document.createElement("button");
            prev.className = "page-num-btn page-arrow";
            prev.innerHTML = "&#8249;";
            prev.title = "Página anterior";
            prev.onclick = () => loadCustomers(_currentView, current - 1);
            container.appendChild(prev);
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
                btn.onclick = () => loadCustomers(_currentView, p);
                container.appendChild(btn);
            }
        });

        const next = document.createElement("button");
        next.className = "page-num-btn page-arrow";
        next.innerHTML = "&#8250;";
        next.title = "Página siguiente";
        next.disabled = current >= total;
        next.onclick = () => { if (current < total) loadCustomers(_currentView, current + 1); };
        container.appendChild(next);
    }

    function updatePagination(p) {
        _totalPages  = p.total_pages;
        _currentPage = p.page;
        _pageSize    = p.page_size;

        renderPageNumbers(p.page, p.total_pages);

        const from = p.total ? (p.page - 1) * p.page_size + 1 : 0;
        const to   = Math.min(p.page * p.page_size, p.total);
        document.getElementById("registrosInfo").textContent =
            `registros (mostrando ${from}–${to} de ${p.total})`;

        const sel = document.getElementById("pageSizeSelect");
        if (sel) sel.value = p.page_size;

        document.getElementById("paginationBar").style.display = "flex";
    }

    function changePageSize(size) {
        _pageSize = parseInt(size, 10);
        loadCustomers(_currentView, 1);
    }
    window.changePageSize = changePageSize;

    function toggleSort(field) {
        if (_sortBy === field) {
            _sortDir = _sortDir === 'asc' ? 'desc' : 'asc';
        } else {
            _sortBy  = field;
            _sortDir = 'asc';
        }
        loadCustomers(_currentView, 1);
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

    async function loadCustomers(view, page, updateUrl) {
        if (page === undefined) page = 1;
        if (updateUrl === undefined) updateUrl = true;

        _currentView = view;
        _currentPage = page;
        updateViewHeading(view);

        const spinner = document.getElementById("loadingSpinner");
        const tbody   = document.getElementById("customersTableBody");

        try {
            spinner.style.display = "flex";
            tbody.innerHTML = "";

            const q       = (document.getElementById('customersSearch')?.value || '').trim();
            const roleId  = document.getElementById('customersRoleFilter')?.value || '';
            const groupId = document.getElementById('customersGroupFilter')?.value || '';

            let fetchUrl = `/customers/filter/?view=${encodeURIComponent(view)}&page=${page}&page_size=${_pageSize}`;
            if (_sortBy) fetchUrl += `&sort_by=${_sortBy}&sort_dir=${_sortDir}`;
            if (q)       fetchUrl += `&q=${encodeURIComponent(q)}`;
            if (roleId)  fetchUrl += `&role_id=${encodeURIComponent(roleId)}`;
            if (groupId) fetchUrl += `&group_id=${encodeURIComponent(groupId)}`;
            const response = await fetch(fetchUrl);
            const json = await response.json();

            if (!json.customers || json.customers.length === 0) {
                tbody.innerHTML = `<tr><td colspan="8"><div class="tf-empty tf-empty--compact">` +
                    `<span class="tf-empty-icon"><i class="fas fa-user-search"></i></span>` +
                    `<span class="tf-empty-title">Sin clientes</span>` +
                    `<span class="tf-empty-message">Prueba otra búsqueda o cambia los filtros.</span>` +
                    `</div></td></tr>`;
            } else {
                const fragment = document.createDocumentFragment();
                json.customers.forEach(u => {
                    const row = document.createElement("tr");
                    row.onclick = () => openCustomer(u.id, u.name);
                    row.tabIndex = 0;
                    row.setAttribute('aria-label', `Abrir perfil de ${u.name}`);
                    row.addEventListener('keydown', (event) => {
                        if (event.key === 'Enter' || event.key === ' ') {
                            event.preventDefault();
                            openCustomer(u.id, u.name);
                        }
                    });
                    row.innerHTML = `
                        <td>${u.id}</td>
                        <td>${u.name}</td>
                        <td>${u.email}</td>
                        <td>${u.status}</td>
                        <td>${u.role}</td>
                        <td>${u.group}</td>
                        <td>${u.created_at}</td>
                        <td class="customer-open-cell" aria-hidden="true"><i class="fas fa-chevron-right"></i></td>
                    `;
                    fragment.appendChild(row);
                });
                tbody.appendChild(fragment);
            }

            if (json.filtros) {
                for (const [key, value] of Object.entries(json.filtros)) {
                    const span = document.querySelector(`#customerFilters li[data-view="${key}"] .filter-count`);
                    if (span) span.textContent = value;
                }
            }

            if (json.pagination) updatePagination(json.pagination);
            if (json.sort) updateSortHeaders(json.sort.sort_by, json.sort.sort_dir);

            if (updateUrl) {
                const u = new URL(window.location);
                u.searchParams.set("view", view);
                u.searchParams.set("page", page);
                u.searchParams.set("page_size", _pageSize);
                if (_sortBy) { u.searchParams.set("sort_by", _sortBy); u.searchParams.set("sort_dir", _sortDir); }
                else { u.searchParams.delete("sort_by"); u.searchParams.delete("sort_dir"); }
                if (q)       u.searchParams.set("q", q);              else u.searchParams.delete("q");
                if (roleId)  u.searchParams.set("role_id", roleId);   else u.searchParams.delete("role_id");
                if (groupId) u.searchParams.set("group_id", groupId); else u.searchParams.delete("group_id");
                window.history.replaceState({}, "", u);
            }
        } catch (err) {
            console.error("Error cargando usuarios:", err);
            tbody.innerHTML = `<tr><td colspan="8"><div class="tf-empty tf-empty--compact"><span class="tf-empty-title" style="color: var(--color-danger);">No se pudieron cargar los clientes</span><span class="tf-empty-message">Vuelve a intentarlo.</span></div></td></tr>`;
        } finally {
            spinner.style.display = "none";
        }
    }
    window.loadCustomers = loadCustomers;

    function openCustomer(customerId, customerName) {
        const tabText = customerName || ("Cliente " + customerId);
        const tabUrl = CUSTOMER_PROFILE_URL + "?id=" + customerId;
        if (window.Tabs && typeof window.Tabs.addTab === 'function') {
            window.Tabs.addTab(tabText, tabUrl);
        } else {
            window.location.href = tabUrl;
        }
    }

    function init() {
        const filters = document.querySelectorAll("#customerFilters li");
        const filtersPanel = document.getElementById('customerFiltersPanel');
        const filtersToggle = document.getElementById('customerFiltersToggle');
        let activeView = getUrlParam("view") || INITIAL_VIEW;
        let activePage = parseInt(getUrlParam("page") || "1", 10) || 1;
        let savedSize  = parseInt(getUrlParam("page_size") || "50", 10);
        if ([10,20,50,100,150].includes(savedSize)) {
            _pageSize = savedSize;
            const sel = document.getElementById("pageSizeSelect");
            if (sel) sel.value = savedSize;
        }

        const savedSort = getUrlParam("sort_by");
        if (savedSort) { _sortBy = savedSort; _sortDir = getUrlParam("sort_dir") || 'asc'; }

        const availableViews = new Set(Array.from(filters, filter => filter.dataset.view));
        if (!availableViews.has(activeView) && filters.length > 0) activeView = filters[0].dataset.view;

        if (filtersToggle && filtersPanel) {
            filtersToggle.addEventListener('click', () => {
                const open = filtersPanel.classList.toggle('is-open');
                filtersToggle.setAttribute('aria-expanded', open ? 'true' : 'false');
            });
        }

        filters.forEach(f => {
            const isActive = f.dataset.view === activeView;
            f.classList.toggle('active', isActive);
            if (isActive) f.setAttribute('aria-current', 'true');
            else f.removeAttribute('aria-current');
            f.addEventListener("click", () => {
                filters.forEach(x => {
                    x.classList.remove("active");
                    x.removeAttribute('aria-current');
                });
                f.classList.add("active");
                f.setAttribute('aria-current', 'true');
                loadCustomers(f.dataset.view, 1);
                if (window.matchMedia('(max-width: 768px)').matches && filtersPanel) {
                    filtersPanel.classList.remove('is-open');
                    if (filtersToggle) filtersToggle.setAttribute('aria-expanded', 'false');
                }
            });
        });

        // Wire search/selects via shared helper — restores from URL + debounces.
        if (window.TF && TF.bindFilters) {
            TF.bindFilters({
                searchEl: '#customersSearch',
                selectEls: ['#customersRoleFilter', '#customersGroupFilter'],
                urlKeys: { search: 'q', selects: ['role_id', 'group_id'] },
                onChange: () => loadCustomers(_currentView, 1),
            });
        }

        if (activeView) loadCustomers(activeView, activePage, false);
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
