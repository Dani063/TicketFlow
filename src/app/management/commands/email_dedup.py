"""
Management command: email_dedup

Merges duplicate email tickets using two strategies:

  Strategy A — email_conversation_id (Graph thread ID, exact match)
  Strategy B — normalized subject + requester + brand (fuzzy, for tickets
               created before conversation_id was tracked)

For each duplicate group:
  - Primary = ticket with zendesk_id (if any), otherwise oldest created_at
  - All related objects are moved to the primary ticket
  - Duplicate tickets are hard-deleted

Usage:
  python main.py email_dedup                        # dry-run, both strategies
  python main.py email_dedup --dry-run              # same as above
  python main.py email_dedup --execute              # apply changes, both strategies
  python main.py email_dedup --execute --strategy b # only subject+requester
  python main.py email_dedup --execute --batch-size 100
"""

import re
from collections import defaultdict

from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import Count

_STRIP_PREFIX_RE = re.compile(r'^(re|fw|fwd|rv|resp):\s*', re.IGNORECASE)
_TICKET_TAG_RE = re.compile(r'\[ticket\s*#\d+\]', re.IGNORECASE)


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
            choices=['a', 'b', 'both'],
            default='both',
            help='a=conversationId, b=subject+requester, both=run A then B (default: both)',
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

    def handle(self, *args, **options):
        from app.models import Ticket, Comment, TicketEvent, Attachment, Notification, SatisfactionRating

        execute = options['execute']
        strategy = options['strategy']
        batch_size = options['batch_size']
        min_len = options['min_subject_len']
        mode = 'EXECUTE' if execute else 'DRY-RUN'

        self.stdout.write(f'\n=== email_dedup [{mode}] strategy={strategy} ===\n')

        totals = dict(groups=0, deleted=0, comments=0, events=0, attachments=0, notifications=0, ratings=0, errors=0)

        if strategy in ('a', 'both'):
            self.stdout.write('--- Estrategia A: email_conversation_id ---')
            groups_a = self._groups_by_conv_id()
            self.stdout.write(f'Grupos encontrados: {len(groups_a):,}')
            if groups_a:
                self._process_groups(groups_a, execute, batch_size, totals, label='conv_id')
            else:
                self.stdout.write('  Sin duplicados por conversationId.\n')

        if strategy in ('b', 'both'):
            self.stdout.write('--- Estrategia B: subject normalizado + requester ---')
            groups_b = self._groups_by_subject(min_len)
            self.stdout.write(f'Grupos encontrados: {len(groups_b):,}')
            if groups_b:
                # Show a few examples in dry-run so user can validate
                self._process_groups(groups_b, execute, batch_size, totals, label='subject', preview_examples=5)
            else:
                self.stdout.write('  Sin duplicados por subject+requester.\n')

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

    # ------------------------------------------------------------------
    # Group processor
    # ------------------------------------------------------------------

    def _process_groups(self, groups, execute, batch_size, totals, label='', preview_examples=0):
        from app.models import Ticket, Comment, TicketEvent, Attachment, Notification, SatisfactionRating

        total_groups = len(groups)
        shown = 0

        for batch_start in range(0, total_groups, batch_size):
            batch = groups[batch_start:batch_start + batch_size]

            for group in batch:
                # Normalize: group items may be dicts (strategy B) or model instances (strategy A)
                if isinstance(group[0], dict):
                    primary_id = group[0]['id']
                    dup_ids = [t['id'] for t in group[1:]]
                    primary_subject = group[0].get('subject', '')[:45]
                else:
                    primary_id = group[0].id
                    dup_ids = [t.id for t in group[1:]]
                    primary_subject = group[0].subject[:45]

                n_comments = Comment.objects.filter(ticket_id__in=dup_ids).count()

                if not execute:
                    if preview_examples == 0 or shown < preview_examples:
                        self.stdout.write(
                            f'  [DRY] #{primary_id} ({label}) ← {len(dup_ids)} dups '
                            f'| {n_comments} cmts | "{primary_subject}"'
                        )
                        shown += 1
                    elif shown == preview_examples:
                        self.stdout.write(f'  [DRY] ... ({total_groups - preview_examples} grupos más)')
                        shown += 1
                    totals['deleted'] += len(dup_ids)
                    totals['comments'] += n_comments
                    totals['groups'] += 1
                    continue

                try:
                    primary = Ticket.objects.get(id=primary_id)
                    with transaction.atomic():
                        Ticket.objects.filter(merged_into_id__in=dup_ids).update(merged_into=primary)
                        n_comments = Comment.objects.filter(ticket_id__in=dup_ids).update(ticket=primary)
                        n_events = TicketEvent.objects.filter(ticket_id__in=dup_ids).update(ticket=primary)
                        n_att = Attachment.objects.filter(ticket_id__in=dup_ids).update(ticket=primary)
                        n_notif = Notification.objects.filter(ticket_id__in=dup_ids).update(ticket=primary)
                        n_rat = SatisfactionRating.objects.filter(ticket_id__in=dup_ids).update(ticket=primary)
                        Ticket.objects.filter(id__in=dup_ids).delete()

                    totals['deleted'] += len(dup_ids)
                    totals['comments'] += n_comments
                    totals['events'] += n_events
                    totals['attachments'] += n_att
                    totals['notifications'] += n_notif
                    totals['ratings'] += n_rat
                    totals['groups'] += 1

                    self.stdout.write(
                        f'  Merged {len(dup_ids)} → #{primary_id} [{primary_subject}] '
                        f'| cmts={n_comments}'
                    )
                except Exception as exc:
                    totals['errors'] += 1
                    self.stderr.write(f'  ERROR primary=#{primary_id}: {exc}')

            processed = min(batch_start + batch_size, total_groups)
            if execute:
                self.stdout.write(f'  → Lote {batch_start + 1}–{processed} procesado')
