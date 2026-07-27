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

    // % de valoraciones positivas a partir del cual la cara sale contenta.
    const SATISFACTION_HAPPY_MIN = 80;

    // Último payload de /reporting/data/, para que el export PDF use exactamente
    // los mismos números que hay pintados en pantalla sin repetir la petición.
    let lastData = null;

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

    // Cara de la tarjeta de satisfacción: contenta a partir del umbral, triste por
    // debajo, neutra cuando aún no hay valoraciones (satisfaction_pct === null).
    // Reutiliza los modificadores de tarjeta ya existentes para el color.
    function setSatisfactionFace(pct) {
        const card = $('kpiSatisfactionCard');
        const ico = $('kpiSatisfactionIco');
        if (!card || !ico) return;
        card.classList.remove('tf-kpi--success', 'tf-kpi--danger');
        if (pct === null || pct === undefined) {
            ico.className = 'fas fa-meh';
            ico.title = 'Sin valoraciones en el periodo';
            return;
        }
        const happy = pct >= SATISFACTION_HAPPY_MIN;
        card.classList.add(happy ? 'tf-kpi--success' : 'tf-kpi--danger');
        ico.className = 'fas ' + (happy ? 'fa-smile' : 'fa-frown');
        ico.title = happy
            ? `Satisfacción ${pct}% (umbral ${SATISFACTION_HAPPY_MIN}%)`
            : `Satisfacción ${pct}%, por debajo del umbral del ${SATISFACTION_HAPPY_MIN}%`;
    }

    function fmtDate(d) {
        const pad = (n) => String(n).padStart(2, '0');
        return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
    }

    /* ========================================================================
       Exportar a PDF (cliente, jsPDF)

       Se construye con lastData — el mismo JSON que pinta la página — y con los
       <canvas> ya renderizados: las gráficas salen exactamente como han quedado
       tras aplicar los filtros, sin repetir la petición ni re-dibujar nada.

       Los canvas de Chart.js son transparentes y su texto toma el color del
       tema, así que cada imagen se compone antes sobre --color-surface. En modo
       oscuro eso hace que los paneles del PDF salgan oscuros, igual que en
       pantalla, pero siempre legibles.
       ======================================================================== */

    function fmtHoursPdf(h) {
        if (h === null || h === undefined) return '-';
        return h >= 48 ? Math.round(h / 24) + ' d' : h + ' h';
    }

    function filterSummary() {
        const sel = $('repBrand');
        const brand = sel.value ? sel.options[sel.selectedIndex].text : 'Todas las empresas';
        const from = $('repFrom').value || '(inicio)';
        const to = $('repTo').value || '(hoy)';
        return `${brand}   |   ${from} a ${to}`;
    }

    // Cada panel con gráfica -> {title, dataUrl, ratio, wide}. Compone sobre el
    // color de superficie para que el PNG no salga con fondo transparente.
    function chartImages() {
        const surface = cssVar('--color-surface', '#ffffff');
        const out = [];
        document.querySelectorAll('.tf-report-panel').forEach(panel => {
            const cv = panel.querySelector('canvas');
            if (!cv || !cv.width || !cv.height) return;
            const tmp = document.createElement('canvas');
            tmp.width = cv.width;
            tmp.height = cv.height;
            const ctx = tmp.getContext('2d');
            ctx.fillStyle = surface;
            ctx.fillRect(0, 0, tmp.width, tmp.height);
            ctx.drawImage(cv, 0, 0);
            const h3 = panel.querySelector('h3');
            out.push({
                title: h3 ? h3.textContent.trim() : '',
                dataUrl: tmp.toDataURL('image/png'),
                ratio: tmp.height / tmp.width,
                wide: panel.classList.contains('tf-report-panel--wide'),
            });
        });
        return out;
    }

    function exportPdf() {
        if (!window.jspdf || !window.jspdf.jsPDF) {
            if (window.toast) window.toast.error('No se ha cargado la librería de PDF');
            return;
        }
        if (!lastData) {
            if (window.toast) window.toast.error('Espera a que terminen de cargar los datos');
            return;
        }

        const { jsPDF } = window.jspdf;
        const doc = new jsPDF({ unit: 'pt', format: 'a4' });
        const PW = doc.internal.pageSize.getWidth();
        const PH = doc.internal.pageSize.getHeight();
        const M = 40;                 // margen
        const CW = PW - M * 2;        // ancho útil
        const MUTED = [100, 116, 139];
        const TEXT = [30, 41, 59];
        const LINE = [226, 232, 240];
        let y = M;

        // Salto de página cuando no caben `space` puntos.
        function ensure(space) {
            if (y + space <= PH - M) return;
            doc.addPage();
            y = M;
        }

        function heading(text, size) {
            ensure(size + 14);
            doc.setFont('helvetica', 'bold').setFontSize(size).setTextColor(...TEXT);
            doc.text(text, M, y);
            y += size + 6;
        }

        // --- Cabecera -------------------------------------------------------
        doc.setFont('helvetica', 'bold').setFontSize(18).setTextColor(...TEXT);
        doc.text('Reportes por entidad', M, y);
        y += 20;
        doc.setFont('helvetica', 'normal').setFontSize(10).setTextColor(...MUTED);
        doc.text(filterSummary(), M, y);
        y += 13;
        const now = new Date();
        const pad = (n) => String(n).padStart(2, '0');
        doc.text(
            `Generado el ${fmtDate(now)} a las ${pad(now.getHours())}:${pad(now.getMinutes())}`,
            M, y
        );
        y += 14;
        doc.setDrawColor(...LINE).line(M, y, PW - M, y);
        y += 20;

        // --- KPIs en rejilla 4x2 --------------------------------------------
        const k = lastData.kpis;
        const sat = k.satisfaction_pct === null ? '-' : k.satisfaction_pct + '%';
        const satNote = (k.satisfaction_good + k.satisfaction_bad)
            ? `${k.satisfaction_good} buenas / ${k.satisfaction_bad} malas`
            : 'sin valoraciones';
        const kpis = [
            ['Creados', k.created.toLocaleString(), ''],
            ['Resueltos', k.resolved.toLocaleString(), ''],
            ['Backlog abierto', k.backlog.toLocaleString(), ''],
            ['Sin asignar', k.unassigned.toLocaleString(), ''],
            ['Cumplimiento SLA', k.sla_compliance_pct === null ? '-' : k.sla_compliance_pct + '%', ''],
            ['SLA incumplidos', k.sla_breached.toLocaleString(), ''],
            ['T. medio resolución', fmtHoursPdf(k.avg_resolution_hours), ''],
            ['Satisfacción', sat, satNote],
        ];
        const cols = 4;
        const cellW = CW / cols;
        const cellH = 52;
        ensure(cellH * Math.ceil(kpis.length / cols) + 10);
        kpis.forEach((kpi, i) => {
            const cx = M + (i % cols) * cellW;
            const cy = y + Math.floor(i / cols) * cellH;
            doc.setFont('helvetica', 'bold').setFontSize(16).setTextColor(...TEXT);
            doc.text(String(kpi[1]), cx, cy + 18);
            doc.setFont('helvetica', 'normal').setFontSize(8).setTextColor(...MUTED);
            doc.text(kpi[0], cx, cy + 31);
            if (kpi[2]) doc.text(kpi[2], cx, cy + 41);
        });
        y += cellH * Math.ceil(kpis.length / cols) + 8;

        // --- Gráficas --------------------------------------------------------
        // Las anchas ocupan la fila entera; el resto van a dos columnas, igual
        // que en pantalla.
        const GAP = 14;
        const halfW = (CW - GAP) / 2;
        const imgs = chartImages();
        let col = 0;      // 0 = izquierda, 1 = derecha
        let rowH = 0;     // alto de la fila en curso

        function closeRow() {
            if (rowH) { y += rowH + GAP; rowH = 0; }
            col = 0;
        }

        imgs.forEach(img => {
            const w = img.wide ? CW : halfW;
            const h = Math.min(w * img.ratio, 240);
            const block = h + 16;
            if (img.wide) closeRow();
            if (col === 0) ensure(block);
            const x = M + (img.wide ? 0 : col * (halfW + GAP));
            const top = y;
            doc.setFont('helvetica', 'bold').setFontSize(9).setTextColor(...TEXT);
            doc.text(img.title, x, top + 9);
            doc.addImage(img.dataUrl, 'PNG', x, top + 14, w, h, undefined, 'FAST');
            rowH = Math.max(rowH, block);
            if (img.wide) { closeRow(); } else if (col === 1) { closeRow(); } else { col = 1; }
        });
        closeRow();

        // --- Tablas -----------------------------------------------------------
        function table(headers, widths, rows, emptyMsg) {
            const ROW_H = 15;
            function headerRow() {
                doc.setFillColor(248, 250, 252).rect(M, y, CW, ROW_H, 'F');
                doc.setFont('helvetica', 'bold').setFontSize(8).setTextColor(...TEXT);
                let x = M + 4;
                headers.forEach((hd, i) => { doc.text(hd, x, y + 10); x += widths[i]; });
                y += ROW_H;
            }
            ensure(ROW_H * 3);
            headerRow();
            if (!rows.length) {
                doc.setFont('helvetica', 'italic').setFontSize(8).setTextColor(...MUTED);
                doc.text(emptyMsg, M + 4, y + 10);
                y += ROW_H + 8;
                return;
            }
            doc.setFont('helvetica', 'normal').setFontSize(8);
            rows.forEach(r => {
                if (y + ROW_H > PH - M) { doc.addPage(); y = M; headerRow(); doc.setFont('helvetica', 'normal').setFontSize(8); }
                let x = M + 4;
                r.forEach((cell, i) => {
                    doc.setTextColor(...TEXT);
                    // Recorta al ancho de columna para que no se solapen.
                    const txt = doc.splitTextToSize(String(cell), widths[i] - 8)[0] || '';
                    doc.text(txt, x, y + 10);
                    x += widths[i];
                });
                doc.setDrawColor(...LINE).line(M, y + ROW_H, PW - M, y + ROW_H);
                y += ROW_H;
            });
            y += 8;
        }

        heading('Tickets abiertos más antiguos', 11);
        const oldest = lastData.oldest_open || [];
        table(
            ['#', 'Asunto', 'Estado', 'Agente', 'Antigüedad'],
            [40, CW - 40 - 70 - 110 - 65, 70, 110, 65],
            oldest.map(t => [
                '#' + t.id,
                t.subject || '',
                t.status || '',
                t.assignee || 'Sin asignar',
                t.age_days == null ? '-' : (t.age_days === 0 ? 'hoy' : t.age_days + ' d'),
            ]),
            'Sin tickets abiertos'
        );

        heading('Desglose por empresa', 11);
        table(
            ['Empresa', 'Creados', 'Abiertos/Pendientes', 'SLA incumplidos'],
            [CW - 80 - 130 - 100, 80, 130, 100],
            (lastData.by_brand || []).map(r => [
                r.brand, r.created.toLocaleString(), r.open.toLocaleString(), r.breached.toLocaleString(),
            ]),
            'Sin datos en el rango'
        );

        // --- Pie con paginación ------------------------------------------------
        const total = doc.internal.getNumberOfPages();
        for (let p = 1; p <= total; p++) {
            doc.setPage(p);
            doc.setFont('helvetica', 'normal').setFontSize(8).setTextColor(...MUTED);
            doc.text('TicketFlow', M, PH - 20);
            doc.text(`Página ${p} de ${total}`, PW - M, PH - 20, { align: 'right' });
        }

        const slug = (s) => s.toLowerCase().normalize('NFD')
            .replace(/[̀-ͯ]/g, '')   // quita los diacríticos ya separados por NFD
            .replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '');
        const sel = $('repBrand');
        const brandPart = sel.value ? '-' + slug(sel.options[sel.selectedIndex].text) : '';
        doc.save(`reportes${brandPart}-${fmtDate(now)}.pdf`);
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

            lastData = json;

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
            setSatisfactionFace(k.satisfaction_pct);
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
    $('repExportPdf').addEventListener('click', () => {
        try {
            exportPdf();
        } catch (e) {
            console.error('Error generando el PDF:', e);
            if (window.toast) window.toast.error('No se ha podido generar el PDF');
        }
    });

    // Redibujar las gráficas al cambiar de tema (claro/oscuro): Chart.js captura
    // los colores (--color-text/--color-border) en el momento de pintar, así que
    // hay que recrearlas. Listener singleton para no acumular handlers en cada
    // re-entrada del fragmento (tabs.js re-ejecuta el script).
    if (window.__tfReportThemeHandler) {
        document.removeEventListener('tf:themechange', window.__tfReportThemeHandler);
    }
    window.__tfReportThemeHandler = function () {
        if (document.getElementById('repKpis')) load();
    };
    document.addEventListener('tf:themechange', window.__tfReportThemeHandler);

    setPreset(30);
    load();
})();
