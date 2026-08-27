import re
from collections import defaultdict

from django.core.management.base import BaseCommand
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
    help = 'Audit email ticket duplication — read-only diagnostic'

    def handle(self, *args, **options):
        from app.models import Ticket, Comment

        total = Ticket.objects.count()
        with_conv = Ticket.objects.filter(email_conversation_id__isnull=False).count()
        without_conv = Ticket.objects.filter(email_conversation_id__isnull=True).count()
        email_ch = Ticket.objects.filter(channel='email').count()

        # --- Strategy A: by email_conversation_id ---
        dup_groups_a = (
            Ticket.objects
            .filter(email_conversation_id__isnull=False)
            .values('email_conversation_id', 'brand_id')
            .annotate(cnt=Count('id'))
            .filter(cnt__gt=1)
            .order_by('-cnt')
        )
        n_groups_a = dup_groups_a.count()
        tickets_del_a = sum(g['cnt'] - 1 for g in dup_groups_a)

        # --- Strategy B: by normalized subject + requester ---
        qs = Ticket.objects.filter(
            channel='email',
            email_conversation_id__isnull=True,
        ).values('subject', 'requester_id', 'brand_id').iterator(chunk_size=2000)

        buckets = defaultdict(int)
        for t in qs:
            normalized = _norm(t['subject'] or '')
            if len(normalized) >= 4:
                key = (normalized, t['requester_id'], t['brand_id'])
                buckets[key] += 1

        dup_buckets = {k: v for k, v in buckets.items() if v >= 2}
        n_groups_b = len(dup_buckets)
        tickets_del_b = sum(v - 1 for v in dup_buckets.values())

        # Top 10 for strategy B
        top_b = sorted(dup_buckets.items(), key=lambda x: -x[1])[:10]

        self.stdout.write('\n=== Email Dedup Audit ===\n')
        self.stdout.write(f'Total tickets:                         {total:,}')
        self.stdout.write(f'  channel=email:                       {email_ch:,}')
        self.stdout.write(f'  con email_conversation_id:           {with_conv:,}')
        self.stdout.write(f'  sin email_conversation_id:           {without_conv:,}')

        self.stdout.write(f'\n[A] Grupos duplicados por conversationId:  {n_groups_a:,}')
        self.stdout.write(f'    Tickets que se eliminarían:            {tickets_del_a:,}')

        self.stdout.write(f'\n[B] Grupos duplicados por subject+requester: {n_groups_b:,}')
        self.stdout.write(f'    Tickets que se eliminarían:              {tickets_del_b:,}')

        if top_b:
            self.stdout.write('\n  Top 10 grupos más grandes (B):')
            for (subj, req_id, brand_id), cnt in top_b:
                self.stdout.write(
                    f'    {cnt} tickets | brand={brand_id} req={req_id} | "{subj[:55]}"'
                )

        self.stdout.write(
            '\nEjecuta `python main.py email_dedup --dry-run` para ver el detalle.\n'
        )
