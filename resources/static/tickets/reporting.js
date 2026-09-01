(function () {
    'use strict';

    const $ = (id) => document.getElementById(id);
    if (!$('repSections') || typeof Chart === 'undefined') return;

    const ZONES = [
        {
            key: 'executive', title: 'Resumen ejecutivo', icon: 'fa-compass',
            description: 'Panorama por producto y comparación con el periodo anterior equivalente.',
            charts: [
                ['volume', 'Demanda y pendientes por producto', 'Recibidos, resueltos y pendientes al cierre'],
                ['quality', 'Calidad por producto', 'Cumplimiento SLA y satisfacción'],
            ], matrix: true,
        },
        {
            key: 'tickets', title: 'Demanda', icon: 'fa-ticket-alt',
            description: 'Demanda recibida, resolución y composición del volumen.',
            charts: [
                ['hour', 'Tickets creados por hora', 'Distribución porcentual de creación'],
                ['weekday', 'Promedio por día de la semana', 'Promedio de tickets creados'],
                ['createdDate', 'Tickets creados por fecha', 'Creados y resueltos del volumen recibido'],
                ['dimensionSummary', 'Tickets recibidos por producto', 'Volumen y proporción por producto o servicio'],
                ['dimensionDate', 'Demanda por producto y fecha', 'Evolución con un color estable por producto'],
                ['monthYear', 'Tickets creados por mes y año', 'Comparativa de los últimos cinco años'],
            ],
        },
        {
            key: 'efficiency', title: 'Eficiencia del servicio', icon: 'fa-stopwatch',
            description: 'Tiempos medianos, transferencias y esfuerzo de resolución.',
            charts: [
                ['groupStations', 'Transferencias entre grupos', 'Distribución por número de grupos que intervienen'],
                ['agentReplies', 'Respuestas del agente', 'Distribución de respuestas por ticket'],
                ['firstReply', 'Primera respuesta y asignación', 'Medianas en horas por fecha'],
                ['resolutionWait', 'Resolución y espera', 'Medianas en horas por fecha'],
                ['stationsDate', 'Intervinientes por fecha', 'Promedio de asignados y grupos'],
                ['repliesSolved', 'Respuestas y resoluciones', 'Promedio de respuestas y tickets resueltos'],
            ],
        },
        {
            key: 'assignee_activity', title: 'Rendimiento del equipo', icon: 'fa-user-check',
            description: 'Resultados y calidad de los agentes que resuelven tickets.',
            charts: [
                ['satisfaction', 'Satisfacción del asignado', 'Valoraciones buenas y malas'],
                ['resolutionBrackets', 'Tiempo de resolución', 'Distribución por tramos'],
                ['satisfactionWait', 'Satisfacción y espera', 'Evolución por fecha'],
            ], table: true,
        },
        {
            key: 'agent_updates', title: 'Interacciones del equipo', icon: 'fa-comments',
            description: 'Comentarios, tickets trabajados y actividad de los agentes.',
            charts: [
                ['commentAverages', 'Comentarios por fecha', 'Volumen y promedios por ticket'],
                ['activityDate', 'Actividad de tickets por fecha', 'Comentados, resueltos y creados'],
            ], table: true,
        },
        {
            key: 'unsolved', title: 'Pendientes', icon: 'fa-hourglass-half',
            description: 'Foto actual de tickets abiertos y pendientes.',
            charts: [
                ['status', 'Estado actual', 'Abiertos y pendientes'],
                ['assignment', 'Asignación de abiertos', 'Asignados y sin asignar'],
                ['dimension', 'Pendientes por producto', 'Abiertos y pendientes por producto o servicio'],
                ['creationMonth', 'Mes de creación', 'Antigüedad del inventario no resuelto'],
            ], table: true,
        },
        {
            key: 'backlog', title: 'Evolución del backlog', icon: 'fa-layer-group',
            description: 'Reconstrucción del inventario abierto en cortes diarios y semanales.',
            charts: [
                ['daily', 'Backlog diario por estado', 'Últimos 30 días'],
                ['weekly', 'Backlog semanal por estado', 'Últimas 12 semanas'],
                ['dimension', 'Backlog semanal por producto', 'Evolución por producto o servicio'],
            ],
        },
        {
            key: 'satisfaction', title: 'Experiencia del cliente', icon: 'fa-smile',
            description: 'CSAT, participación y calidad de las valoraciones.',
            charts: [
                ['comments', 'Valoraciones y comentarios', 'Buenas y malas, con y sin comentario'],
                ['funnel', 'Embudo de valoración', 'Resueltos, encuestados y valorados'],
                ['daily', 'Satisfacción por fecha', 'Puntuación y valoraciones recibidas'],
                ['dimension', 'Satisfacción por producto', 'Buenas, malas y puntuación por producto o servicio'],
                ['monthly', 'Satisfacción por mes', 'Puntuación y volumen valorado'],
                ['ratedMonthly', 'Participación por mes', 'Valorados sobre encuestados'],
            ],
        },
        {
            key: 'sla', title: 'SLA', icon: 'fa-shield-alt',
            description: 'Cumplimiento, incumplimientos y distribución de objetivos SLA.',
            charts: [
                ['completed', 'Objetivos completados por fecha', 'Cumplidos e incumplidos'],
                ['dimension', 'Objetivos SLA por producto', 'Cumplidos e incumplidos por producto o servicio'],
                ['hour', 'Incumplimientos por hora', 'Distribución porcentual'],
                ['weekday', 'Incumplimientos por día', 'Distribución porcentual'],
                ['metric', 'Cumplimiento por métrica', 'Primera respuesta y resolución total'],
                ['monthly', 'Cumplimiento mensual', 'Evolución por métrica SLA'],
            ],
        },
    ];

    const state = {
        active: 'executive', product: '', data: {}, requests: {}, charts: {}, exporting: false,
    };

    function esc(value) {
        return String(value == null ? '' : value)
            .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;').replace(/'/g, '&#039;');
    }

    function helpTitle(title, help) {
        const label = `${title}. ${help}`;
        return `<h3 class="tf-report-help-title" tabindex="0" data-help="${esc(help)}" ` +
            `aria-label="${esc(label)}">${esc(title)}<i class="far fa-question-circle" aria-hidden="true"></i></h3>`;
    }

    function buildShell() {
        $('repZones').innerHTML = ZONES.map((zone, index) =>
            `<button type="button" class="tf-report-zone${index === 0 ? ' is-active' : ''}" ` +
            `data-zone="${zone.key}" aria-selected="${index === 0}">` +
            `<i class="fas ${zone.icon}" aria-hidden="true"></i><span>${zone.title}</span></button>`
        ).join('');

        $('repSections').innerHTML = ZONES.map((zone, index) => {
            const panels = zone.charts.map(chart => {
                const help = chart[3] || `${chart[2]}. Se calcula con el periodo y los filtros seleccionados.`;
                return `<article class="tf-report-panel" data-chart-panel="${zone.key}:${chart[0]}">` +
                    `<div class="tf-report-panel-head"><div>${helpTitle(chart[1], help)}` +
                    `<p class="tf-report-panel-note">${esc(chart[2])}</p></div></div>` +
                    `<div class="tf-report-canvas"><canvas id="chart-${zone.key}-${chart[0]}"></canvas></div></article>`;
            }).join('');
            const table = zone.table ? `<article class="tf-report-panel tf-report-panel--wide" ` +
                `id="table-panel-${zone.key}" hidden></article>` : '';
            const matrix = zone.matrix ? `<article class="tf-report-panel tf-report-panel--wide tf-product-matrix-panel" ` +
                `id="matrix-panel-${zone.key}"></article>` : '';
            return `<section class="tf-report-section${index === 0 ? ' is-active' : ''}" ` +
                `id="section-${zone.key}" data-section="${zone.key}" aria-hidden="${index !== 0}">` +
                `<header class="tf-report-section-head"><div><h2>${zone.title}</h2><p>${zone.description}</p></div></header>` +
                `<div class="tf-report-kpis" id="kpis-${zone.key}"></div>` +
                `<p class="tf-report-insight" id="insight-${zone.key}"></p>` +
                `<div class="tf-report-grid">${matrix}${panels}${table}</div></section>`;
        }).join('');
    }

    function cssVar(name, fallback) {
        if (state.exporting) return fallback;
        const value = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
        return value || fallback;
    }

    function theme() {
        return {
            text: cssVar('--color-text', '#1e293b'),
            muted: cssVar('--color-text-muted', '#64748b'),
            grid: cssVar('--color-border', '#e2e8f0'),
            colors: ['#5b67ca', '#56b6c2', '#98c379', '#d19a66', '#e06c75', '#61afef', '#c678dd', '#abb2bf'],
        };
    }

    function productColor(data, label) {
        const product = (data.products || []).find(item => item.name === label || item.code === label);
        return product ? product.color : '#64748b';
    }

    function chartId(section, key) { return `chart-${section}-${key}`; }
    function chartKey(section, key) { return `${section}:${key}`; }
    function num(value) { return value == null || Number.isNaN(Number(value)) ? null : Number(value); }
    function formatValue(value, unit) {
        if (value == null) return '—';
        const numeric = Number(value);
        const shown = Number.isFinite(numeric)
            ? numeric.toLocaleString('es-ES', { maximumFractionDigits: 1 }) : value;
        return unit ? `${shown}${unit === '%' ? '%' : ` ${unit}`}` : shown;
    }

    // En la web los valores exactos viven en el tooltip. Durante la exportación
    // se imprimen sobre las barras para que el PDF conserve esa información.
    const PDF_BAR_VALUE_LABELS = {
        id: 'tfPdfBarValueLabels',
        afterDatasetsDraw(chart) {
            if (!state.exporting) return;
            const ctx = chart.ctx;
            const horizontal = chart.options.indexAxis === 'y';
            ctx.save();
            ctx.font = '600 9px Arial, sans-serif';
            ctx.textBaseline = 'middle';
            chart.data.datasets.forEach((dataset, datasetIndex) => {
                const meta = chart.getDatasetMeta(datasetIndex);
                if (meta.hidden || meta.type !== 'bar') return;
                meta.data.forEach((element, index) => {
                    const value = num(dataset.data[index]);
                    if (value === null || value === 0) return;
                    const text = formatValue(value, '');
                    const stacked = Boolean(dataset.stack);
                    let x;
                    let y;
                    let align = 'center';
                    if (horizontal) {
                        y = element.y;
                        x = stacked ? (element.x + element.base) / 2 : element.x + 4;
                        align = stacked ? 'center' : 'left';
                        if (!stacked && x + ctx.measureText(text).width > chart.chartArea.right) {
                            x = element.x - 4;
                            align = 'right';
                        }
                    } else {
                        x = element.x;
                        y = stacked ? (element.y + element.base) / 2 : Math.max(chart.chartArea.top + 6, element.y - 7);
                    }
                    ctx.textAlign = align;
                    ctx.lineWidth = 3;
                    ctx.strokeStyle = 'rgba(255,255,255,.96)';
                    ctx.strokeText(text, x, y);
                    ctx.fillStyle = '#1e293b';
                    ctx.fillText(text, x, y);
                });
            });
            ctx.restore();
        },
    };

    function doughnutLegendLabels(chart) {
        const labels = Chart.overrides.doughnut.plugins.legend.labels.generateLabels(chart);
        if (!state.exporting) return labels;
        const values = chart.data.datasets[0].data.map(value => num(value) || 0);
        const total = values.reduce((sum, value) => sum + value, 0);
        return labels.map((label, position) => {
            const index = label.index == null ? position : label.index;
            const value = values[index];
            const pct = total ? Math.round(value / total * 1000) / 10 : 0;
            return Object.assign({}, label, {
                text: `${chart.data.labels[index] || label.text}: ${formatValue(value, '')} (${formatValue(pct, '%')})`,
            });
        });
    }

    function baseOptions(extra) {
        const t = theme();
        const options = {
            responsive: true, maintainAspectRatio: false, animation: false,
            interaction: { mode: 'index', intersect: false },
            plugins: {
                legend: { position: 'bottom', labels: { color: t.text, boxWidth: 11, boxHeight: 11, usePointStyle: true } },
                tooltip: { backgroundColor: '#1e293b', titleColor: '#fff', bodyColor: '#fff', padding: 9 },
            },
            scales: {
                x: { ticks: { color: t.muted, maxTicksLimit: 13 }, grid: { color: t.grid } },
                y: { beginAtZero: true, ticks: { color: t.muted, precision: 0 }, grid: { color: t.grid } },
            },
        };
        if (!extra) return options;
        Object.keys(extra).forEach(key => {
            if (typeof extra[key] === 'object' && !Array.isArray(extra[key]) && options[key]) {
                options[key] = Object.assign({}, options[key], extra[key]);
            } else options[key] = extra[key];
        });
        return options;
    }

    function makeChart(section, key, config) {
        const id = chartId(section, key);
        const canvas = $(id);
        if (!canvas) return;
        const registryKey = chartKey(section, key);
        if (state.charts[registryKey]) state.charts[registryKey].destroy();
        const holder = canvas.parentElement;
        const oldEmpty = holder.querySelector('.tf-chart-empty');
        if (oldEmpty) oldEmpty.remove();
        const values = (config.data.datasets || []).flatMap(dataset => dataset.data || []);
        const hasData = values.some(value => num(value) !== null && num(value) !== 0);
        if (!hasData) {
            canvas.hidden = true;
            holder.insertAdjacentHTML('beforeend', '<div class="tf-chart-empty"><span>Sin datos para los filtros seleccionados</span></div>');
            delete state.charts[registryKey];
            return;
        }
        canvas.hidden = false;
        state.charts[registryKey] = new Chart(canvas, config);
    }

    function bars(section, key, labels, datasets, opts) {
        const t = theme();
        makeChart(section, key, {
            type: 'bar', data: { labels, datasets: datasets.map((dataset, index) => Object.assign({
                backgroundColor: dataset.color || t.colors[index], borderRadius: 3, borderWidth: 0,
            }, dataset)) },
            options: baseOptions(opts),
            plugins: state.exporting ? [PDF_BAR_VALUE_LABELS] : [],
        });
    }

    function lines(section, key, labels, datasets, opts) {
        const t = theme();
        makeChart(section, key, {
            type: 'line', data: { labels, datasets: datasets.map((dataset, index) => Object.assign({
                borderColor: dataset.color || t.colors[index], backgroundColor: dataset.color || t.colors[index],
                borderWidth: 2, fill: false, tension: .28, pointRadius: labels.length > 24 ? 0 : 2,
                spanGaps: true,
            }, dataset)) },
            options: baseOptions(opts),
        });
    }

    function doughnut(section, key, rows) {
        const t = theme();
        makeChart(section, key, {
            type: 'doughnut',
            data: { labels: rows.map(row => row.label), datasets: [{ data: rows.map(row => row.n), backgroundColor: t.colors }] },
            options: {
                responsive: true, maintainAspectRatio: false, animation: false, cutout: '58%',
                plugins: { legend: { position: 'right', labels: {
                    color: t.text, boxWidth: 11, usePointStyle: true,
                    generateLabels: doughnutLegendLabels,
                } } },
            },
        });
    }

    function stacked(section, key, rows, labelField, series) {
        bars(section, key, rows.map(row => row[labelField]), series.map(item => ({
            label: item[1], data: rows.map(row => num(row[item[0]]) || 0), color: item[2], stack: 'total',
        })), { scales: {
            x: { stacked: true, ticks: { color: theme().muted, maxTicksLimit: 13 }, grid: { display: false } },
            y: { stacked: true, beginAtZero: true, ticks: { color: theme().muted, precision: 0 }, grid: { color: theme().grid } },
        } });
    }

    function alignedRows(seriesLists, xField) {
        const labels = [...new Set(seriesLists.flatMap(rows => rows.map(row => row[xField])))].sort();
        return labels;
    }

    function renderExecutive(data) {
        const volume = data.charts.volume_by_product || [];
        bars('executive', 'volume', ['Recibidos', 'Resueltos', 'Pendientes'], volume.map(row => ({
            label: row.label,
            data: [row.created, row.resolved, row.backlog],
            color: productColor(data, row.label),
        })));
        const quality = data.charts.quality_by_product || [];
        bars('executive', 'quality', ['SLA', 'CSAT'], quality.map(row => ({
            label: row.label,
            data: [row.sla, row.csat],
            color: productColor(data, row.label),
        })), { scales: {
            x: { ticks: { color: theme().muted }, grid: { display: false } },
            y: { beginAtZero: true, max: 100, ticks: { color: theme().muted, callback: value => `${value}%` }, grid: { color: theme().grid } },
        } });
    }

    function renderTickets(data) {
        const c = data.charts;
        bars('tickets', 'hour', c.created_by_hour.labels, [{ label: '% creados', data: c.created_by_hour.values, color: '#98c379' }]);
        bars('tickets', 'weekday', c.average_by_weekday.labels, [{ label: 'Promedio', data: c.average_by_weekday.values, color: '#d19a66' }]);
        const dateLabels = alignedRows([c.created_by_date.created, c.created_by_date.solved], 'day');
        const created = Object.fromEntries(c.created_by_date.created.map(row => [row.day, row.n]));
        const solved = Object.fromEntries(c.created_by_date.solved.map(row => [row.day, row.n]));
        lines('tickets', 'createdDate', dateLabels, [
            { label: 'Creados', data: dateLabels.map(day => created[day] || 0), color: '#5b67ca', fill: true, backgroundColor: 'rgba(91,103,202,.10)' },
            { label: 'Resueltos', data: dateLabels.map(day => solved[day] || 0), color: '#56b6c2' },
        ]);
        let dim = c.dimensions.product;
        bars('tickets', 'dimensionSummary', dim.summary.map(row => row.label), [{
            label: 'Tickets', data: dim.summary.map(row => row.n),
            backgroundColor: dim.summary.map(row => productColor(data, row.label)),
        }]);
        dim = c.dimensions.product;
        const dimLabels = [...new Set(dim.by_day.flatMap(row => Object.keys(row.values)))];
        lines('tickets', 'dimensionDate', dim.by_day.map(row => row.day), dimLabels.map((label, index) => ({
            label, data: dim.by_day.map(row => row.values[label] || 0), color: productColor(data, label),
        })));
        const years = c.created_by_month_year.years;
        lines('tickets', 'monthYear', ['Ene','Feb','Mar','Abr','May','Jun','Jul','Ago','Sep','Oct','Nov','Dic'],
            years.map((row, index) => ({ label: String(row.year), data: row.values, color: theme().colors[index % theme().colors.length] })));
    }

    function renderEfficiency(data) {
        const c = data.charts;
        bars('efficiency', 'groupStations', c.group_stations_brackets.map(r => r.label), [{ label: 'Tickets', data: c.group_stations_brackets.map(r => r.n) }]);
        bars('efficiency', 'agentReplies', c.agent_replies_brackets.map(r => r.label), [{ label: 'Tickets', data: c.agent_replies_brackets.map(r => r.n), color: '#56b6c2' }]);
        lines('efficiency', 'firstReply', c.first_reply_assignment_by_date.map(r => r.day), [
            { label: 'Primera respuesta (h)', data: c.first_reply_assignment_by_date.map(r => r.first_reply) },
            { label: 'Primera asignación (h)', data: c.first_reply_assignment_by_date.map(r => r.first_assignment), color: '#d19a66' },
        ]);
        lines('efficiency', 'resolutionWait', c.resolution_wait_by_date.map(r => r.day), [
            { label: 'Resolución completa (h)', data: c.resolution_wait_by_date.map(r => r.full_resolution) },
            { label: 'Espera solicitante (h)', data: c.resolution_wait_by_date.map(r => r.requester_wait), color: '#e06c75' },
        ]);
        lines('efficiency', 'stationsDate', c.stations_by_date.map(r => r.day), [
            { label: 'Asignado', data: c.stations_by_date.map(r => r.assignee) },
            { label: 'Grupo', data: c.stations_by_date.map(r => r.group), color: '#98c379' },
        ]);
        lines('efficiency', 'repliesSolved', c.replies_resolutions_by_date.map(r => r.day), [
            { label: 'Respuestas', data: c.replies_resolutions_by_date.map(r => r.agent_replies) },
            { label: 'Resueltos', data: c.replies_resolutions_by_date.map(r => r.solved), color: '#56b6c2' },
        ]);
    }

    function renderAssignee(data) {
        const c = data.charts;
        doughnut('assignee_activity', 'satisfaction', c.satisfaction);
        bars('assignee_activity', 'resolutionBrackets', c.resolution_brackets.map(r => r.label), [{ label: 'Tickets', data: c.resolution_brackets.map(r => r.n) }]);
        lines('assignee_activity', 'satisfactionWait', c.satisfaction_wait_by_date.map(r => r.day), [
            { label: 'CSAT (%)', data: c.satisfaction_wait_by_date.map(r => r.satisfaction_pct), yAxisID: 'y' },
            { label: 'Espera (h)', data: c.satisfaction_wait_by_date.map(r => r.requester_wait_hours), color: '#d19a66', yAxisID: 'y1' },
        ], dualAxisOptions('%', 'h'));
    }

    function dualAxisOptions(left, right) {
        const t = theme();
        return { scales: {
            x: { ticks: { color: t.muted, maxTicksLimit: 13 }, grid: { color: t.grid } },
            y: { beginAtZero: true, position: 'left', ticks: { color: t.muted, callback: v => `${v}${left}` }, grid: { color: t.grid } },
            y1: { beginAtZero: true, position: 'right', ticks: { color: t.muted, callback: v => `${v}${right}` }, grid: { drawOnChartArea: false } },
        } };
    }

    function renderUpdates(data) {
        const c = data.charts;
        lines('agent_updates', 'commentAverages', c.comment_averages_by_date.map(r => r.day), [
            { label: 'Comentarios', data: c.comment_averages_by_date.map(r => r.comments) },
            { label: 'Públicos/ticket', data: c.comment_averages_by_date.map(r => r.public_per_ticket), color: '#56b6c2' },
            { label: 'Internos/ticket', data: c.comment_averages_by_date.map(r => r.internal_per_ticket), color: '#d19a66' },
        ]);
        lines('agent_updates', 'activityDate', c.activity_by_date.map(r => r.day), [
            { label: 'Comentados', data: c.activity_by_date.map(r => r.commented) },
            { label: 'Resueltos', data: c.activity_by_date.map(r => r.solved), color: '#98c379' },
            { label: 'Creados', data: c.activity_by_date.map(r => r.created), color: '#d19a66' },
        ]);
    }

    function renderUnsolved(data) {
        const c = data.charts;
        doughnut('unsolved', 'status', c.by_status);
        doughnut('unsolved', 'assignment', c.assignment_status);
        const dim = c.dimensions.product;
        stacked('unsolved', 'dimension', dim.rows, 'label', [['open', 'Abiertos', '#5b67ca'], ['pending', 'Pendientes', '#d19a66']]);
        stacked('unsolved', 'creationMonth', c.by_creation_month, 'month', [['open', 'Abiertos', '#5b67ca'], ['pending', 'Pendientes', '#d19a66']]);
    }

    function renderBacklog(data) {
        const c = data.charts;
        stacked('backlog', 'daily', c.daily_by_status, 'day', [['open', 'Abiertos', '#5b67ca'], ['pending', 'Pendientes', '#d19a66']]);
        stacked('backlog', 'weekly', c.weekly_by_status, 'day', [['open', 'Abiertos', '#5b67ca'], ['pending', 'Pendientes', '#d19a66']]);
        const dim = c.dimensions.product;
        lines('backlog', 'dimension', dim.series.map(r => r.day), dim.labels.map((label, index) => ({
            label, data: dim.series.map(r => r.values[label] || 0), color: productColor(data, label),
        })));
    }

    function renderSatisfaction(data) {
        const c = data.charts;
        bars('satisfaction', 'comments', c.good_bad_comments.map(r => r.label), [{ label: 'Valoraciones', data: c.good_bad_comments.map(r => r.n) }]);
        bars('satisfaction', 'funnel', c.funnel.map(r => r.label), [{ label: 'Tickets', data: c.funnel.map(r => r.n), color: '#56b6c2' }], { indexAxis: 'y' });
        lines('satisfaction', 'daily', c.score_rated_by_date.map(r => r.day), [
            { label: 'CSAT (%)', data: c.score_rated_by_date.map(r => r.score), yAxisID: 'y' },
            { label: 'Valoraciones', data: c.score_rated_by_date.map(r => r.rated), color: '#d19a66', yAxisID: 'y1' },
        ], dualAxisOptions('%', ''));
        const dim = c.dimensions.product;
        stacked('satisfaction', 'dimension', dim.rows, 'label', [['good', 'Buenas', '#98c379'], ['bad', 'Malas', '#e06c75']]);
        lines('satisfaction', 'monthly', c.score_rated_by_month.map(r => r.month), [
            { label: 'CSAT (%)', data: c.score_rated_by_month.map(r => r.score), yAxisID: 'y' },
            { label: 'Valoraciones', data: c.score_rated_by_month.map(r => r.rated), color: '#56b6c2', yAxisID: 'y1' },
        ], dualAxisOptions('%', ''));
        lines('satisfaction', 'ratedMonthly', c.rated_surveyed_by_month.map(r => r.month), [
            { label: 'Participación (%)', data: c.rated_surveyed_by_month.map(r => r.rated_ratio), yAxisID: 'y' },
            { label: 'Encuestados', data: c.rated_surveyed_by_month.map(r => r.surveyed), color: '#d19a66', yAxisID: 'y1' },
        ], dualAxisOptions('%', ''));
    }

    function renderSla(data) {
        const c = data.charts;
        stacked('sla', 'completed', c.completed_by_date, 'day', [['achieved', 'Cumplidos', '#98c379'], ['breached', 'Incumplidos', '#e06c75']]);
        const dim = c.dimensions.product;
        stacked('sla', 'dimension', dim.rows, 'label', [['achieved', 'Cumplidos', '#98c379'], ['breached', 'Incumplidos', '#e06c75']]);
        bars('sla', 'hour', c.breaches_by_hour.labels, [{ label: '% incumplimientos', data: c.breaches_by_hour.values, color: '#e06c75' }]);
        bars('sla', 'weekday', c.breaches_by_weekday.labels, [{ label: '% incumplimientos', data: c.breaches_by_weekday.values, color: '#e06c75' }]);
        stacked('sla', 'metric', c.by_metric, 'metric', [['achieved', 'Cumplidos', '#98c379'], ['breached', 'Incumplidos', '#e06c75']]);
        const metricNames = [...new Set(c.achievement_by_month.flatMap(r => Object.keys(r.metrics)))];
        lines('sla', 'monthly', c.achievement_by_month.map(r => r.month), metricNames.map((metric, index) => ({
            label: metric, data: c.achievement_by_month.map(r => r.metrics[metric]), color: theme().colors[index],
        })));
    }

    const RENDERERS = {
        executive: renderExecutive, tickets: renderTickets, efficiency: renderEfficiency, assignee_activity: renderAssignee,
        agent_updates: renderUpdates, unsolved: renderUnsolved, backlog: renderBacklog,
        satisfaction: renderSatisfaction, sla: renderSla,
    };

    function renderKpis(section, kpis) {
        const holder = $(`kpis-${section}`);
        holder.innerHTML = (kpis || []).map(kpi => `<article class="tf-kpi-card">` +
            `<span class="tf-kpi-value">${esc(formatValue(kpi.value, kpi.unit))}</span>` +
            `<span class="tf-kpi-label">${esc(kpi.label)}</span>` +
            (kpi.delta == null ? '' : `<span class="tf-kpi-delta is-${esc(kpi.direction)}">` +
                `<i class="fas ${kpi.direction === 'up' ? 'fa-arrow-up' : kpi.direction === 'down' ? 'fa-arrow-down' : 'fa-minus'}"></i>` +
                `${esc((kpi.delta > 0 ? '+' : '') + formatValue(kpi.delta, kpi.unit))} vs. periodo anterior</span>`) +
            (kpi.note ? `<span class="tf-kpi-note">${esc(kpi.note)}</span>` : '') + '</article>').join('');
        holder.hidden = !(kpis || []).length;
    }

    function renderMatrix(data) {
        const panel = $('matrix-panel-executive');
        if (!panel) return;
        const cells = [
            ['created', 'Recibidos', ''], ['resolved', 'Resueltos', ''], ['backlog', 'Pendientes', ''],
            ['sla', 'SLA', '%'], ['csat', 'CSAT', '%'],
        ];
        function metricCell(row, key, unit) {
            const comparison = (row.comparison || {})[key] || {};
            const delta = comparison.delta;
            const trend = delta == null ? '' : `<span class="tf-matrix-delta is-${esc(comparison.direction)}">` +
                `<i class="fas ${comparison.direction === 'up' ? 'fa-arrow-up' : comparison.direction === 'down' ? 'fa-arrow-down' : 'fa-minus'}"></i>` +
                `${esc((delta > 0 ? '+' : '') + formatValue(delta, unit))}</span>`;
            return `<td><strong>${esc(formatValue(row[key], unit))}</strong>${trend}</td>`;
        }
        panel.innerHTML = `<div class="tf-report-panel-head"><div>${helpTitle(
            'Cuadro ejecutivo por producto',
            'Resume demanda, resolución, pendientes y calidad. Las flechas comparan con el periodo inmediatamente anterior de la misma duración.'
        )}<p class="tf-report-panel-note">Mismo orden y color en todo el reporte</p></div></div>` +
            `<div class="tf-report-table-area"><table class="tf-report-table tf-product-matrix"><thead><tr><th>Producto / servicio</th>` +
            cells.map(cell => `<th>${cell[1]}</th>`).join('') + `<th>Muestra CSAT</th></tr></thead><tbody>` +
            (data.matrix || []).map(row => `<tr><td><span class="tf-product-name">` +
                `<span class="tf-product-dot" style="--product-color:${esc(row.color)}"></span>` +
                `<i class="fas ${esc(row.icon)}" aria-hidden="true"></i><strong>${esc(row.name)}</strong></span></td>` +
                cells.map(cell => metricCell(row, cell[0], cell[2])).join('') + `<td>${esc(formatValue(row.ratings, ''))}</td></tr>`).join('') +
            `</tbody></table></div>`;
    }

    function renderTable(section, tables) {
        const panel = $(`table-panel-${section}`);
        if (!panel) return;
        const table = (tables || [])[0];
        if (!table) { panel.hidden = true; return; }
        const rows = table.rows || [];
        panel.hidden = false;
        const titles = {
            assignee_activity: ['Detalle por agente', 'Compara volumen, tiempos y calidad para localizar cargas o resultados atípicos.'],
            agent_updates: ['Interacciones por agente', 'Muestra el volumen de comentarios y tickets trabajados por cada persona.'],
            unsolved: ['Pendientes por agente', 'Permite localizar concentración de pendientes y antigüedad por persona asignada.'],
        };
        const title = titles[section] || ['Detalle', 'Desglose de la zona seleccionada.'];
        panel.innerHTML = `<div class="tf-report-panel-head"><div>${helpTitle(
            title[0],
            title[1]
        )}` +
            `<p class="tf-report-panel-note">Hasta 50 filas, ordenadas por actividad</p></div></div>` +
            `<div class="tf-report-table-area"><table class="tf-report-table"><thead><tr>` +
            table.columns.map(column => `<th>${esc(column)}</th>`).join('') + '</tr></thead><tbody>' +
            (rows.length ? rows.map(row => '<tr>' + Object.values(row).map(value =>
                `<td>${esc(formatValue(value, ''))}</td>`).join('') + '</tr>').join('') :
                `<tr><td colspan="${table.columns.length}">Sin datos para los filtros seleccionados</td></tr>`) +
            '</tbody></table></div>';
    }

    function renderSection(section) {
        const data = state.data[section];
        if (!data) return;
        renderKpis(section, data.kpis);
        const insight = $(`insight-${section}`);
        if (insight) insight.textContent = data.insight || '';
        if (section === 'executive') renderMatrix(data);
        RENDERERS[section](data);
        renderTable(section, data.tables);
    }

    function fmtDate(date) {
        const local = new Date(date.getTime() - date.getTimezoneOffset() * 60000);
        return local.toISOString().slice(0, 10);
    }

    function setPreset(days) {
        const to = new Date();
        const from = new Date();
        from.setDate(to.getDate() - (days - 1));
        $('repFrom').value = fmtDate(from);
        $('repTo').value = fmtDate(to);
        $('repRangeMode').textContent = `Periodo activo: últimos ${days} días`;
    }

    function params(section) {
        const values = {
            section, product: state.product, group: $('repGroup').value,
            assignee: $('repAssignee').value, organization: $('repOrganization').value,
            channel: $('repChannel').value,
            priority: $('repPriority').value, type: $('repType').value,
            from: $('repFrom').value, to: $('repTo').value,
        };
        const p = new URLSearchParams();
        Object.entries(values).forEach(([key, value]) => { if (value) p.set(key, value); });
        return p.toString();
    }

    function invalidate() {
        Object.values(state.charts).forEach(chart => chart.destroy());
        state.charts = {}; state.data = {}; state.requests = {};
        ZONES.forEach(zone => { $(`kpis-${zone.key}`).innerHTML = ''; });
    }

    async function loadSection(section, force) {
        if (state.data[section] && !force) return state.data[section];
        if (state.requests[section] && !force) return state.requests[section];
        const spinner = $('repLoading');
        spinner.style.display = 'flex';
        $('repStatus').className = 'tf-report-status';
        $('repStatus').textContent = '';
        const request = fetch(`/reporting/data/?${params(section)}`, { headers: { Accept: 'application/json' } })
            .then(async response => {
                const data = await response.json();
                if (!response.ok || !data.ok) throw new Error(data.error || `Error HTTP ${response.status}`);
                state.data[section] = data;
                renderSection(section);
                return data;
            })
            .catch(error => {
                console.error(`Error cargando reporting ${section}:`, error);
                $('repStatus').className = 'tf-report-status is-error';
                $('repStatus').textContent = 'No se ha podido cargar esta zona del informe. Revisa los filtros e inténtalo de nuevo.';
                throw error;
            })
            .finally(() => {
                delete state.requests[section];
                if (!Object.keys(state.requests).length) spinner.style.display = 'none';
            });
        state.requests[section] = request;
        return request;
    }

    function activate(section) {
        state.active = section;
        document.querySelectorAll('.tf-report-zone').forEach(button => {
            const active = button.dataset.zone === section;
            button.classList.toggle('is-active', active);
            button.setAttribute('aria-selected', String(active));
        });
        document.querySelectorAll('.tf-report-section').forEach(panel => {
            const active = panel.dataset.section === section;
            panel.classList.toggle('is-active', active);
            panel.setAttribute('aria-hidden', String(!active));
        });
        loadSection(section).catch(() => {});
    }

    function syncReportViewport() {
        const wrap = document.querySelector('.tf-report-wrap');
        if (!wrap) return;
        if (window.matchMedia('(max-width: 768px)').matches) {
            wrap.style.height = '';
            return;
        }
        const viewportHeight = window.visualViewport ? window.visualViewport.height : window.innerHeight;
        // Reserva un pequeño colchón inferior: evita que la última fila o la
        // leyenda de un gráfico quede pegada al borde del navegador o la barra
        // del sistema, incluso mientras la cabecera termina de reajustarse.
        const available = Math.floor(viewportHeight - wrap.getBoundingClientRect().top - 40);
        wrap.style.height = `${Math.max(320, available)}px`;
    }

    function canvasOnWhite(canvas) {
        const result = document.createElement('canvas');
        result.width = canvas.width || 900;
        result.height = canvas.height || 400;
        const ctx = result.getContext('2d');
        ctx.fillStyle = '#ffffff';
        ctx.fillRect(0, 0, result.width, result.height);
        ctx.drawImage(canvas, 0, 0);
        return result.toDataURL('image/png');
    }

    async function exportPdf() {
        if (!window.jspdf || !window.jspdf.jsPDF || state.exporting) return;
        state.exporting = true;
        document.body.classList.add('tf-report-exporting');
        const button = $('repExportPdf');
        const previous = button.innerHTML;
        button.disabled = true;
        button.innerHTML = `<i class="fas fa-circle-notch fa-spin"></i> Preparando ${ZONES.length} zonas…`;
        try {
            await Promise.all(ZONES.map(zone => loadSection(zone.key)));
            ZONES.forEach(zone => renderSection(zone.key));
            await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));

            const { jsPDF } = window.jspdf;
            const doc = new jsPDF({ orientation: 'landscape', unit: 'pt', format: 'a4', compress: true });
            const PW = doc.internal.pageSize.getWidth();
            const PH = doc.internal.pageSize.getHeight();
            const M = 28;
            const CW = PW - M * 2;
            const colors = { primary: [91,103,202], text: [30,41,59], muted: [100,116,139], line: [226,232,240] };

            function whitePage() { doc.setFillColor(255,255,255); doc.rect(0,0,PW,PH,'F'); }
            function pageHeader(zone, range, suffix) {
                whitePage();
                doc.setFillColor(...colors.primary); doc.rect(0,0,PW,8,'F');
                doc.setTextColor(...colors.text).setFont('helvetica','bold').setFontSize(17);
                doc.text(`Reporte · ${zone.title}${suffix || ''}`, M, 34);
                doc.setTextColor(...colors.muted).setFont('helvetica','normal').setFontSize(8.5);
                doc.text(`${range.from} a ${range.to} · ${zone.description}`, M, 49);
            }
            function kpiRow(kpis) {
                if (!kpis.length) return 61;
                const gap = 7; const w = (CW - gap * (kpis.length - 1)) / kpis.length;
                kpis.forEach((kpi, index) => {
                    const x = M + index * (w + gap);
                    doc.setFillColor(248,250,252).setDrawColor(...colors.line).roundedRect(x,61,w,55,4,4,'FD');
                    doc.setFillColor(...colors.primary).rect(x,61,3,55,'F');
                    doc.setTextColor(...colors.text).setFont('helvetica','bold').setFontSize(13);
                    doc.text(formatValue(kpi.value, kpi.unit), x+9,80);
                    doc.setTextColor(...colors.muted).setFont('helvetica','normal').setFontSize(7.2);
                    doc.text(doc.splitTextToSize(kpi.label, w-16)[0] || '', x+9,96);
                    if (kpi.delta != null) {
                        const sign = kpi.delta > 0 ? '+' : '';
                        doc.setTextColor(kpi.direction === 'up' ? 22 : 185, kpi.direction === 'up' ? 122 : 71, kpi.direction === 'up' ? 78 : 71)
                            .setFont('helvetica','bold').setFontSize(6.5);
                        doc.text(`${kpi.direction === 'up' ? '↑' : kpi.direction === 'down' ? '↓' : '–'} ${sign}${formatValue(kpi.delta,kpi.unit)} vs. anterior`,x+9,108);
                    }
                });
                return 124;
            }
            function chartGrid(zone, top, bottomReserve) {
                const gapX = 9, gapY = 8, colW = (CW-gapX)/2;
                const count = zone.charts.length;
                const rows = Math.ceil(count/2);
                const available = PH - top - 32 - (bottomReserve || 0);
                const rowH = Math.min(139, (available-gapY*(rows-1))/rows);
                zone.charts.forEach((definition, index) => {
                    const canvas = $(chartId(zone.key, definition[0]));
                    const x = M + (index%2)*(colW+gapX);
                    const y = top + Math.floor(index/2)*(rowH+gapY);
                    doc.setFillColor(255,255,255).setDrawColor(...colors.line).roundedRect(x,y,colW,rowH,4,4,'FD');
                    doc.setTextColor(...colors.text).setFont('helvetica','bold').setFontSize(8.2);
                    doc.text(definition[1], x+8, y+13);
                    if (canvas && !canvas.hidden) {
                        const image = canvasOnWhite(canvas);
                        const maxW=colW-12, maxH=rowH-21;
                        const ratio=Math.min(maxW/canvas.width,maxH/canvas.height);
                        const w=canvas.width*ratio, h=canvas.height*ratio;
                        doc.addImage(image,'PNG',x+(colW-w)/2,y+18+(maxH-h)/2,w,h,undefined,'FAST');
                    } else {
                        doc.setTextColor(...colors.muted).setFont('helvetica','italic').setFontSize(8);
                        doc.text('Sin datos para los filtros seleccionados', x+colW/2, y+rowH/2, {align:'center'});
                    }
                });
                return top + rows * rowH + (rows - 1) * gapY;
            }
            function tableGeometry(table) {
                const usable = CW;
                const firstW = Math.min(155, usable*.25);
                const otherW = (usable-firstW)/Math.max(1,table.columns.length-1);
                const widths = table.columns.map((_,i)=>i===0?firstW:otherW);
                return {usable, widths};
            }
            function drawTable(table, startIndex, y, maxY) {
                const rows = table.rows || [];
                const {usable, widths} = tableGeometry(table);
                doc.setFillColor(248,250,252).setDrawColor(...colors.line).rect(M,y,usable,18,'FD');
                let x=M;
                doc.setTextColor(...colors.text).setFont('helvetica','bold').setFontSize(6.7);
                table.columns.forEach((column,i)=>{ doc.text(doc.splitTextToSize(column,widths[i]-6)[0]||'',x+3,y+12); x+=widths[i]; });
                y+=18;
                let index=startIndex;
                while(index<rows.length && y+17<=maxY) {
                    const values=Object.values(rows[index]); x=M;
                    doc.setTextColor(...colors.text).setFont('helvetica','normal').setFontSize(7);
                    values.forEach((value,i)=>{
                        const text=doc.splitTextToSize(formatValue(value,''),widths[i]-7)[0]||'';
                        doc.text(text,i===0?x+3:x+widths[i]-3,y+11,{align:i===0?'left':'right'}); x+=widths[i];
                    });
                    doc.setDrawColor(...colors.line).line(M,y+17,M+usable,y+17); y+=17; index+=1;
                }
                return index;
            }
            function tablePages(zone, data, startIndex) {
                const table = (data.tables || [])[0];
                const rows = table ? (table.rows || []) : [];
                let index=startIndex || 0;
                while(table && index<rows.length) {
                    doc.addPage(); pageHeader(zone,data.range,' · Detalle');
                    index = drawTable(table,index,67,PH-28);
                }
            }
            function executiveMatrix(data, y) {
                const rows = data.matrix || [];
                const columns = ['Producto / servicio','Recibidos','Resueltos','Pendientes','SLA','CSAT'];
                const widths = [190,96,96,96,96,96];
                const total = widths.reduce((sum,value)=>sum+value,0);
                doc.setTextColor(...colors.text).setFont('helvetica','bold').setFontSize(8.4);
                doc.text('Cuadro ejecutivo por producto',M,y);
                y+=6;
                doc.setFillColor(245,247,255).setDrawColor(...colors.line).rect(M,y,total,18,'FD');
                let x=M;
                doc.setFontSize(6.8);
                columns.forEach((column,i)=>{doc.text(column,x+5,y+12);x+=widths[i];});
                y+=18;
                rows.forEach(row=>{
                    x=M;
                    const values=[row.name,row.created,row.resolved,row.backlog,row.sla,row.csat];
                    values.forEach((value,i)=>{
                        const unit=i>=4?'%':'';
                        let label=formatValue(value,unit);
                        const key=['name','created','resolved','backlog','sla','csat'][i];
                        const comparison=(row.comparison||{})[key];
                        if(comparison && comparison.delta!=null){
                            const sign=comparison.delta>0?'+':'';
                            label += ` (${sign}${formatValue(comparison.delta,unit)})`;
                        }
                        if(i===0){
                            const hex=(row.color||'#64748b').replace('#','');
                            doc.setFillColor(parseInt(hex.slice(0,2),16),parseInt(hex.slice(2,4),16),parseInt(hex.slice(4,6),16));
                            doc.circle(x+6,y+9,2.5,'F');
                        }
                        doc.setTextColor(...colors.text).setFont('helvetica',i===0?'bold':'normal').setFontSize(7);
                        doc.text(doc.splitTextToSize(label,widths[i]-14)[0]||'',i===0?x+13:x+widths[i]-5,y+12,{align:i===0?'left':'right'});
                        x+=widths[i];
                    });
                    doc.setDrawColor(...colors.line).line(M,y+18,M+total,y+18); y+=18;
                });
                return y+8;
            }

            ZONES.forEach((zone, index) => {
                if (index) doc.addPage();
                const data = state.data[zone.key];
                pageHeader(zone, data.range, '');
                let top = kpiRow(data.kpis || []);
                if (zone.matrix) top = executiveMatrix(data,top+2);
                const table = (data.tables || [])[0];
                const reserve = table && (table.rows || []).length ? 135 : 0;
                const chartBottom = chartGrid(zone,top,reserve);
                let nextRow = 0;
                if (table && (table.rows || []).length) {
                    nextRow = drawTable(table,0,chartBottom+7,PH-25);
                }
                if (data.insight) {
                    doc.setTextColor(...colors.muted).setFont('helvetica','italic').setFontSize(6.8);
                    doc.text(doc.splitTextToSize(`Lectura: ${data.insight}`,CW)[0]||'',M,PH-24);
                }
                tablePages(zone, data, nextRow);
            });
            const pages=doc.internal.getNumberOfPages();
            for(let page=1;page<=pages;page++){
                doc.setPage(page); doc.setTextColor(...colors.muted).setFont('helvetica','normal').setFontSize(7.5);
                doc.text('TicketFlow · Reporte generado en paleta clara',M,PH-13);
                doc.text(`Página ${page} de ${pages}`,PW-M,PH-13,{align:'right'});
            }
            doc.setProperties({ title: 'Reporte de métricas TicketFlow', subject: 'Métricas de soporte', creator: 'TicketFlow' });
            doc.save(`reporte-metricas-${$('repFrom').value}-${$('repTo').value}.pdf`);
        } finally {
            state.exporting = false;
            ZONES.forEach(zone => renderSection(zone.key));
            document.body.classList.remove('tf-report-exporting');
            button.disabled = false; button.innerHTML = previous;
        }
    }

    buildShell();
    setPreset(7);

    $('repZones').addEventListener('click', event => {
        const button = event.target.closest('[data-zone]');
        if (button) activate(button.dataset.zone);
    });
    document.querySelectorAll('.tf-report-presets button').forEach(button => {
        button.addEventListener('click', () => {
            document.querySelectorAll('.tf-report-presets button').forEach(item => {
                item.classList.toggle('is-active', item === button);
                item.setAttribute('aria-pressed', String(item === button));
            });
            setPreset(Number(button.dataset.days)); invalidate(); activate(state.active);
        });
    });
    [$('repFrom'), $('repTo')].forEach(input => input.addEventListener('change', () => {
        document.querySelectorAll('.tf-report-presets button').forEach(item => {
            item.classList.remove('is-active'); item.setAttribute('aria-pressed', 'false');
        });
        $('repRangeMode').textContent = `Periodo activo: ${$('repFrom').value} → ${$('repTo').value}`;
        invalidate(); activate(state.active);
    }));
    $('repProductRail').addEventListener('click', event => {
        const button = event.target.closest('.tf-product-chip');
        if (!button) return;
        state.product = button.dataset.productId || '';
        document.querySelectorAll('.tf-product-chip').forEach(chip => {
            const selected = chip === button;
            chip.classList.toggle('is-selected', selected);
            chip.setAttribute('aria-pressed', String(selected));
        });
        invalidate(); activate(state.active);
    });
    ['repGroup','repAssignee','repOrganization','repChannel','repPriority','repType']
        .forEach(id => $(id).addEventListener('change', () => {
            invalidate(); activate(state.active);
        }));
    $('repExport').addEventListener('click', () => { window.location.href = `/reporting/export.csv?${params('')}`; });
    $('repExportPdf').addEventListener('click', () => exportPdf().catch(error => {
        console.error('Error generando PDF:', error);
        if (window.toast) window.toast.error('No se ha podido generar el PDF');
    }));
    const toggle=$('repToolbarToggle');
    toggle.addEventListener('click',()=>{
        const open=$('repToolbar').classList.toggle('is-open'); toggle.setAttribute('aria-expanded',String(open));
    });
    if (window.__tfReportThemeHandler) document.removeEventListener('tf:themechange',window.__tfReportThemeHandler);
    window.__tfReportThemeHandler=()=>{ if($('repSections')) Object.keys(state.data).forEach(renderSection); };
    document.addEventListener('tf:themechange',window.__tfReportThemeHandler);
    if (window.__tfReportResizeHandler) {
        window.removeEventListener('resize', window.__tfReportResizeHandler);
        if (window.visualViewport) window.visualViewport.removeEventListener('resize', window.__tfReportResizeHandler);
    }
    window.__tfReportResizeHandler = syncReportViewport;
    window.addEventListener('resize', window.__tfReportResizeHandler);
    if (window.visualViewport) window.visualViewport.addEventListener('resize', window.__tfReportResizeHandler);
    requestAnimationFrame(syncReportViewport);
    activate('executive');
})();
