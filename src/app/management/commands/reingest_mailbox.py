"""Recupera correo de un buzón M365 que no llegó a ingerirse (backfill).

Lista la carpeta en la ventana pedida y registra en InboundEmailLog lo que no
conste, como ``source=backfill``. **No procesa**: lo hace el barrido normal en
sus vueltas, después del correo en vivo y sin enviar confirmaciones al
cliente. Así el backfill no compite con la recogida ni necesita su lease.

El correo automático (alarmas, rebotes, fuera de oficina…) se descarta por
defecto y queda como ``skipped`` con la regla que lo decidió; el dudoso
(``review``) también, salvo ``--include-review``. Todo descarte se puede
revertir desde el admin (InboundEmailLog → Reprocesar).

Uso habitual:

    python main.py reingest_mailbox --since 2026-09-29T10:00:00Z --dry-run
    python main.py reingest_mailbox --since 2026-09-29T10:00:00Z
"""

from datetime import timezone as dt_timezone

import requests
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils.dateparse import parse_datetime

from app.models import Brand, InboundEmailLog
from app.services import msgraph_mail
from app.services.inbound_classifier import classify
from app.services.mailbox_sweep import MailboxSweep, get_cursor, graph_headers, resolve_targets
from app.services.msgraph import get_graph_token

GROUPS = (
    ('new', 'Se recuperarán (correo humano)'),
    ('review', 'Dudosos (se descartan salvo --include-review)'),
    ('excluded', 'Automáticos (se descartan)'),
    ('logged_no_ticket', 'Registrados antes sin ticket asociado'),
    ('already_ingested', 'Ya ingeridos'),
)


def _instant(value, name):
    parsed = parse_datetime(value) if value else None
    if parsed is None:
        raise CommandError(f'{name} debe ser una fecha ISO 8601, p. ej. 2026-09-29T10:00:00Z')
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt_timezone.utc)
    return parsed


class Command(BaseCommand):
    help = 'Registra para su ingesta el correo de un buzón M365 que no llegó a entrar.'

    def add_arguments(self, parser):
        parser.add_argument('--since', required=True, help='Inicio de la ventana (ISO 8601, UTC).')
        parser.add_argument('--until', help='Fin de la ventana. Por defecto, donde arrancó el barrido (deep_floor).')
        parser.add_argument('--dry-run', action='store_true', help='Solo informa; no escribe nada.')
        parser.add_argument('--include-review', action='store_true', help='Recupera también los dudosos.')
        parser.add_argument('--include-automated', action='store_true',
                            help='No aplica el clasificador: recupera todo.')
        parser.add_argument('--lines', type=int, default=300, help='Máximo de líneas por grupo en el informe.')

    def handle(self, *args, **options):
        since = _instant(options['since'], '--since')
        environment = getattr(settings, 'ENVIRONMENT_NAME', 'local')
        brands = list(Brand.objects.filter(mailbox_type='m365').exclude(support_email='').order_by('id'))
        if not brands:
            raise CommandError('No hay marcas con buzón M365.')
        headers = graph_headers(get_graph_token())
        targets, _ = resolve_targets(brands, headers)
        own = [b.support_email for b in Brand.objects.exclude(support_email='')]
        mode = 'DRY-RUN (no escribe nada)' if options['dry_run'] else 'EJECUCIÓN'
        self.stdout.write(f'== reingest_mailbox · {environment} · {mode}')

        for target in targets:
            cursor = get_cursor(target, environment, allow_create=False)
            if cursor is None or not cursor.deep_floor:
                raise CommandError(
                    f'{target["mailbox"]}: el barrido no ha arrancado en este entorno; actívalo antes '
                    'para que el backfill sepa dónde empieza la recogida en vivo.'
                )
            until = _instant(options['until'], '--until') if options['until'] else cursor.deep_floor
            if until <= since:
                raise CommandError(f'Ventana vacía: {since.isoformat()} → {until.isoformat()}')

            classifier = None if options['include_automated'] else (
                lambda msg, known_conversation=False: classify(
                    msg, known_conversation=known_conversation, own_addresses=own,
                )
            )

            report = {}
            sweep = MailboxSweep(cursor, target, headers, environment)
            fields = msgraph_mail.LIST_FIELDS + ',internetMessageHeaders'
            try:
                complete, _ = sweep.discover(
                    since, until, source=InboundEmailLog.SOURCE_BACKFILL, classifier=classifier,
                    include_review=options['include_review'], dry_run=options['dry_run'],
                    report=report, max_pages=10000, fields=fields,
                )
            except requests.HTTPError as exc:
                if exc.response is None or exc.response.status_code != 400:
                    raise
                # Si Graph no admite cabeceras en el listado, se clasifica solo
                # por remitente y asunto (cubre alarmas, rebotes y fuera de oficina).
                self.stderr.write('Graph no devuelve cabeceras en el listado; se clasifica sin ellas.')
                report = {}
                complete, _ = sweep.discover(
                    since, until, source=InboundEmailLog.SOURCE_BACKFILL, classifier=classifier,
                    include_review=options['include_review'], dry_run=options['dry_run'],
                    report=report, max_pages=10000,
                )
            self._print(target, since, until, report, sweep, complete, options)

    def _print(self, target, since, until, report, sweep, complete, options):
        self.stdout.write(
            f'\n## {target["mailbox"]} · {since.isoformat()} → {until.isoformat()} · '
            f'{sweep.stats["listed"]} mensajes en la ventana'
            + ('' if complete else ' · INCOMPLETO: repetir para continuar')
        )
        for group, title in GROUPS:
            rows = report.get(group, [])
            self.stdout.write(f'\n### {title}: {len(rows)}')
            if group == 'already_ingested':
                continue
            for row in sorted(rows, key=lambda r: r['received'] or '')[:options['lines']]:
                extra = f' [{row["rule"]}]' if row.get('rule') else ''
                self.stdout.write(f'  {row["received"]} | {row["sender"][:45]:45} | {row["subject"]}{extra}')
            if len(rows) > options['lines']:
                self.stdout.write(f'  … y {len(rows) - options["lines"]} más')
        adopted = sweep.stats['adopted_legacy']
        if adopted:
            self.stdout.write(f'\nFilas antiguas sin terminar adoptadas para reintento: {adopted}')
        if not options['dry_run']:
            self.stdout.write('\nRegistrado. El barrido los procesará en las próximas vueltas '
                              '(primero el correo en vivo). Seguimiento: admin → InboundEmailLog, source=backfill.')
