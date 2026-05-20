"""
Re-fetch html_body from Graph API for email tickets whose initial comment
has no html_body (created before the initial-comment fix).
"""
import django, os, re, html as _html, requests, sys
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'TicketFlow.settings')
django.setup()

from django.conf import settings
from django.utils.html import strip_tags
from app.models import Ticket, Comment, Brand
from app.tasks import _get_graph_token, _html_to_text, _BODY_RE

GRAPH_BASE = 'https://graph.microsoft.com/v1.0'

# Find tickets with email_message_id whose first email comment has no html_body
tickets_to_fix = (
    Ticket.objects
    .filter(channel='email')
    .exclude(email_message_id__isnull=True)
    .exclude(email_message_id='')
    .select_related('brand')
    .order_by('id')
)

print('Buscando tickets a reparar...', flush=True)

to_update = []
for t in tickets_to_fix.iterator(chunk_size=200):
    c = t.comment_set.filter(via_channel='email').order_by('id').first()
    if c and not c.html_body:
        to_update.append((t, c))

print(f'Tickets a re-fetch: {len(to_update)}', flush=True)
if not to_update:
    print('Nada que hacer.')
    sys.exit(0)

print('Obteniendo token Graph...', flush=True)
token = _get_graph_token()
headers = {'Authorization': 'Bearer ' + token}
print('Token OK', flush=True)

fixed = 0
errors = 0
skipped = 0

for i, (ticket, comment) in enumerate(to_update, 1):
    brand = ticket.brand
    if not brand or not brand.support_email:
        skipped += 1
        continue

    url = '%s/users/%s/messages/%s?$select=body' % (GRAPH_BASE, brand.support_email, ticket.email_message_id)
    try:
        resp = requests.get(url, headers=headers, timeout=20)
        if resp.status_code == 404:
            skipped += 1
            if i % 20 == 0 or i <= 5:
                print(f'[{i}/{len(to_update)}] #{ticket.id} → 404 (no encontrado)', flush=True)
            continue
        resp.raise_for_status()
        body = resp.json().get('body', {})
        raw = body.get('content', '')
        ct = body.get('contentType', 'html').lower()
        if ct == 'html':
            m = _BODY_RE.search(raw)
            html_inner = m.group(1).strip() if m else raw
            text = _html_to_text(html_inner)
            comment.html_body = html_inner
            comment.content = text or comment.content
            comment.save(update_fields=['html_body', 'content'])
        else:
            comment.content = _html.unescape(raw).strip()
            comment.save(update_fields=['content'])
        fixed += 1
        if i % 10 == 0 or i <= 5:
            print(f'[{i}/{len(to_update)}] #{ticket.id} ✓  (fixed={fixed}, errors={errors})', flush=True)
    except Exception as e:
        errors += 1
        print(f'[{i}/{len(to_update)}] #{ticket.id} ERROR: {e}', flush=True)

print(f'\nListo — Fixed: {fixed}  Skipped: {skipped}  Errors: {errors}')
