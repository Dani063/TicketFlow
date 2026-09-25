/* Huevo de pascua: "Fuga de Tickets".
 *
 * Diez clics seguidos en el logo de la barra lateral y el agente cruza la
 * pantalla perseguido por sus tickets; vuelve por el otro lado con el cartel
 * de RESOLVED y son ellos los que huyen.
 *
 * El logo no lleva ni cursor de mano ni title ni efecto al pasar por encima:
 * es un huevo de pascua, no debe anunciarse. No se le toca el CSS.
 *
 * Todo se dibuja en un lienzo propio a pantalla completa, con pointer-events
 * a none, asi que no roba ni un clic: se puede seguir trabajando por debajo.
 * Al terminar, el lienzo se destruye.
 *
 * Re-entrante: base.html lo carga una vez por pagina completa, pero tabs.js
 * re-ejecuta scripts al cambiar de pestana, de ahi el guard de singleton.
 */
(function () {
    'use strict';
    if (window.__tfEggBound) return;
    window.__tfEggBound = true;

    var CLICKS = 10;          // clics seguidos que lo disparan
    var WINDOW_MS = 2000;     // margen maximo entre clic y clic
    var ACT = 4, GAP = 0.5;   // segundos de cada pasada y de la pausa

    /* ---------- Paleta: dos tonos por material, que es lo que da volumen ---------- */
    var P = {
        k: '#15121f', h: '#2e2015', H: '#4a3524', f: '#f6c9a0', g: '#d9a077',
        e: '#ffffff', p: '#1a1420', w: '#f4f6fc', W: '#d7dcea', s: '#3b4374',
        d: '#2a3056', l: '#4d568c', t: '#d8453a', T: '#a82f27', o: '#171522',
        b: '#ffd94a', B: '#c9a324', q: '#7ec8ff'
    };

    /* ---------- El agente: 22 x 30 ---------- */
    var MAN_W = 22, MAN_H = 30;

    // Huyendo: pelo de punta, ojos de tres por tres con la pupila diminuta
    // (el contraste blanco-pupila es lo que da el susto) y gota de sudor.
    var HEAD_PANIC = [
        '......h.hh..hh.h......',
        '......hhhhhhhhhh......',
        '.....hhHHhhhhhhhh.....',
        '.....hhffffffffhh.....',
        '....q.hffffffffh......',
        '...qq.feeefeeefg......',
        '......fepefepefg......',
        '......feeefeeefg......',
        '......ffffffffgg......',
        '......fffkkkkffg......',
        '......ffkkeekkfg......',
        '......fffkkkkffg......'
    ];
    // A la caza: cejas del color del pelo (en negro parecian un antifaz) y
    // sonrisa de comisuras, sin dientes, que se leia como calavera.
    var HEAD_HUNT = [
        '........hhhhhh........',
        '......hhhhhhhhhh......',
        '.....hhHHhhhhhhhh.....',
        '.....hhffffffffhh.....',
        '......hffffffffh......',
        '......fhhffhhffg......',
        '......feeffeeffg......',
        '......fepffepffg......',
        '......ffffffffgg......',
        '......ffkffffkfg......',
        '......fffkkkkffg......',
        '......ffffffffgg......'
    ];
    var TORSO = [
        '........fffff.........',
        '.......wwWtWww........',
        '....ssslwwtwwlsss.....',
        '...ssssdwwtwwdssss....',
        '...sssssdwtwdsssss....',
        '...ssssssdtdssssss....',
        '...sssssssTsssssss....',
        '...ssssssstssssbbs....',
        '...ssssssstssssbBs....',
        '....sssssssssssss.....',
        '....ssssssssssss......'
    ];
    // Zancada contenida: abiertas del todo parecia que hacia el spagat.
    var LEGS = [[
        '....ssssssssssss......',
        '....ssss...sssss......',
        '....sss.....ssss......',
        '...sss......ssss......',
        '...ss........sss......',
        '..ooo........ooo......',
        '......................'
    ], [
        '....ssssssssssss......',
        '....sssssssssss.......',
        '.....sss...ssss.......',
        '.....sss....sss.......',
        '....sss......sss......',
        '...ooo.......ooo......',
        '......................'
    ]];

    function man(hunting, step) {
        return (hunting ? HEAD_HUNT : HEAD_PANIC).concat(TORSO, LEGS[step]);
    }

    var CASE_PAL = { k: P.k, b: '#8a5a2b', c: '#6b4420', m: '#e8c34a', n: '#b8952e' };
    var CASE = [
        '.....kkkk.....',
        '....kmmmmk....',
        '....knnnnk....',
        'kkkkkkkkkkkkkk',
        'kbbbbbbbbbbbbk',
        'kbbbbbccbbbbbk',
        'kbbbbbccbbbbbk',
        'kbbbbbbbbbbbbk',
        'kcccccccccccck',
        'kkkkkkkkkkkkkk',
        '..............'
    ];

    /* ---------- Ticket fantasma: 18 x 18 ---------- */
    var TICKET_W = 18;
    function ticketRows(frameB, scared) {
        var skirt = frameB ? 'kkCCkkCCkkCCkkCCkk' : 'kCCkkCCkkCCkkCCkkk';
        var eyes = scared
            ? ['kCCeeeCCCCCeeeCCCk', 'kCCepeCCCCCepeCCCk', 'kCCeeeCCCCCeeeCCCk']
            : ['kCCeeeCCCCCeeeCCCk', 'kCCeppCCCCCeppCCCk', 'kCCeeeCCCCCeeeCCCk'];
        return [
            '....kkkkkkkkkk....',
            '..kkCCCCCCCCCCkk..',
            '.kCCCCCCCCCCCCCCk.',
            'kCCCCCCCCCCCCCCCCk',
            'kCCCCCCCCCCCCCCCCk',
            eyes[0], eyes[1], eyes[2],
            'kCCCCCCCCCCCCCCCCk',
            'kCkkkkkkkkkkkkkkCk',
            'kCCCCCCCCCCCCCCCCk',
            'kCCCCCCCCCCCCCCCCk',
            'kCCCCCCCCCCCCCCCCk',
            'kCCCCCCCCCCCCCCCCk',
            'kCCCCCCCCCCCCCCCCk',
            'kCCCCCCCCCCCCCCCCk',
            skirt,
            'k.kk..kk..kk..kk.k'
        ];
    }
    var TICKETS = [
        { body: '#ff5630', label: '#4021' },
        { body: '#ffc53d', label: '#4022' },
        { body: '#41c7e8', label: '#4023' },
        { body: '#a982f5', label: '#4024' }
    ];
    var SCARED = '#8fd0ff', SCARED_INK = '#17456e';
    var PIXEL_FONT = '"Press Start 2P", ui-monospace, "Courier New", monospace';

    /* ---------- Estado de la funcion en curso ---------- */
    var canvas = null, ctx = null, W = 0, H = 0, unit = 6;
    var running = false, elapsed = 0, lastTs = 0, raf = 0;

    function measure() {
        if (!canvas) return;
        var dpr = Math.min(window.devicePixelRatio || 1, 2);
        W = window.innerWidth;
        H = window.innerHeight;
        canvas.width = Math.round(W * dpr);
        canvas.height = Math.round(H * dpr);
        canvas.style.width = W + 'px';
        canvas.style.height = H + 'px';
        ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
        ctx.imageSmoothingEnabled = false;
        unit = Math.max(3, Math.min(9, Math.round(H / 80)));
    }

    function sprite(rows, pal, x, y, u, flip, width) {
        var w = width || rows[0].length;
        for (var r = 0; r < rows.length; r++) {
            var row = rows[r];
            for (var c = 0; c < w; c++) {
                var ch = row[c];
                if (!ch || ch === '.') continue;
                var col = pal[ch];
                if (!col) continue;
                var cx = flip ? w - 1 - c : c;
                ctx.fillStyle = col;
                ctx.fillRect(Math.round(x + cx * u), Math.round(y + r * u), u, u);
            }
        }
    }

    function px(x, y, w, h, color) {
        ctx.fillStyle = color;
        ctx.fillRect(Math.round(x), Math.round(y), Math.round(w), Math.round(h));
    }

    /* El cartel de estado, igual que la pastilla verde de la interfaz. */
    var PANEL_W = 34, PANEL_H = 12;
    function corner(r, h) {
        var dist = Math.min(r, h - 1 - r);
        return dist === 0 ? 3 : dist === 1 ? 1 : 0;
    }
    function resolvedPanel(x, y, u) {
        var r, c, k;
        for (r = 0; r < PANEL_H; r++) {
            k = corner(r, PANEL_H);
            for (c = k; c < PANEL_W - k; c++) px(x + c * u, y + r * u, u, u, '#36a05f');
        }
        for (r = 1; r < PANEL_H - 1; r++) {
            k = corner(r, PANEL_H) + 1;
            for (c = k; c < PANEL_W - k; c++) px(x + c * u, y + r * u, u, u, '#e9f8ef');
        }
        ctx.font = (u * 3) + 'px ' + PIXEL_FONT;
        ctx.textAlign = 'center';
        ctx.textBaseline = 'middle';
        ctx.fillStyle = '#1c7a44';
        ctx.fillText('RESOLVED', x + PANEL_W * u / 2, y + PANEL_H * u / 2);
    }

    function bubble(text, cx, cy, u) {
        ctx.font = (u * 3) + 'px ' + PIXEL_FONT;
        ctx.textAlign = 'center';
        ctx.textBaseline = 'middle';
        var w = ctx.measureText(text).width + u * 5;
        var h = u * 7;
        var x = cx - w / 2, y = cy - h;
        px(x - u, y - u, w + u * 2, h + u * 2, P.k);
        px(x, y, w, h, '#fffdf5');
        px(cx - u, y + h, u * 3, u, '#fffdf5');
        px(cx - u, y + h + u, u * 2, u, P.k);
        ctx.fillStyle = P.k;
        ctx.fillText(text, cx, y + h / 2);
    }

    function ticketAt(x, y, u, i, scared, frameB) {
        var conf = TICKETS[i];
        var pal = {
            k: P.k,
            C: scared ? SCARED : conf.body,
            e: '#ffffff',
            p: scared ? SCARED_INK : P.k
        };
        sprite(ticketRows(frameB, scared), pal, x, y, u, false, TICKET_W);
        ctx.font = (u * 2.4) + 'px ' + PIXEL_FONT;
        ctx.textAlign = 'center';
        ctx.textBaseline = 'middle';
        ctx.fillStyle = scared ? SCARED_INK : 'rgba(20,17,30,.8)';
        ctx.fillText(conf.label, x + u * 9, y + u * 13);
    }

    function dust(x, y, u, seed) {
        for (var i = 0; i < 3; i++) {
            var s = (seed + i * 37) % 100 / 100;
            px(x - u * (2 + i * 2.6) - s * u, y - s * u * 2, u * 1.5, u * 1.5, 'rgba(120,110,140,.40)');
        }
    }

    function gapFor(i, u) { return u * (34 + i * 22); }
    function tail(u) { return gapFor(3, u) + u * TICKET_W; }

    function frame(now) {
        if (!running) return;
        // El tiempo se acumula sumando diferencias entre fotogramas: restar de
        // performance.now() falla si el sello de rAF viene de otro origen.
        if (!lastTs) lastTs = now;
        var dt = (now - lastTs) / 1000;
        lastTs = now;
        if (!(dt >= 0) || dt > 0.25) dt = 1 / 60;
        elapsed += dt;

        var t = elapsed, total = ACT * 2 + GAP;
        if (t >= total) { finish(); return; }

        ctx.clearRect(0, 0, W, H);
        var u = unit, base = H * 0.52;
        var step = Math.floor(t * 9) % 2;
        var hunting = t > ACT + GAP;

        // El recorrido se mide sobre el grupo entero: midiendo solo el del
        // agente, la carrera acaba con los tickets todavia dentro del plano.
        var margin = u * 8;
        var enter = -u * MAN_W - margin;
        var exit = W + tail(u) + margin;
        var p, manX, dir;
        if (!hunting) { p = Math.min(1, t / ACT); manX = enter + (exit - enter) * p; dir = 1; }
        else { p = Math.min(1, (t - ACT - GAP) / ACT); manX = exit + (enter - exit) * p; dir = -1; }

        var fade = Math.min(1, Math.min(t, total - t) / 0.45);
        px(0, 0, W, H, 'rgba(18,12,32,' + (0.12 * fade).toFixed(3) + ')');

        var manY = base + Math.round(Math.sin(t * 17) * (u * 0.8));

        // Los tickets siempre a la izquierda: en la ida le pisan los talones,
        // en la vuelta huyen de el, que es quien persigue.
        for (var i = 3; i >= 0; i--) {
            var tx = manX - gapFor(i, u);
            var ty = base + u * 6 + Math.round(Math.sin(t * 13 + i * 1.3) * (u * 1.2));
            var tumble = hunting && i === 1 ? Math.round(Math.sin(t * 22) * u * 1.4) : 0;
            ticketAt(tx, ty + tumble, u, i, hunting, (Math.floor(t * 8) + i) % 2 === 0);
            if (hunting && i === 3) {
                ctx.font = (u * 3.4) + 'px ' + PIXEL_FONT;
                ctx.textAlign = 'center';
                ctx.fillStyle = SCARED_INK;
                ctx.fillText('!', tx + u * 9, ty - u * 2);
            }
        }

        if (hunting) {
            var lift = Math.round(Math.sin(t * 19) * (u * 1.2));
            // Tramos solapados, no cubos sueltos: si no, el brazo sale en escalera.
            for (var a = 0; a < 7; a++) {
                px(manX + u * (5 - a * 2.3), manY + u * (13 - a * 1.5) + lift * 0.4,
                   u * 3, u * 2.6, a % 2 ? P.d : P.s);
            }
            px(manX - u * 12, manY + u * 3.5 + lift * 0.4, u * 3, u * 3, P.f);
            resolvedPanel(manX - u * 26, manY - u * 9 + lift, u);
            if (t < ACT + GAP + 1.6) bubble('¡RESUELTO!', manX + u * 14, manY - u * 3, u);
        } else {
            var swing = Math.round(Math.sin(t * 17) * (u * 1.6));
            sprite(CASE, CASE_PAL, manX - u * 9, manY + u * 16 + swing, u, false);
            px(manX + u, manY + u * 16 + swing, u * 4, u * 3, P.s);
            for (var s2 = 0; s2 < 3; s2++) {
                var sp = (t * 3 + s2 * 0.33) % 1;
                px(manX - u * (3 + sp * 13), manY + u * (3 - sp * 6), u * 1.5, u * 1.5, P.q);
            }
            if (t < 1.4) bubble('¡AYUDA!', manX + u * 11, manY - u * 2, u);
        }

        sprite(man(hunting, step), P, manX, manY, u, dir < 0, MAN_W);
        dust(manX + (dir > 0 ? 0 : u * MAN_W), manY + u * MAN_H, u, Math.floor(t * 60));
        raf = requestAnimationFrame(frame);
    }

    function finish() {
        running = false;
        cancelAnimationFrame(raf);
        window.removeEventListener('resize', measure);
        document.removeEventListener('visibilitychange', onHidden);
        if (canvas && canvas.parentNode) canvas.parentNode.removeChild(canvas);
        canvas = null;
        ctx = null;
    }

    // La tipografia de pixel solo se descarga si alguien encuentra el huevo.
    function loadPixelFont() {
        if (document.getElementById('tf-egg-font')) return;
        var link = document.createElement('link');
        link.id = 'tf-egg-font';
        link.rel = 'stylesheet';
        link.href = 'https://fonts.googleapis.com/css2?family=Press+Start+2P&display=swap';
        document.head.appendChild(link);
    }

    // En segundo plano el navegador congela requestAnimationFrame: el lienzo
    // se quedaria colgado del body sin animarse y sin destruirse nunca.
    function onHidden() {
        if (document.hidden) finish();
    }

    function run() {
        if (running) return;
        loadPixelFont();
        canvas = document.createElement('canvas');
        canvas.setAttribute('aria-hidden', 'true');
        canvas.style.cssText = 'position:fixed;inset:0;z-index:99999;pointer-events:none;' +
            'image-rendering:pixelated;';
        document.body.appendChild(canvas);
        ctx = canvas.getContext('2d');
        measure();
        window.addEventListener('resize', measure);
        document.addEventListener('visibilitychange', onHidden);
        running = true;
        elapsed = 0;
        lastTs = 0;
        raf = requestAnimationFrame(frame);
    }

    /* ---------- Disparador ---------- */
    var hits = 0, lastHit = 0;

    document.addEventListener('click', function (event) {
        if (!event.target.closest('.sidebar-navigation img')) { hits = 0; return; }
        var now = Date.now();
        hits = (now - lastHit <= WINDOW_MS) ? hits + 1 : 1;
        lastHit = now;
        if (hits < CLICKS) return;
        hits = 0;
        if (document.hidden) return;
        if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;
        run();
    });
})();
