"""
Management command: email_dedup

Merges duplicate email tickets using three strategies:

  Strategy A — email_conversation_id (Graph thread ID, exact match)
  Strategy B — normalized subject + requester + brand (fuzzy, for tickets
               created before conversation_id was tracked)
  Strategy C — Alertas automáticas (ALARM:, AWS Notification, AutoFax, onTrace…):
               subject + brand dentro de ventanas de N días (default 15).
               Misma alerta 40 veces en una semana → 1 ticket + 39 comentarios.
               Ventana nueva = incidente nuevo.

For each duplicate group:
  - Primary = ticket with zendesk_id (if any), otherwise oldest created_at
  - All related objects are moved to the primary ticket
  - Duplicate tickets are hard-deleted

Usage:
  python main.py email_dedup                              # dry-run, all strategies
  python main.py email_dedup --dry-run                    # same as above
  python main.py email_dedup --execute                    # apply changes, all strategies
  python main.py email_dedup --execute --strategy c       # only ALARM dedup
  python main.py email_dedup --execute --strategy c --window-days 7
  python main.py email_dedup --execute --batch-size 100
"""

import os
import re
import datetime
import threading
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed

from django.core.management.base import BaseCommand
from django.db import connection, transaction
from django.db.models import Count

_STRIP_PREFIX_RE = re.compile(r'^(re|fw|fwd|rv|resp):\s*', re.IGNORECASE)
_TICKET_TAG_RE = re.compile(r'\[ticket\s*#\d+\]', re.IGNORECASE)

# Subjects automáticos que deben agruparse con ventana temporal (igual que ALARM)
# AWS Notification/RDS: subject genérico que cubre múltiples tipos de evento
_AUTO_SUBJECT_RE = re.compile(
    r'^alarm:|^aws \w+ message$|autofax alert|ontrace alert|'
    r'recordia proxy alert|mxtoolbox|plataforma ecomfax|rds notification',
    re.IGNORECASE,
)

_EPOCH_DATE = datetime.date(2015, 1, 1)


def _day_bucket(created_at, window_days):
    """Maps a datetime to a fixed N-day bucket index (0, 1, 2, …)."""
    if not created_at:
        return 0
    d = created_at.date() if hasattr(created_at, 'date') else created_at
    return (d - _EPOCH_DATE).days // window_days


def _norm(subject):
    s = _TICKET_TAG_RE.sub('', subject or '').strip()
    while True:
        c = _STRIP_PREFIX_RE.sub('', s).strip()
        if c == s:
            break
        s = c
    return s.lower()


class Command(BaseCommand):
    help = 'Merge duplicate email tickets (by conversationId or subject+requester)'

    def add_arguments(self, parser):
        parser.add_argument(
            '--execute',
            action='store_true',
            default=False,
            help='Apply changes (default is dry-run)',
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            default=False,
            help='Preview only — no changes (this is the default)',
        )
        parser.add_argument(
            '--strategy',
            choices=['a', 'b', 'c', 'both', 'all'],
            default='all',
            help=(
                'a=conversationId, b=subject+requester, c=ALARM windows, '
                'both=A+B, all=A+B+C (default: all)'
            ),
        )
        parser.add_argument(
            '--batch-size',
            type=int,
            default=200,
            help='Groups processed per transaction batch (default: 200)',
        )
        parser.add_argument(
            '--min-subject-len',
            type=int,
            default=4,
            help='Minimum normalized subject length to consider for strategy B (default: 4)',
        )
        parser.add_argument(
            '--window-days',
            type=int,
            default=15,
            metavar='N',
            help='Day window for strategy C ALARM grouping (default: 15)',
        )
        _cpus = os.cpu_count() or 4
        parser.add_argument(
            '--workers',
            type=int,
            default=_cpus,
            metavar='N',
            help=f'Threads paralelos para execute (default: {_cpus} = nucleos detectados)',
        )

    def handle(self, *args, **options):
        from app.models import Ticket, Comment, TicketEvent, Attachment, Notification, SatisfactionRating

        execute    = options['execute']
        strategy   = options['strategy']
        batch_size = options['batch_size']
        min_len    = options['min_subject_len']
        window_days = options['window_days']
        workers    = max(1, options['workers'])
        mode = 'EXECUTE' if execute else 'DRY-RUN'

        self.stdout.write(f'\n=== email_dedup [{mode}] strategy={strategy} ===\n')

        totals = dict(groups=0, deleted=0, comments=0, events=0, attachments=0, notifications=0, ratings=0, errors=0)

        if strategy in ('a', 'both', 'all'):
            self.stdout.write('--- Estrategia A: email_conversation_id ---')
            groups_a = self._groups_by_conv_id()
            self.stdout.write(f'Grupos encontrados: {len(groups_a):,}')
            if groups_a:
                self._process_groups(groups_a, execute, batch_size, totals, label='conv_id', workers=workers)
            else:
                self.stdout.write('  Sin duplicados por conversationId.\n')

        if strategy in ('b', 'both', 'all'):
            self.stdout.write('--- Estrategia B: subject normalizado + requester ---')
            groups_b = self._groups_by_subject(min_len)
            self.stdout.write(f'Grupos encontrados: {len(groups_b):,}')
            if groups_b:
                self._process_groups(groups_b, execute, batch_size, totals, label='subject', preview_examples=5, workers=workers)
            else:
                self.stdout.write('  Sin duplicados por subject+requester.\n')

        if strategy in ('c', 'all'):
            self.stdout.write(f'--- Estrategia C: alertas automáticas — ventanas de {window_days} días ---')
            groups_c = self._groups_by_alarm_window(window_days)
            self.stdout.write(f'Grupos encontrados: {len(groups_c):,}')
            if groups_c:
                self._process_groups(groups_c, execute, batch_size, totals, label='alarm', preview_examples=10, workers=workers)
            else:
                self.stdout.write('  Sin duplicados ALARM en las ventanas configuradas.\n')

        self.stdout.write('\n=== Resumen total ===')
        self.stdout.write(f'Grupos procesados:     {totals["groups"]:,}')
        self.stdout.write(f'Tickets eliminados:    {totals["deleted"]:,}')
        self.stdout.write(f'Comentarios movidos:   {totals["comments"]:,}')
        self.stdout.write(f'Eventos movidos:       {totals["events"]:,}')
        self.stdout.write(f'Adjuntos movidos:      {totals["attachments"]:,}')
        self.stdout.write(f'Notificaciones mov.:   {totals["notifications"]:,}')
        self.stdout.write(f'Valoraciones mov.:     {totals["ratings"]:,}')
        if totals['errors']:
            self.stdout.write(self.style.ERROR(f'Errores:               {totals["errors"]}'))

        if not execute:
            self.stdout.write(self.style.WARNING(
                '\n[DRY-RUN] Ningún cambio aplicado. Usa --execute para aplicar.\n'
            ))
        else:
            self.stdout.write(self.style.SUCCESS('\nLimpieza completada.\n'))

    # ------------------------------------------------------------------
    # Group builders
    # ------------------------------------------------------------------

    def _groups_by_conv_id(self):
        """Returns list of ticket-id lists grouped by email_conversation_id."""
        from app.models import Ticket
        raw = (
            Ticket.objects
            .filter(email_conversation_id__isnull=False)
            .values('email_conversation_id', 'brand_id')
            .annotate(cnt=Count('id'))
            .filter(cnt__gt=1)
            .order_by('email_conversation_id')
        )
        groups = []
        for g in raw:
            tickets = list(
                Ticket.objects
                .filter(email_conversation_id=g['email_conversation_id'], brand_id=g['brand_id'])
                .order_by('created_at')
            )
            tickets.sort(key=lambda t: (t.zendesk_id is None, t.created_at))
            if len(tickets) >= 2:
                groups.append(tickets)
        return groups

    def _groups_by_subject(self, min_len):
        """Returns list of ticket-id lists grouped by normalized subject + requester + brand."""
        from app.models import Ticket

        # Load all email tickets without conversation_id (the historical backlog)
        qs = Ticket.objects.filter(
            channel='email',
            email_conversation_id__isnull=True,
            is_deleted=False,
        ).exclude(
            subject__iregex=(
                r'^alarm:|^aws \w+ message$|autofax alert|ontrace alert|'
                r'recordia proxy alert|mxtoolbox|plataforma ecomfax|rds notification'
            )
        ).values('id', 'subject', 'requester_id', 'brand_id', 'zendesk_id', 'created_at').order_by('created_at')

        buckets = defaultdict(list)
        for t in qs.iterator(chunk_size=2000):
            normalized = _norm(t['subject'] or '')
            if len(normalized) < min_len:
                continue
            key = (normalized, t['requester_id'], t['brand_id'])
            buckets[key].append(t)

        # Build groups with 2+ tickets, sorted primary-first
        groups = []
        for tickets_data in buckets.values():
            if len(tickets_data) < 2:
                continue
            tickets_data.sort(key=lambda t: (t['zendesk_id'] is None, t['created_at']))
            groups.append(tickets_data)

        return groups

    def _groups_by_alarm_window(self, window_days):
        """
        Groups tickets whose subject starts with 'ALARM:' by (normalized_subject, brand, N-day bucket).
        Same alarm firing repeatedly within the window → merged into the oldest ticket.
        A new window = a new incident, kept separate.
        """
        from app.models import Ticket

        qs = Ticket.objects.filter(
            is_deleted=False,
            subject__iregex=(
                r'^alarm:|^aws \w+ message$|autofax alert|ontrace alert|'
                r'recordia proxy alert|mxtoolbox|plataforma ecomfax|rds notification'
            ),
        ).values('id', 'subject', 'brand_id', 'zendesk_id', 'created_at').order_by('created_at')

        buckets = defaultdict(list)
        for t in qs.iterator(chunk_size=2000):
            normalized = _norm(t['subject'] or '')
            if not normalized:
                continue
            bucket = _day_bucket(t['created_at'], window_days)
            key = (normalized, t['brand_id'], bucket)
            buckets[key].append(t)

        groups = []
        for tickets_data in buckets.values():
            if len(tickets_data) < 2:
                continue
            tickets_data.sort(key=lambda t: (t['zendesk_id'] is None, t['created_at']))
            groups.append(tickets_data)

        return groups

    # ------------------------------------------------------------------
    # Group processor
    # ------------------------------------------------------------------

    def _process_groups(self, groups, execute, batch_size, totals, label='', preview_examples=0, workers=4):
        from app.models import Ticket, Comment, TicketEvent, Attachment, Notification, SatisfactionRating

        total_groups = len(groups)
        lock = threading.Lock()
        counter = [0]
        shown = [0]

        def _normalize(group):
            if isinstance(group[0], dict):
                return group[0]['id'], [t['id'] for t in group[1:]], group[0].get('subject', '')[:45]
            return group[0].id, [t.id for t in group[1:]], group[0].subject[:45]

        # DRY-RUN: secuencial (output ordenado)
        if not execute:
            for group in groups:
                primary_id, dup_ids, primary_subject = _normalize(group)
                n_comments = Comment.objects.filter(ticket_id__in=dup_ids).count()
                if preview_examples == 0 or shown[0] < preview_examples:
                    self.stdout.write(
                        f'  [DRY] #{primary_id} ({label}) <- {len(dup_ids)} dups '
                        f'| {n_comments} cmts | "{primary_subject}"'
                    )
                    shown[0] += 1
                elif shown[0] == preview_examples:
                    self.stdout.write(f'  [DRY] ... ({total_groups - preview_examples} grupos mas)')
                    shown[0] += 1
                totals['deleted'] += len(dup_ids)
                totals['comments'] += n_comments
                totals['groups'] += 1
            return

        # EXECUTE: paralelo — cada grupo en su propio thread y transacción
        self.stdout.write(f'  Procesando {total_groups} grupos con {workers} workers...')

        def _process_one(group):
            from django.db.models import Max
            primary_id, dup_ids, primary_subject = _normalize(group)
            try:
                primary = Ticket.objects.get(id=primary_id)
                with transaction.atomic():
                    Ticket.objects.filter(merged_into_id__in=dup_ids).update(merged_into=primary)
                    n_c = Comment.objects.filter(ticket_id__in=dup_ids).update(ticket=primary)
                    n_e = TicketEvent.objects.filter(ticket_id__in=dup_ids).update(ticket=primary)
                    n_a = Attachment.objects.filter(ticket_id__in=dup_ids).update(ticket=primary)
                    n_n = Notification.objects.filter(ticket_id__in=dup_ids).update(ticket=primary)
                    n_r = SatisfactionRating.objects.filter(ticket_id__in=dup_ids).update(ticket=primary)
                    Ticket.objects.filter(id__in=dup_ids).delete()
                    # updated_at = fecha del comentario más reciente entre todos los fusionados
                    latest = Comment.objects.filter(ticket_id=primary_id).aggregate(m=Max('created_at'))['m']
                    if latest:
                        Ticket.objects.filter(pk=primary_id).update(updated_at=latest)
                return ('ok', len(dup_ids), n_c, n_e, n_a, n_n, n_r)
            except Exception as exc:
                return ('error', primary_id, str(exc))
            finally:
                connection.close()

        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {executor.submit(_process_one, g): g for g in groups}
            for future in as_completed(futures):
                result = future.result()
                with lock:
                    counter[0] += 1
                    if result[0] == 'ok':
                        _, deleted, n_c, n_e, n_a, n_n, n_r = result
                        totals['deleted']       += deleted
                        totals['comments']      += n_c
                        totals['events']        += n_e
                        totals['attachments']   += n_a
                        totals['notifications'] += n_n
                        totals['ratings']       += n_r
                        totals['groups']        += 1
                    else:
                        _, pid, exc_msg = result
                        totals['errors'] += 1
                        self.stderr.write(f'  ERROR #{pid}: {exc_msg}')
                    if counter[0] % 200 == 0 or counter[0] == total_groups:
                        self.stdout.write(
                            f'  [{counter[0]}/{total_groups}] -{totals["deleted"]} tickets, '
                            f'{totals["comments"]} cmts'
                            + (f', {totals["errors"]} errores' if totals['errors'] else '')
                        )
