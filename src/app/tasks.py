import logging
import requests
from django.conf import settings
from celery import shared_task
from app.services.assignment import AssignmentService
from app.services.email_ingestion import EmailIngestionService
from app.services.sla import SLAService

logger = logging.getLogger(__name__)

GRAPH_BASE = 'https://graph.microsoft.com/v1.0'


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


def _process_message(message, brand):
    return EmailIngestionService.process_message(message, brand)


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


@shared_task(name='app.tasks.auto_assign_unassigned_tickets')
def auto_assign_unassigned_tickets():
    """Auto-assign unassigned tickets using database assignment rules."""
    assigned_count = AssignmentService.auto_assign_unassigned()
    return f'assigned_count: {assigned_count}'


@shared_task(name='app.tasks.mark_sla_breaches')
def mark_sla_breaches():
    breached = SLAService.mark_breaches()
    return f'breached_count: {len(breached)}'
