"""Acceso a los buzones M365 por Microsoft Graph para la ingesta de correo.

Solo operaciones de lectura idempotentes (más el marcado como leído, que es
cortesía). Nada aquí guarda estado en Graph: el barrido lista la carpeta por
fecha de recepción y la verdad de qué se ha ingerido vive en la BD. Ver
app/services/mailbox_sweep.py.
"""

import logging
import os
from datetime import timezone as dt_timezone
from urllib.parse import quote

import requests
from django.conf import settings

from app.services.msgraph import GRAPH_BASE

logger = logging.getLogger(__name__)

# Metadatos del listado: lo justo para deduplicar y clasificar. El cuerpo y las
# cabeceras solo se piden para los mensajes que de verdad hay que ingerir.
LIST_FIELDS = (
    'id,receivedDateTime,lastModifiedDateTime,internetMessageId,subject,from,'
    'conversationId,hasAttachments'
)
MESSAGE_FIELDS = (
    'id,subject,from,body,receivedDateTime,lastModifiedDateTime,conversationId,'
    'internetMessageId,internetMessageHeaders,hasAttachments,ccRecipients'
)


class GraphThrottled(Exception):
    """Graph ha pedido esperar (429/503/504). No es culpa del mensaje."""

    def __init__(self, status, retry_after):
        super().__init__(f'Graph HTTP {status}; reintentar en {retry_after}s')
        self.status = status
        self.retry_after = retry_after


class GraphNotFound(Exception):
    """El recurso ya no existe con ese id (borrado o movido de carpeta)."""


def _user_path(mailbox):
    return f'{GRAPH_BASE}/users/{quote(mailbox, safe="@")}'


def _request(method, url, headers, timeout=30, **kwargs):
    response = requests.request(method, url, headers=headers, timeout=timeout, **kwargs)
    if response.status_code in (429, 503, 504):
        try:
            retry_after = int(response.headers.get('Retry-After') or 30)
        except (TypeError, ValueError):
            retry_after = 30
        raise GraphThrottled(response.status_code, retry_after)
    if response.status_code == 404:
        raise GraphNotFound(url)
    response.raise_for_status()
    return response


def graph_timestamp(value):
    """Instante en el formato que espera $filter (UTC, al segundo, más inclusivo)."""
    return value.astimezone(dt_timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def inbox_folder_id(mailbox, headers):
    """Id de la carpeta Inbox *física* que hay detrás de una dirección (o alias)."""
    response = _request('GET', f'{_user_path(mailbox)}/mailFolders/Inbox', headers,
                        params={'$select': 'id'})
    return response.json()['id']


# Carpetas que nunca se barren (#21419). Las primeras son imprescindibles: barrer
# Enviados convertiría cada respuesta propia en un ticket. Si no se pueden
# resolver, la vuelta se queda solo con Inbox.
REQUIRED_EXCLUDED_FOLDERS = ('sentitems', 'drafts', 'outbox', 'deleteditems', 'junkemail')
# Estas pueden no existir en un buzón dado; si no responden, no hay nada que excluir.
OPTIONAL_EXCLUDED_FOLDERS = (
    'conversationhistory', 'syncissues', 'scheduled', 'searchfolders', 'clutter',
    'recoverableitemsdeletions',
)


def _well_known_folder_id(mailbox, name, headers):
    response = _request('GET', f'{_user_path(mailbox)}/mailFolders/{name}', headers,
                        params={'$select': 'id'})
    return response.json()['id']


def _list_mail_folders(mailbox, headers, excluded_ids):
    """Todas las carpetas de correo visibles, recorriendo subcarpetas.

    Las excluidas se saltan con todo lo que cuelga de ellas (una subcarpeta de
    la papelera tampoco se barre).
    """
    folders = []
    pending = [f'{_user_path(mailbox)}/mailFolders']
    while pending:
        url = pending.pop()
        params = {'$select': 'id,displayName,childFolderCount', '$top': '100'}
        while url:
            data = _request('GET', url, headers, params=params).json()
            for folder in data.get('value', []):
                if folder['id'] in excluded_ids:
                    continue
                folders.append({'id': folder['id'], 'name': folder.get('displayName') or ''})
                if folder.get('childFolderCount'):
                    pending.append(
                        f'{_user_path(mailbox)}/mailFolders/{quote(folder["id"], safe="")}/childFolders'
                    )
            # El nextLink ya lleva sus parámetros.
            url, params = data.get('@odata.nextLink'), None
    return folders


def sweep_folders(mailbox, headers):
    """Carpetas que se barren en un buzón, Inbox primero.

    Todas salvo las de sistema, también las que se creen más adelante: el
    equipo mueve alarmas e informes a carpetas propias con reglas del buzón y
    tienen que seguir entrando como tickets (#21419). La lista se cachea unos
    minutos para no recorrer el árbol de carpetas en cada vuelta.
    """
    from django.core.cache import cache

    inbox = {'id': inbox_folder_id(mailbox, headers), 'name': 'Inbox'}
    if not getattr(settings, 'INBOUND_SWEEP_ALL_FOLDERS', True):
        return [inbox]
    cache_key = f'inbound_sweep_folders:{mailbox.lower()}'
    cached = cache.get(cache_key)
    if cached is not None:
        return cached
    try:
        excluded = {_well_known_folder_id(mailbox, name, headers) for name in REQUIRED_EXCLUDED_FOLDERS}
        for name in OPTIONAL_EXCLUDED_FOLDERS:
            try:
                excluded.add(_well_known_folder_id(mailbox, name, headers))
            except GraphNotFound:
                pass
        folders = [inbox] + [
            folder for folder in _list_mail_folders(mailbox, headers, excluded)
            if folder['id'] != inbox['id']
        ]
    except Exception as exc:
        # Sin la lista completa (o sin saber qué excluir) se barre solo Inbox en
        # esta vuelta: nunca se arriesga a barrer Enviados. No se cachea.
        logger.error('inbound_sweep [%s]: no se pudieron listar las carpetas, solo se barre Inbox: %s',
                     mailbox, exc)
        return [inbox]
    cache.set(cache_key, folders, getattr(settings, 'INBOUND_SWEEP_FOLDERS_CACHE_SECONDS', 600))
    return folders


def list_window(mailbox, folder_id, since, until=None, headers=None, top=200, skip=0, fields=LIST_FIELDS):
    """Una página de mensajes recibidos desde `since`, en orden ascendente.

    El que llama pagina por *keyset* (repite con el último receivedDateTime) en
    lugar de seguir el nextLink, que usa $skip: si se borra o mueve un mensaje
    durante la lectura, un offset se salta otro y la marca de agua lo dejaría
    atrás. `skip` solo se usa para desempatar una página entera con el mismo
    segundo.
    """
    flt = f'receivedDateTime ge {graph_timestamp(since)}'
    if until is not None:
        flt += f' and receivedDateTime le {graph_timestamp(until)}'
    params = {
        '$filter': flt,
        # La propiedad del $orderby tiene que estar también en el $filter;
        # si no, Graph responde InefficientFilter.
        '$orderby': 'receivedDateTime asc',
        '$select': fields,
        '$top': str(top),
    }
    if skip:
        params['$skip'] = str(skip)
    url = f'{_user_path(mailbox)}/mailFolders/{quote(folder_id, safe="")}/messages'
    return _request('GET', url, headers, params=params).json().get('value', [])


def get_message(mailbox, message_id, headers):
    url = f'{_user_path(mailbox)}/messages/{quote(message_id, safe="")}'
    return _request('GET', url, headers, params={'$select': MESSAGE_FIELDS}).json()


def find_by_internet_message_id(mailbox, internet_message_id, headers):
    """Busca un mensaje en todo el buzón por su Message-ID (sobrevive a moverlo)."""
    if not internet_message_id:
        return None
    escaped = internet_message_id.replace("'", "''")
    response = _request('GET', f'{_user_path(mailbox)}/messages', headers, params={
        '$filter': f"internetMessageId eq '{escaped}'",
        '$select': MESSAGE_FIELDS,
        '$top': '1',
    })
    values = response.json().get('value', [])
    return values[0] if values else None


def fetch_attachments(mailbox, message_id, headers):
    """Descarga adjuntos de archivo de Graph antes de procesar el mensaje.

    Las imágenes inline conservan su Content-ID y no consumen el cupo de
    archivos de la galería. Los tipos o tamaños no admitidos no bloquean el correo.
    """
    base = f'{_user_path(mailbox)}/messages/{quote(message_id, safe="")}/attachments'
    response = _request('GET', base, headers, params={
        '$select': 'id,name,contentType,size,isInline,contentId', '$top': '25',
    })
    items = []
    while True:
        listing = response.json()
        items.extend(listing.get('value', []))
        next_link = listing.get('@odata.nextLink')
        if not next_link:
            break
        response = _request('GET', next_link, headers)

    max_files = getattr(settings, 'PUBLIC_ATTACHMENT_MAX_FILES', 5)
    max_bytes = getattr(settings, 'PUBLIC_ATTACHMENT_MAX_BYTES', 15 * 1024 * 1024)
    max_total = getattr(settings, 'PUBLIC_ATTACHMENT_MAX_TOTAL_BYTES', 25 * 1024 * 1024)
    allowed = getattr(settings, 'PUBLIC_ATTACHMENT_ALLOWED_EXTENSIONS', set())
    accepted = []
    total = 0
    regular_count = 0
    for item in items:
        if item.get('@odata.type') not in (
            None, '#microsoft.graph.fileAttachment',
        ):
            continue
        name = os.path.basename((item.get('name') or 'archivo').replace('\\', '/'))
        extension = os.path.splitext(name)[1].lower().lstrip('.')
        declared_size = int(item.get('size') or 0)
        is_inline = bool(item.get('isInline'))
        if (
            (not is_inline and regular_count >= max_files)
            or not extension
            or extension not in allowed
            or declared_size > max_bytes
            or total + declared_size > max_total
        ):
            logger.warning(
                'inbound attachment skipped by policy',
                extra={'message_id': message_id, 'attachment_name': name, 'size': declared_size},
            )
            continue

        content = _request(
            'GET', f'{base}/{quote(item["id"], safe="")}/$value', headers, timeout=60,
        ).content
        if len(content) > max_bytes or total + len(content) > max_total:
            logger.warning(
                'inbound attachment skipped after download',
                extra={'message_id': message_id, 'attachment_name': name, 'size': len(content)},
            )
            continue
        accepted.append({
            'name': name,
            'content_type': item.get('contentType') or 'application/octet-stream',
            'content': content,
            'is_inline': is_inline,
            'content_id': str(item.get('contentId') or '').strip().strip('<>'),
        })
        total += len(content)
        if not is_inline:
            regular_count += 1
    return accepted


def mark_read(mailbox, message_id, headers):
    """Cortesía para quien mire el buzón; nunca forma parte de la corrección."""
    try:
        _request('PATCH', f'{_user_path(mailbox)}/messages/{quote(message_id, safe="")}',
                 headers, timeout=10, json={'isRead': True})
    except Exception as exc:
        logger.warning('inbound mark-read failed …%s: %s', message_id[-16:], exc)
