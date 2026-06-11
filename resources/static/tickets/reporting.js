/* ============================================================
   Reporting por entidad — KPIs + Chart.js + export CSV.
   RE-ENTRANTE: tabs.js re-ejecuta los scripts del fragmento al
   volver a la pestaña, así que las instancias Chart viven en
   window.__tfReportCharts y se destruyen antes de recrear.
   ============================================================ */
(function () {
    const $ = (id) => document.getElementById(id);
    if (!$('repKpis')) return; // no estamos en /reporting/

    window.__tfReportCharts = window.__tfReportCharts || {};

    function cssVar(name, fallback) {
        const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
        return v || fallback;
    }

    function palette() {
        return [
            '#5b67ca', '#56b6c2', '#98c379', '#d19a66',
            '#e06c75', '#c678dd', '#61afef', '#e5c07b',
        ];
    }

    function esc(s) {
        return String(s == null ? '' : s)
            .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
    }

    function destroyChart(key) {
        if (window.__tfReportCharts[key]) {
            try { window.__tfReportCharts[key].destroy(); } catch (e) { /* no-op */ }
            delete window.__tfReportCharts[key];
        }
    }

    function makeChart(key, canvasId, config) {
        destroyChart(key);
        const canvas = $(canvasId);
        if (!canvas || !window.Chart) return;
        window.__tfReportCharts[key] = new Chart(canvas.getContext('2d'), config);
    }

    function setDelta(id, cur, prev) {
        const el = $(id);
        if (!el) return;
        const diff = (cur || 0) - (prev || 0);
        if (!prev && !cur) { el.textContent = ''; el.className = 'tf-kpi-delta'; return; }
        const arrow = diff > 0 ? '▲' : (diff < 0 ? '▼' : '–');
        const pct = prev ? Math.round(Math.abs(diff) / prev * 100) : null;
        el.textContent = `${arrow} ${Math.abs(diff)}` + (pct !== null ? ` (${pct}%)` : '');
        el.className = 'tf-kpi-delta ' + (diff > 0 ? 'is-up' : (diff < 0 ? 'is-down' : ''));
        el.title = 'vs periodo anterior';
    }

    function fmtDate(d) {
        const pad = (n) => String(n).padStart(2, '0');
        return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
    }

    function setPreset(days) {
        const to = new Date();
        const from = new Date();
        from.setDate(to.getDate() - days);
        $('repFrom').value = fmtDate(from);
        $('repTo').value = fmtDate(to);
    }

    function params() {
        const p = new URLSearchParams();
        if ($('repBrand').value) p.set('brand', $('repBrand').value);
        if ($('repFrom').value) p.set('from', $('repFrom').value);
        if ($('repTo').value) p.set('to', $('repTo').value);
        return p.toString();
    }

    function doughnut(key, canvasId, dist) {
        const text = cssVar('--color-text', '#333');
        makeChart(key, canvasId, {
            type: 'doughnut',
            data: {
                labels: dist.map(d => d.label),
                datasets: [{ data: dist.map(d => d.n), backgroundColor: palette() }],
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: { legend: { position: 'right', labels: { color: text, boxWidth: 12 } } },
            },
        });
    }

    function bars(key, canvasId, dist) {
        const text = cssVar('--color-text', '#333');
        const grid = cssVar('--color-border', '#ddd');
        makeChart(key, canvasId, {
            type: 'bar',
            data: {
                labels: dist.map(d => d.label),
                datasets: [{ data: dist.map(d => d.n), backgroundColor: palette()[0] }],
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: { legend: { display: false } },
                scales: {
                    x: { ticks: { color: text }, grid: { color: grid } },
                    y: { ticks: { color: text, precision: 0 }, grid: { color: grid }, beginAtZero: true },
                },
            },
        });
    }

    function seriesBars(key, canvasId, labels, values, color) {
        const text = cssVar('--color-text', '#333');
        const grid = cssVar('--color-border', '#ddd');
        makeChart(key, canvasId, {
            type: 'bar',
            data: { labels, datasets: [{ data: values, backgroundColor: color || palette()[1], borderRadius: 4 }] },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: { legend: { display: false } },
                scales: {
                    x: { ticks: { color: text, autoSkip: false, maxRotation: 0 }, grid: { display: false } },
                    y: { ticks: { color: text, precision: 0 }, grid: { color: grid }, beginAtZero: true },
                },
            },
        });
    }

    async function load() {
        const spinner = $('repLoading');
        spinner.style.display = 'flex';
        try {
            const res = await fetch('/reporting/data/?' + params());
            const json = await res.json();
            if (!json.ok) {
                if (window.toast) window.toast.error(json.error || 'Error al cargar reportes');
                return;
            }

            const k = json.kpis;
            $('kpiCreated').textContent = k.created.toLocaleString();
            $('kpiResolved').textContent = k.resolved.toLocaleString();
            $('kpiBacklog').textContent = k.backlog.toLocaleString();
            $('kpiUnassigned').textContent = k.unassigned.toLocaleString();
            $('kpiSla').textContent = k.sla_compliance_pct === null ? '—' : k.sla_compliance_pct + '%';
            $('kpiBreached').textContent = k.sla_breached.toLocaleString();
            $('kpiResolution').textContent = k.avg_resolution_hours === null
                ? '—'
                : (k.avg_resolution_hours >= 48
                    ? Math.round(k.avg_resolution_hours / 24) + ' d'
                    : k.avg_resolution_hours + ' h');
            $('kpiSatisfaction').textContent = k.satisfaction_pct === null ? '—' : k.satisfaction_pct + '%';
            $('kpiSatisfactionLabel').textContent = (k.satisfaction_good + k.satisfaction_bad)
                ? `Satisfacción (${k.satisfaction_good}👍 / ${k.satisfaction_bad}👎)`
                : 'Satisfacción';
            setDelta('kpiCreatedDelta', k.created, k.created_prev);
            setDelta('kpiResolvedDelta', k.resolved, k.resolved_prev);

            // Línea creados vs cerrados: unificar eje de días
            const days = [...new Set([
                ...json.series.created.map(r => r.day),
                ...json.series.closed.map(r => r.day),
            ])].sort();
            const createdMap = Object.fromEntries(json.series.created.map(r => [r.day, r.n]));
            const closedMap = Object.fromEntries(json.series.closed.map(r => [r.day, r.n]));
            const text = cssVar('--color-text', '#333');
            const grid = cssVar('--color-border', '#ddd');
            makeChart('volume', 'chartVolume', {
                type: 'line',
                data: {
                    labels: days,
                    datasets: [
                        { label: 'Creados', data: days.map(d => createdMap[d] || 0),
                          borderColor: '#5b67ca', backgroundColor: 'rgba(91,103,202,0.10)',
                          fill: true, tension: 0.3, pointRadius: days.length > 30 ? 0 : 3,
                          pointBackgroundColor: '#5b67ca', borderWidth: 2 },
                        { label: 'Cerrados', data: days.map(d => closedMap[d] || 0),
                          borderColor: '#56b6c2', backgroundColor: 'rgba(86,182,194,0.08)',
                          fill: true, tension: 0.3, pointRadius: days.length > 30 ? 0 : 3,
                          pointBackgroundColor: '#56b6c2', borderWidth: 2 },
                    ],
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    plugins: { legend: { labels: { color: text } } },
                    scales: {
                        x: { ticks: { color: text, maxTicksLimit: 14 }, grid: { color: grid } },
                        y: { ticks: { color: text, precision: 0 }, grid: { color: grid }, beginAtZero: true },
                    },
                },
            });

            // Carga operativa: por hora (0-23) y por día de la semana (L-D).
            const hours = Array.from({ length: 24 }, (_, h) => String(h).padStart(2, '0'));
            seriesBars('hour', 'chartHour', hours, json.series.by_hour || [], '#98c379');
            const wdLabels = ['Lun', 'Mar', 'Mié', 'Jue', 'Vie', 'Sáb', 'Dom'];
            seriesBars('weekday', 'chartWeekday', wdLabels, json.series.by_weekday || [], '#d19a66');

            doughnut('status', 'chartStatus', json.distributions.status);
            bars('priority', 'chartPriority', json.distributions.priority);
            doughnut('type', 'chartType', json.distributions.type);
            doughnut('channel', 'chartChannel', json.distributions.channel);

            // Tabla: tickets abiertos más antiguos (accionable).
            const oTbody = $('repOldestTbody');
            const oldest = json.oldest_open || [];
            if (!oldest.length) {
                oTbody.innerHTML = '<tr><td colspan="5">Sin tickets abiertos 🎉</td></tr>';
            } else {
                oTbody.innerHTML = oldest.map(t => {
                    const age = t.age_days == null ? '—'
                        : (t.age_days === 0 ? 'hoy' : t.age_days + ' d');
                    const aged = t.age_days != null && t.age_days >= 7 ? ' class="tf-age-old"' : '';
                    return `<tr><td>#${t.id}</td>` +
                        `<td class="tf-cell-subject" title="${esc(t.subject)}">${esc(t.subject)}</td>` +
                        `<td><span class="tf-badge tf-badge--${esc(t.status)}">${esc(t.status)}</span></td>` +
                        `<td>${esc(t.assignee || 'Sin asignar')}</td>` +
                        `<td${aged}>${age}</td></tr>`;
                }).join('');
            }

            const tbody = $('repBrandTbody');
            if (!json.by_brand.length) {
                tbody.innerHTML = '<tr><td colspan="4">Sin datos en el rango</td></tr>';
            } else {
                tbody.innerHTML = json.by_brand.map(r =>
                    `<tr><td>${esc(r.brand)}</td><td>${r.created.toLocaleString()}</td>` +
                    `<td>${r.open.toLocaleString()}</td><td>${r.breached.toLocaleString()}</td></tr>`
                ).join('');
            }
        } catch (e) {
            console.error('Error cargando reporting:', e);
            if (window.toast) window.toast.error('Error de red al cargar reportes');
        } finally {
            spinner.style.display = 'none';
        }
    }

    document.querySelectorAll('.tf-report-presets button').forEach(btn => {
        btn.addEventListener('click', () => {
            document.querySelectorAll('.tf-report-presets button').forEach(b => b.classList.remove('is-active'));
            btn.classList.add('is-active');
            setPreset(parseInt(btn.dataset.days, 10));
            load();
        });
    });
    // Rango personalizado: al editar una fecha se deselecciona el preset y se aplica solo.
    [$('repFrom'), $('repTo')].forEach(inp => inp.addEventListener('change', () => {
        document.querySelectorAll('.tf-report-presets button').forEach(b => b.classList.remove('is-active'));
        load();
    }));
    $('repBrand').addEventListener('change', load);
    $('repExport').addEventListener('click', () => {
        window.location.href = '/reporting/export.csv?' + params();
    });

    setPreset(30);
    load();
})();
