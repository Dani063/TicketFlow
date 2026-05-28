import re
import html as _html
import logging
import requests
from datetime import timedelta
from django.conf import settings
from django.utils import timezone
from django.utils.html import strip_tags
from celery import shared_task

logger = logging.getLogger(__name__)

GRAPH_BASE = 'https://graph.microsoft.com/v1.0'
_TICKET_REF_RE = re.compile(r'\[Ticket #(\d+)\]', re.IGNORECASE)


# ---------------------------------------------------------------------------
# Microsoft Graph helpers
# ---------------------------------------------------------------------------

def _get_graph_token():
    import msal
    client = msal.ConfidentialClientApplication(
        settings.AZURE_CLIENT_ID,
        authority=f'https://login.microsoftonline.com/{settings.AZURE_TENANT_ID}',
        client_credential=settings.AZURE_CLIENT_SECRET,
    )
    result = client.acquire_token_for_client(scopes=['https://graph.microsoft.com/.default'])
    if 'access_token' not in result:
        raise RuntimeError(f"Graph token error: {result.get('error_description', result)}")
    return result['access_token']


def _get_or_create_requester(email, name):
    from app.models import User, Role
    email = email.strip().lower()
    user, created = User.objects.get_or_create(
        email=email,
        defaults={'name': name.strip() if name else email.split('@')[0]},
    )
    if created:
        user.set_unusable_password()
        try:
            role, _ = Role.objects.get_or_create(role_name='End user')
            user.role = role
        except Exception:
            pass
        user.save()
    return user


def _find_ticket_for_message(subject, conversation_id, brand, requester=None):
    from app.models import Ticket
    # Tier 1: Graph conversationId — most reliable, groups the whole email thread
    if conversation_id:
        ticket = Ticket.objects.filter(email_conversation_id=conversation_id, brand=brand).first()
        if ticket:
            return ticket
    # Tier 2: explicit [Ticket #N] in subject (system reply-to or manual reference)
    m = _TICKET_REF_RE.search(subject or '')
    if m:
        num = int(m.group(1))
        for lookup in ({'id': num, 'brand': brand}, {'zendesk_id': num, 'brand': brand}):
            try:
                return Ticket.objects.get(**lookup)
            except Ticket.DoesNotExist:
                pass
    # Tier 3: normalized subject + same requester + open/pending ticket (last 30 days)
    # Handles replies from sources that don't preserve conversationId (e.g. SES, forwards)
    if requester and not conversation_id:
        normalized = _normalize_subject(subject or '')
        if len(normalized) >= 4:
            cutoff = timezone.now() - timedelta(days=7)
            ticket = (
                Ticket.objects
                .filter(
                    brand=brand,
                    requester=requester,
                    status__in=('open', 'pending'),
                    created_at__gte=cutoff,
                    email_conversation_id__isnull=True,
                )
                .filter(subject__icontains=normalized[:60])
                .order_by('-created_at')
                .first()
            )
            if ticket:
                return ticket
    return None


_STRIP_PREFIX_RE = re.compile(r'^(re|fw|fwd|rv|resp):\s*', re.IGNORECASE)
_TICKET_TAG_RE = re.compile(r'\[ticket\s*#\d+\]', re.IGNORECASE)


def _normalize_subject(subject):
    """Strip ticket tags and reply/forward prefixes for fuzzy thread matching."""
    s = _TICKET_TAG_RE.sub('', subject).strip()
    while True:
        cleaned = _STRIP_PREFIX_RE.sub('', s).strip()
        if cleaned == s:
            break
        s = cleaned
    return s.lower()


_BODY_RE = re.compile(r'<body[^>]*>(.*?)</body>', re.DOTALL | re.IGNORECASE)
# Whitelist of tags that indicate actual HTML formatting (not email addresses like <user@host>)
_FORMAT_TAG_RE = re.compile(
    r'<(?:p|div|br|table|span|strong|em|b|i|u|ul|ol|li|h[1-6]|a|blockquote|font|pre)\b',
    re.IGNORECASE,
)


def _html_to_text(html_inner):
    """Convert HTML to readable plain text preserving paragraph/line structure."""
    t = re.sub(r'<br\s*/?>', '\n', html_inner, flags=re.IGNORECASE)
    t = re.sub(r'</(?:p|div|tr|li|h[1-6])>', '\n', t, flags=re.IGNORECASE)
    return _html.unescape(strip_tags(t)).strip()


def _extract_body(message):
    body = message.get('body', {})
    raw = body.get('content', '')
    if body.get('contentType', 'html').lower() == 'html':
        m = _BODY_RE.search(raw)
        html_inner = m.group(1).strip() if m else raw
        return _html_to_text(html_inner), html_inner
    return _html.unescape(raw).strip(), ''


def _process_message(message, brand):
    from app.models import Ticket, Comment

    msg_id = message['id']
    conversation_id = message.get('conversationId') or ''
    subject = message.get('subject') or '(sin asunto)'
    sender = message.get('from', {}).get('emailAddress', {})
    sender_email = sender.get('address', '').strip().lower()
    sender_name = sender.get('name', '')
    text_body, html_body = _extract_body(message)
    if html_body:
        from app.views import _sanitize_email_html
        html_body = _sanitize_email_html(html_body)

    if not sender_email:
        return 'skip_no_sender'

    # Dedup — skip if already processed
    if Comment.objects.filter(email_message_id=msg_id).exists():
        return 'skip_dup'
    if Ticket.objects.filter(email_message_id=msg_id).exists():
        return 'skip_dup'

    requester = _get_or_create_requester(sender_email, sender_name)
    ticket = _find_ticket_for_message(subject, conversation_id, brand, requester=requester)

    if ticket:
        Comment.objects.create(
            ticket=ticket,
            user=requester,
            content=text_body or html_body,
            html_body=html_body or None,
            via_channel='email',
            is_public=True,
            email_message_id=msg_id,
        )
        if ticket.status in ('pending', 'resolved'):
            ticket.status = 'open'
            ticket.save(update_fields=['status'])
        return 'comment_added'
    else:
        ticket = Ticket.objects.create(
            subject=subject,
            description=text_body or html_body or '',
            requester=requester,
            created_by=requester,
            brand=brand,
            channel='email',
            status='open',
            email_message_id=msg_id,
            email_conversation_id=conversation_id or None,
        )
        Comment.objects.create(
            ticket=ticket,
            user=requester,
            content=text_body or html_body or '',
            html_body=html_body or None,
            via_channel='email',
            is_public=True,
        )
        return 'ticket_created'


# ---------------------------------------------------------------------------
# Celery tasks
# ---------------------------------------------------------------------------

@shared_task(name='app.tasks.poll_m365_mailboxes')
def poll_m365_mailboxes():
    from app.models import Brand

    brands = Brand.objects.filter(mailbox_type='m365').exclude(support_email='')
    if not brands.exists():
        logger.info('poll_m365: no brands with M365 mailbox configured')
        return 'no_brands'

    try:
        token = _get_graph_token()
    except Exception as exc:
        logger.error('poll_m365: token acquisition failed: %s', exc)
        return

    headers = {
        'Authorization': f'Bearer {token}',
        'Content-Type': 'application/json',
    }

    for brand in brands:
        mailbox = brand.support_email
        url = (
            f'{GRAPH_BASE}/users/{mailbox}/mailFolders/Inbox/messages'
            '?$filter=isRead eq false'
            '&$select=id,subject,from,body,receivedDateTime,conversationId'
            '&$top=50'
            '&$orderby=receivedDateTime asc'
        )
        try:
            resp = requests.get(url, headers=headers, timeout=30)
            resp.raise_for_status()
        except Exception as exc:
            logger.error('poll_m365 [%s]: Graph read error: %s', mailbox, exc)
            continue

        for msg in resp.json().get('value', []):
            msg_id = msg['id']
            try:
                result = _process_message(msg, brand)
                logger.info('poll_m365 [%s] %s… → %s', mailbox, msg_id[:16], result)
            except Exception as exc:
                logger.error('poll_m365 [%s] %s… error: %s', mailbox, msg_id[:16], exc)
                continue

            # Mark as read (best-effort; even on skip_dup so we don't keep seeing it)
            try:
                requests.patch(
                    f'{GRAPH_BASE}/users/{mailbox}/messages/{msg_id}',
                    headers=headers,
                    json={'isRead': True},
                    timeout=10,
                )
            except Exception as exc:
                logger.warning('poll_m365 [%s] mark-read failed %s…: %s', mailbox, msg_id[:16], exc)


