/*
 * Ancho de columnas ajustable en todas las tablas de TicketFlow.
 *
 * Se carga una sola vez desde base.html. Las páginas que llegan como
 * fragmento (tabs.js) y las tablas que se repintan después, como las de
 * informes, se detectan con un MutationObserver. Arrastrar el borde de una
 * cabecera cambia su ancho; doble clic devuelve la tabla a su ancho
 * automático. Los anchos se recuerdan por navegador y por tabla.
 */
(function () {
    'use strict';
    if (window.__tfTableResize) return;
    window.__tfTableResize = true;

    // Lista explícita: los correos de los tickets traen sus propias tablas y
    // no deben recibir asas. Una tabla nueva solo necesita data-resizable-columns.
    const TABLES = [
        'table[data-workspace-ticket-table]',   // home, lista de tickets y tickets de perfiles
        'table.tickets-table',
        'table.customers-table',                // usuarios
        'table.tf-admin-table',                 // panel de administración
        'table.tf-report-table',                // informes
        'table[data-resizable-columns]',
    ].join(', ');
    const NO_HANDLE = '.th-checkbox, .col-cb, .customer-open-col, [data-no-resize]';
    const MIN_WIDTH = 48;
    const KEY_STEP = 16;
    const STORAGE_PREFIX = 'tf:col-widths:v1:';
    const desktop = window.matchMedia('(min-width: 721px)');
    const sizedTables = new Set();
    let suppressClickUntil = 0;

    function headerCells(table) {
        const head = table.tHead;
        if (!head || head.rows.length !== 1) return null;
        const cells = Array.from(head.rows[0].cells);
        if (!cells.length || cells.some(cell => cell.colSpan > 1)) return null;
        return cells;
    }

    function label(cell) {
        return (cell.textContent || '').replace(/\s+/g, ' ').trim();
    }

    // La misma tabla en páginas distintas (p. ej. los tickets del perfil
    // propio y del de un cliente) comparte anchos porque tiene las mismas columnas.
    function storageKey(table, cells) {
        return STORAGE_PREFIX + (table.dataset.resizeKey || cells.map(label).join('|'));
    }

    function readWidths(key) {
        try {
            const saved = JSON.parse(localStorage.getItem(key) || 'null');
            return Array.isArray(saved) ? saved : null;
        } catch (error) {
            return null;
        }
    }

    function writeWidths(key, widths) {
        try {
            if (widths) localStorage.setItem(key, JSON.stringify(widths));
            else localStorage.removeItem(key);
        } catch (error) {
            // Sin almacenamiento el ajuste dura hasta recargar la página.
        }
    }

    function isVisible(cell) {
        return getComputedStyle(cell).display !== 'none';
    }

    function horizontalExtras(style) {
        return ['paddingLeft', 'paddingRight', 'borderLeftWidth', 'borderRightWidth']
            .reduce((sum, prop) => sum + (parseFloat(style[prop]) || 0), 0);
    }

    function visibleExtras(style) {
        return style.boxSizing === 'border-box' ? 0 : horizontalExtras(style);
    }

    // Lo que fija el usuario manda sobre el CSS de cada tabla (algunas limitan
    // columnas con max-width o fuerzan width con !important); por eso va en
    // línea con !important. El min-width del CSS se respeta como suelo en JS.
    function setImportant(element, prop, value) {
        element.style.setProperty(prop, value, 'important');
    }

    // Los anchos se manejan siempre como ancho visible de la celda; en las
    // celdas content-box el CSS necesita ese valor sin relleno ni borde.
    function setCellWidth(cell, width) {
        cell.dataset.tfWidth = Math.round(width);
        setImportant(cell, 'width', Math.max(0, Math.round(width - visibleExtras(getComputedStyle(cell)))) + 'px');
        setImportant(cell, 'min-width', '0');
        setImportant(cell, 'max-width', 'none');
    }

    // Se lee al preparar la tabla, antes de que el estilo en línea lo anule.
    function cssFloor(cell) {
        const style = getComputedStyle(cell);
        const min = parseFloat(style.minWidth) || 0;
        return min ? Math.ceil(min + visibleExtras(style)) : 0;
    }

    function cellWidth(cell) {
        return parseFloat(cell.dataset.tfWidth) || cell.getBoundingClientRect().width;
    }

    // El título de la cabecera no se corta: la columna nunca baja de lo que
    // ocupa en una línea (texto, icono de orden, relleno y borde).
    function titleWidth(cell) {
        const range = document.createRange();
        let left = Infinity;
        let right = -Infinity;
        cell.childNodes.forEach(node => {
            let rect = null;
            if (node.nodeType === Node.TEXT_NODE && node.textContent.trim()) {
                range.selectNodeContents(node);
                rect = range.getBoundingClientRect();
            } else if (node.nodeType === Node.ELEMENT_NODE && !node.classList.contains('tf-col-resizer')) {
                rect = node.getBoundingClientRect();
            }
            if (!rect || !rect.width) return;
            left = Math.min(left, rect.left);
            right = Math.max(right, rect.right);
        });
        const content = right > left ? right - left : 0;
        return Math.ceil(content + horizontalExtras(getComputedStyle(cell)));
    }

    function minWidth(cell) {
        return Math.max(MIN_WIDTH, titleWidth(cell), parseFloat(cell.dataset.tfMin) || 0);
    }

    // Con table-layout: fixed la tabla mide exactamente la suma de sus
    // columnas visibles; si no cabe, el contenedor ya tiene scroll horizontal.
    function syncTableWidth(table, cells) {
        const total = cells.reduce((sum, cell) => sum + (isVisible(cell) ? cellWidth(cell) : 0), 0);
        setImportant(table, 'width', Math.round(total) + 'px');
    }

    // Las columnas sin ancho guardado (p. ej. ocultas al guardarlo) conservan
    // el que tienen ahora; se mide antes de pasar a table-layout: fixed.
    function applyWidths(table, cells, widths) {
        const natural = cells.map(cell => isVisible(cell) ? cell.getBoundingClientRect().width : 0);
        table.classList.add('tf-cols-sized');
        cells.forEach((cell, index) => {
            let width = widths[index] || natural[index];
            if (isVisible(cell)) width = Math.max(width, cell.matches(NO_HANDLE) ? 0 : minWidth(cell));
            if (width) setCellWidth(cell, width);
        });
        setImportant(table, 'table-layout', 'fixed');
        setImportant(table, 'min-width', '0');
        sizedTables.add(table);
        syncTableWidth(table, cells);
    }

    function clearWidths(table, cells) {
        cells.forEach(cell => {
            ['width', 'min-width', 'max-width'].forEach(prop => cell.style.removeProperty(prop));
            delete cell.dataset.tfWidth;
        });
        ['table-layout', 'min-width', 'width'].forEach(prop => table.style.removeProperty(prop));
        table.classList.remove('tf-cols-sized');
        sizedTables.delete(table);
    }

    // Antes del primer arrastre se fijan los anchos actuales de todas las
    // columnas para que ninguna salte al pasar a table-layout: fixed.
    function freeze(table, cells) {
        if (!table.classList.contains('tf-cols-sized')) applyWidths(table, cells, []);
    }

    function currentWidths(cells) {
        return cells.map(cell => Math.round(parseFloat(cell.dataset.tfWidth)) || null);
    }

    function resizeTo(table, cells, cell, width, floor) {
        setCellWidth(cell, Math.max(floor, width));
        syncTableWidth(table, cells);
    }

    function reset(table, cells, key) {
        clearWidths(table, cells);
        writeWidths(key, null);
    }

    function addHandle(table, cells, cell, key) {
        const handle = document.createElement('span');
        handle.className = 'tf-col-resizer';
        handle.setAttribute('role', 'separator');
        handle.setAttribute('aria-orientation', 'vertical');
        handle.tabIndex = 0;
        handle.title = 'Arrastra para cambiar el ancho · doble clic para restablecer';
        handle.setAttribute('aria-label', `Ancho de la columna ${label(cell) || 'sin título'}`);

        handle.addEventListener('pointerdown', event => {
            if (event.button !== 0 || !desktop.matches) return;
            event.preventDefault();
            event.stopPropagation();
            freeze(table, cells);
            const startX = event.clientX;
            const startWidth = cellWidth(cell);
            const floor = minWidth(cell);
            let moved = false;
            handle.setPointerCapture(event.pointerId);
            handle.classList.add('is-active');
            document.body.classList.add('tf-col-resizing');

            const move = moveEvent => {
                const delta = moveEvent.clientX - startX;
                if (Math.abs(delta) > 2) moved = true;
                resizeTo(table, cells, cell, startWidth + delta, floor);
            };
            const end = () => {
                handle.removeEventListener('pointermove', move);
                handle.removeEventListener('pointerup', end);
                handle.removeEventListener('pointercancel', end);
                handle.classList.remove('is-active');
                document.body.classList.remove('tf-col-resizing');
                if (moved) {
                    suppressClickUntil = Date.now() + 400;
                    writeWidths(key, currentWidths(cells));
                }
            };
            handle.addEventListener('pointermove', move);
            handle.addEventListener('pointerup', end);
            handle.addEventListener('pointercancel', end);
        });

        // La cabecera suele ordenar al hacer clic: el asa no debe disparar el orden.
        handle.addEventListener('click', event => {
            event.preventDefault();
            event.stopPropagation();
        });
        handle.addEventListener('dblclick', event => {
            event.preventDefault();
            event.stopPropagation();
            reset(table, cells, key);
        });
        handle.addEventListener('keydown', event => {
            if (event.key !== 'ArrowLeft' && event.key !== 'ArrowRight') return;
            event.preventDefault();
            event.stopPropagation();
            if (!desktop.matches) return;
            freeze(table, cells);
            const step = event.key === 'ArrowRight' ? KEY_STEP : -KEY_STEP;
            resizeTo(table, cells, cell, cellWidth(cell) + step, minWidth(cell));
            writeWidths(key, currentWidths(cells));
        });

        // Las cabeceras fijas (sticky) ya posicionan el asa; no se les cambia el position.
        if (getComputedStyle(cell).position === 'static') cell.classList.add('tf-col-host');
        cell.appendChild(handle);
    }

    function enhance(table) {
        if (table.dataset.tfColumns) return;
        const cells = headerCells(table);
        if (!cells) return;
        table.dataset.tfColumns = '1';
        const key = storageKey(table, cells);
        table.__tfColumns = { cells, key };
        cells.forEach(cell => {
            cell.dataset.tfMin = cssFloor(cell);
            if (!cell.matches(NO_HANDLE)) addHandle(table, cells, cell, key);
        });
        const saved = readWidths(key);
        if (saved && desktop.matches) applyWidths(table, cells, saved);
    }

    function scan(root) {
        if (root.matches && root.matches(TABLES)) enhance(root);
        if (root.querySelectorAll) root.querySelectorAll(TABLES).forEach(enhance);
    }

    // Al cruzar el corte de móvil se devuelven las tablas a su CSS responsive
    // y al volver a escritorio se recuperan los anchos guardados.
    function onBreakpoint() {
        document.querySelectorAll('table[data-tf-columns]').forEach(table => {
            const { cells, key } = table.__tfColumns || {};
            if (!cells) return;
            if (!desktop.matches) {
                clearWidths(table, cells);
                return;
            }
            const saved = readWidths(key);
            if (saved) applyWidths(table, cells, saved);
        });
    }

    // Entre 721 y 1100 px la home oculta columnas: la tabla se reajusta a las visibles.
    let resizeFrame = 0;
    function onWindowResize() {
        if (resizeFrame) return;
        resizeFrame = requestAnimationFrame(() => {
            resizeFrame = 0;
            sizedTables.forEach(table => {
                if (!table.isConnected) {
                    sizedTables.delete(table);
                    return;
                }
                syncTableWidth(table, table.__tfColumns.cells);
            });
        });
    }

    // Fase de captura: corta el clic que el navegador emite al soltar un
    // arrastre antes de que llegue a los onclick de ordenación.
    window.addEventListener('click', event => {
        if (Date.now() < suppressClickUntil) {
            event.preventDefault();
            event.stopPropagation();
            suppressClickUntil = 0;
        }
    }, true);
    window.addEventListener('resize', onWindowResize);
    if (desktop.addEventListener) desktop.addEventListener('change', onBreakpoint);
    else desktop.addListener(onBreakpoint);

    let pending = false;
    const observer = new MutationObserver(() => {
        if (pending) return;
        pending = true;
        requestAnimationFrame(() => {
            pending = false;
            scan(document.body);
        });
    });

    function start() {
        scan(document.body);
        observer.observe(document.body, { childList: true, subtree: true });
    }
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start);
    else start();
})();
