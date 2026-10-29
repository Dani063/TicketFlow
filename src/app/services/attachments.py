"""Almacenamiento y descarga segura de adjuntos de tickets."""

import csv
import hashlib
import io
import json
import logging
import os
import re
import time
import unicodedata
import uuid
import warnings
from contextlib import contextmanager
from datetime import timedelta
from functools import wraps
from urllib.parse import unquote, urlsplit

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError
from django.conf import settings
from django.core.files.storage import default_storage
from django.db import OperationalError, connection, transaction
from django.db.models import F
from django.http import FileResponse, HttpResponse, HttpResponseRedirect, JsonResponse
from django.utils import timezone
from django.utils.http import content_disposition_header
from django.urls import reverse

from app.api import APIValidationError
from app.models import Attachment, AttachmentDraft, AttachmentRequest, Ticket, User
from app.permissions import can_view_all_tickets, can_view_ticket, is_readonly


_SAFE_CHARS = re.compile(r"[^A-Za-z0-9._-]+")
logger = logging.getLogger(__name__)


def retry_attachment_mutation(func):
    """Retry the complete transaction, never a query inside a broken transaction.

    SQLite shared-memory databases report SQLITE_LOCKED immediately instead of
    honoring busy_timeout. A nested mutation delegates retry to its outer owner.
    Committed request results are always replayed through the persistent ledger.
    """
    @wraps(func)
    def wrapped(*args, **kwargs):
        nested = connection.in_atomic_block
        for attempt in range(40):
            try:
                return func(*args, **kwargs)
            except OperationalError as exc:
                contention = (connection.vendor == 'sqlite' and 'locked' in str(exc).lower()) or (
                    connection.vendor == 'mysql' and exc.args and exc.args[0] in {1205, 1213})
                if not contention or nested:
                    raise
                if attempt == 39:
                    raise APIValidationError('attachment_busy', 'Hay otra operación en curso. Reintenta con las mismas claves.', status=503) from exc
                time.sleep(min(0.005 * (attempt + 1), 0.1))
    return wrapped


class AttachmentService:
    STORAGE_ERROR_MESSAGE = "No se pudo guardar el adjunto. Inténtalo de nuevo en unos minutos."
    IMAGE_TYPES = {'.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg',
                   '.gif': 'image/gif', '.webp': 'image/webp', '.bmp': 'image/bmp'}
    PREVIEW_MAX_BYTES = 256 * 1024
    CSV_MAX_ROWS = 200
    CSV_MAX_COLUMNS = 50
    CSV_MAX_CELL_CHARS = 2000

    @staticmethod
    def limits():
        return {
            'max_files': getattr(settings, 'PUBLIC_ATTACHMENT_MAX_FILES', 10),
            'max_bytes': getattr(settings, 'PUBLIC_ATTACHMENT_MAX_BYTES', 15 * 1024 * 1024),
            'max_total_bytes': getattr(settings, 'PUBLIC_ATTACHMENT_MAX_TOTAL_BYTES', 50 * 1024 * 1024),
            'allowed_extensions': sorted(getattr(settings, 'PUBLIC_ATTACHMENT_ALLOWED_EXTENSIONS', set())),
        }

    @staticmethod
    def original_filename(name):
        # Preserve Unicode and spaces, but not path components, control characters,
        # bidi overrides or header injection. This value is never a storage key.
        name = str(name or '').replace('\\', '/').rsplit('/', 1)[-1]
        name = ''.join(c for c in name if not unicodedata.category(c).startswith('C')).strip()
        if not name or name in {'.', '..'}:
            return 'archivo'
        if len(name) > 255:
            stem, ext = os.path.splitext(name)
            name = stem[:255 - len(ext)] + ext if len(ext) < 255 else name[:255]
        return name

    @staticmethod
    def filename(attachment):
        if getattr(attachment, 'original_name', ''):
            return AttachmentService.original_filename(attachment.original_name)
        try:
            path = urlsplit(getattr(attachment, 'file_url', '') or '').path
        except ValueError:
            path = ''
        basename = unquote(path.rsplit('/', 1)[-1])
        if not basename:
            basename = (getattr(attachment, 'storage_key', '') or '').rsplit('/', 1)[-1]
        return AttachmentService.original_filename(basename) if basename else f'adjunto-{attachment.id}'

    @staticmethod
    def parse_uuid(value, field, required=False):
        if value is None and not required:
            return None
        try:
            if not isinstance(value, (str, uuid.UUID)):
                raise ValueError
            return uuid.UUID(str(value))
        except (ValueError, AttributeError, TypeError):
            raise APIValidationError('invalid_' + field, f'{field} debe ser un UUID válido.', status=400)

    @staticmethod
    def parse_ids(value, from_post=False):
        if value is None:
            return []
        if from_post and isinstance(value, str):
            try:
                value = json.loads(value)
            except (ValueError, TypeError):
                raise APIValidationError('invalid_attachment_ids', 'attachment_ids debe ser una lista JSON.', status=400)
        if (not isinstance(value, list) or any(type(i) is not int or i <= 0 or i > 2**63 - 1 for i in value)
                or len(set(value)) != len(value)):
            raise APIValidationError('invalid_attachment_ids', 'attachment_ids debe contener IDs enteros únicos.', status=400)
        max_files = getattr(settings, 'PUBLIC_ATTACHMENT_MAX_FILES', 10)
        if len(value) > max_files:
            raise APIValidationError(
                'too_many_files',
                f'Demasiados archivos por mensaje: lleva {len(value)} y el máximo es {max_files}. Quita alguno antes de enviar.',
                status=400,
            )
        return sorted(value)

    @staticmethod
    def size_label(size):
        if size < 1024 * 1024:
            return f'{max(1, round(size / 1024))} KB'
        return f'{size / (1024 * 1024):.1f}'.rstrip('0').rstrip('.').replace('.', ',') + ' MB'

    @staticmethod
    def lock_actor(actor):
        # First SQL statement in each mutation transaction: an actual write locks
        # even an empty batch on SQLite; on MySQL it acquires the owner's row lock.
        # This serializes independent workers, including first upload / publish.
        if not actor or not actor.is_authenticated or not actor.is_active:
            raise APIValidationError('forbidden', 'Usuario no disponible.', status=403)
        if not User.objects.filter(pk=actor.pk, is_active=True).update(id=F('id')):
            raise APIValidationError('forbidden', 'Usuario no disponible.', status=403)

    @staticmethod
    def fingerprint(payload):
        def encode(value):
            if hasattr(value, 'pk'):
                return value.pk
            if isinstance(value, uuid.UUID):
                return str(value)
            if hasattr(value, 'isoformat'):
                return value.isoformat()
            raise TypeError(type(value).__name__)
        return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=True,
                                         separators=(',', ':'), default=encode).encode()).hexdigest()

    @staticmethod
    def replay(actor, operation, key, fingerprint):
        if key is None:
            return None
        previous = AttachmentRequest.objects.filter(actor=actor, operation=operation, key=key).first()
        if previous and previous.fingerprint != fingerprint:
            raise APIValidationError('idempotency_conflict', 'La clave ya se utilizó para otra petición.', status=409)
        return previous

    @staticmethod
    def remember(actor, operation, key, fingerprint, **result):
        if key is not None:
            AttachmentRequest.objects.create(actor=actor, operation=operation, key=key,
                                             fingerprint=fingerprint, **result)

    @staticmethod
    def validate_batch(attachments, adding=None):
        """Límites de un mensaje. Con adding (el archivo que se está subiendo), los
        avisos explican qué le pasa a ese archivo en concreto."""
        max_files = getattr(settings, 'PUBLIC_ATTACHMENT_MAX_FILES', 10)
        max_total = getattr(settings, 'PUBLIC_ATTACHMENT_MAX_TOTAL_BYTES', 50 * 1024 * 1024)
        max_bytes = getattr(settings, 'PUBLIC_ATTACHMENT_MAX_BYTES', 15 * 1024 * 1024)
        name = AttachmentService.original_filename(adding.name) if adding is not None else ''
        label = AttachmentService.size_label
        if len(attachments) > max_files:
            files = f'{max_files} archivo' if max_files == 1 else f'{max_files} archivos'
            message = (f'No se ha añadido «{name}»: este mensaje ya tiene {files}, el máximo por mensaje. '
                       'Quita alguno para añadir otro.') if adding is not None else (
                       f'Demasiados archivos por mensaje: lleva {len(attachments)} y el máximo es {max_files}. '
                       'Quita alguno antes de enviar.')
            raise APIValidationError('too_many_files', message, status=400)
        total = sum(a.size or 0 for a in attachments)
        if total > max_total:
            message = (f'No se ha añadido «{name}»: con él los adjuntos de este mensaje sumarían {label(total)} '
                       f'y el máximo es {label(max_total)}.') if adding is not None else (
                       f'Los adjuntos de este mensaje suman {label(total)} y el máximo es {label(max_total)}.')
            raise APIValidationError('attachments_too_large', message, status=400)
        too_big = next((a for a in attachments if (a.size or 0) > max_bytes), None)
        if too_big is not None:
            too_big_name = name if too_big is adding else AttachmentService.filename(too_big)
            raise APIValidationError(
                'file_too_large',
                f'«{too_big_name}» ocupa {label(too_big.size)} y el máximo por archivo es {label(max_bytes)}.',
                status=400,
            )

    @staticmethod
    def validate_email_size(attachments):
        """La respuesta pública viaja por correo, que admite menos que un mensaje."""
        max_email = getattr(settings, 'OUTBOUND_EMAIL_ATTACHMENT_MAX_BYTES', 25 * 1024 * 1024)
        total = sum(a.size or 0 for a in attachments)
        if total > max_email:
            label = AttachmentService.size_label
            raise APIValidationError(
                'email_attachments_too_large',
                f'Una respuesta pública se envía por correo y el correo admite como máximo {label(max_email)} '
                f'de adjuntos; este mensaje lleva {label(total)}. Quita adjuntos o publícalo como nota interna.',
                status=400,
            )

    @staticmethod
    def pending_for_publish(actor, ids, ticket=None, draft=None):
        attachments = list(Attachment.objects.select_for_update().filter(id__in=ids).order_by('id'))
        if len(attachments) != len(ids):
            missing = len(ids) - len(attachments)
            raise APIValidationError(
                'invalid_attachments',
                (f'{missing} de los adjuntos ya no están en el servidor' if missing > 1 else 'Un adjunto ya no está en el servidor')
                + ': lo subido y no publicado se borra a las 24 horas. Quítalo del mensaje y vuelve a añadirlo.',
                status=400,
            )
        # Lo ajeno va antes que lo publicado para no revelar nombres de otros.
        if any(
            a.uploaded_by_id != actor.id or not a.storage_key
            or a.ticket_id != (ticket.id if ticket else None)
            or (draft is not None and a.draft_id != draft.id)
            for a in attachments
        ):
            raise APIValidationError('invalid_attachments', 'Algún adjunto no te pertenece o es de otro ticket.', status=400)
        published = next((a for a in attachments if a.comment_id is not None), None)
        if published is not None:
            raise APIValidationError(
                'invalid_attachments',
                f'«{AttachmentService.filename(published)}» ya se publicó en otro mensaje. Quítalo y vuelve a añadirlo.',
                status=400,
            )
        AttachmentService.validate_batch(attachments)
        return attachments

    @staticmethod
    def can_access(user, attachment):
        if not user.is_authenticated or not user.is_active:
            return False
        if attachment.comment_id:
            comment = attachment.comment
            if comment.ticket_id != attachment.ticket_id or (not comment.is_public and not can_view_all_tickets(user)):
                return False
        elif attachment.uploaded_by_id != user.id:
            return False
        if attachment.ticket_id:
            return can_view_ticket(user, attachment.ticket)
        return attachment.comment_id is None and attachment.uploaded_by_id == user.id

    INLINE_PREVIEW_RE = re.compile(r'/attachments/(\d+)/preview/')

    @staticmethod
    def mark_inline(comment, html_body):
        """Marca como inline los adjuntos del comentario que aparecen dentro del texto.

        El compositor inserta las imágenes pegadas en el HTML con su URL de vista
        previa (#21525). Así no se repiten en la galería y el correo las lleva
        dentro del cuerpo, referenciadas por su Content-ID.
        """
        referenced = {int(value) for value in AttachmentService.INLINE_PREVIEW_RE.findall(html_body or '')}
        if not referenced:
            return []
        marked = []
        for attachment in Attachment.objects.filter(comment=comment, id__in=referenced, is_inline=False):
            attachment.is_inline = True
            attachment.content_id = attachment.content_id or f'tf-att-{attachment.id}'
            attachment.save(update_fields=['is_inline', 'content_id'])
            marked.append(attachment.id)
        return marked

    @staticmethod
    def metadata(attachment):
        """Pure metadata: no network, blob reads or remote URL signing during SSR."""
        filename = AttachmentService.filename(attachment)
        ext = os.path.splitext(filename)[1].lower()
        kind = None
        if attachment.storage_key:
            if ext in AttachmentService.IMAGE_TYPES:
                kind = 'image'
            elif ext == '.pdf':
                kind = 'pdf'
            elif ext in {'.txt', '.log'}:
                kind = 'text'
            elif ext == '.csv':
                kind = 'csv'
        category = ('image' if ext in AttachmentService.IMAGE_TYPES else
                    'pdf' if ext == '.pdf' else 'text' if ext in {'.txt', '.log'} else
                    'csv' if ext == '.csv' else 'archive' if ext in {'.zip', '.rar', '.7z', '.gz'} else
                    'document' if ext in {'.doc', '.docx', '.xls', '.xlsx', '.ppt', '.pptx', '.odt', '.ods'} else 'file')
        download = reverse('attachment_download', args=[attachment.id])
        preview = reverse('attachment_preview', args=[attachment.id]) if kind else None
        return {'id': attachment.id, 'filename': filename,
                'size': attachment.size if attachment.storage_key or attachment.size else None,
                'file_type': attachment.file_type or '', 'category': category, 'preview_kind': kind,
                'preview_url': preview, 'thumbnail_url': preview + '?thumbnail=1' if kind == 'image' else None,
                'download_url': download, 'file_url': download}

    @staticmethod
    def validate_files(files):
        files = list(files or [])
        max_files = getattr(settings, "PUBLIC_ATTACHMENT_MAX_FILES", 10)
        max_bytes = getattr(settings, "PUBLIC_ATTACHMENT_MAX_BYTES", 15 * 1024 * 1024)
        max_total_bytes = getattr(settings, "PUBLIC_ATTACHMENT_MAX_TOTAL_BYTES", 50 * 1024 * 1024)
        allowed = getattr(settings, "PUBLIC_ATTACHMENT_ALLOWED_EXTENSIONS", set())
        label = AttachmentService.size_label
        if len(files) > max_files:
            raise APIValidationError(
                "too_many_files",
                f"Has elegido {len(files)} archivos y el máximo es {max_files} por mensaje.",
                status=400,
            )
        total = sum(getattr(uploaded, "size", 0) for uploaded in files)
        if total > max_total_bytes:
            raise APIValidationError(
                "attachments_too_large",
                f"Los adjuntos suman {label(total)} y el máximo por mensaje es {label(max_total_bytes)}.",
                status=400,
            )
        for uploaded in files:
            name = os.path.basename((uploaded.name or "archivo").replace("\\", "/"))
            ext = os.path.splitext(name)[1].lower().lstrip(".")
            if not ext or ext not in allowed:
                raise APIValidationError(
                    "file_type_not_allowed",
                    f"«{AttachmentService.original_filename(name)}»: el tipo de archivo .{ext or '?'} no está permitido. "
                    f"Se admiten: {', '.join(sorted(allowed))}.",
                    status=400,
                )
            if uploaded.size > max_bytes:
                raise APIValidationError(
                    "file_too_large",
                    f"«{AttachmentService.original_filename(name)}» ocupa {label(uploaded.size)} "
                    f"y el máximo por archivo es {label(max_bytes)}.",
                    status=400,
                )
        return files

    @staticmethod
    def safe_name(name):
        basename = os.path.basename((name or "archivo").replace("\\", "/"))
        stem, ext = os.path.splitext(basename)
        safe_stem = _SAFE_CHARS.sub("-", stem).strip(".-_")[:120] or "archivo"
        safe_ext = _SAFE_CHARS.sub("", ext.lower())[:12]
        return f"{safe_stem}{safe_ext}"

    @staticmethod
    def _storage_backend():
        mode = (getattr(settings, "ATTACHMENT_STORAGE_BACKEND", "auto") or "auto").strip().lower()
        bucket = (getattr(settings, "PUBLIC_ATTACHMENTS_S3_BUCKET", "") or "").strip()
        if mode not in {"auto", "local", "s3"}:
            logger.error("attachment_storage_mode_invalid", extra={"storage_mode": mode})
            raise APIValidationError(
                "attachment_storage_not_configured",
                "El almacenamiento de adjuntos no está configurado correctamente.",
                status=503,
            )
        if mode == "s3" and not bucket:
            logger.error("attachment_s3_bucket_missing")
            raise APIValidationError(
                "attachment_storage_not_configured",
                "El almacenamiento de adjuntos no está disponible temporalmente.",
                status=503,
            )
        return ("s3" if bucket else "local") if mode == "auto" else mode

    @staticmethod
    def _storage_key(ticket, original_name, draft=None):
        prefix = (getattr(settings, "ATTACHMENT_STORAGE_PREFIX", "") or "").strip("/ ")
        scope = ('tickets', str(ticket.id)) if ticket else ('drafts', str(draft.uploaded_by_id), str(draft.key))
        parts = [part for part in (prefix, "attachments", *scope) if part]
        return "/".join(parts + [f"{uuid.uuid4().hex}-{original_name}"])

    @staticmethod
    def _s3_client():
        return boto3.client(
            "s3",
            region_name=getattr(settings, "PUBLIC_ATTACHMENTS_S3_REGION", None),
            config=Config(signature_version="s3v4"),
        )

    @staticmethod
    def _raise_storage_error(operation, backend, exc, **context):
        logger.exception(
            "attachment_storage_failed",
            extra={"operation": operation, "storage_backend": backend, **context},
        )
        raise APIValidationError(
            "attachment_storage_unavailable",
            AttachmentService.STORAGE_ERROR_MESSAGE,
            status=503,
        ) from exc

    @staticmethod
    def save(uploaded, ticket, user, comment=None, draft=None):
        AttachmentService.validate_files([uploaded])
        original_name = AttachmentService.original_filename(uploaded.name)
        storage_key = AttachmentService._storage_key(ticket, AttachmentService.safe_name(original_name), draft=draft)
        bucket = (getattr(settings, "PUBLIC_ATTACHMENTS_S3_BUCKET", "") or "").strip()
        backend = AttachmentService._storage_backend()

        try:
            if backend == "s3":
                extra = {
                    "ContentType": "application/octet-stream",
                    "ContentDisposition": content_disposition_header(True, original_name),
                    "ServerSideEncryption": "AES256",
                }
                AttachmentService._s3_client().upload_fileobj(
                    uploaded, bucket, storage_key, ExtraArgs=extra,
                )
            else:
                storage_key = default_storage.save(storage_key, uploaded)
        except (BotoCoreError, ClientError, OSError) as exc:
            AttachmentService._raise_storage_error(
                "upload", backend, exc, ticket_id=ticket.id if ticket else None, original_name=original_name,
            )

        try:
            attachment = Attachment.objects.create(
                file_url="https://private.invalid/pending",
                file_type=getattr(uploaded, "content_type", None),
                storage_key=storage_key,
                storage_backend=backend,
                original_name=original_name,
                size=getattr(uploaded, "size", 0),
                is_private=True,
                ticket=ticket,
                comment=comment,
                uploaded_by=user,
                draft=draft,
            )
            attachment.file_url = reverse("attachment_download", args=[attachment.id])
            attachment.save(update_fields=["file_url"])
        except Exception:
            # Si la base de datos falla después de almacenar el objeto, no debe
            # quedar un fichero privado sin registro ni propietario funcional.
            AttachmentService.delete_blob_by_key(backend, storage_key, suppress_errors=True)
            raise
        return attachment

    @staticmethod
    @retry_attachment_mutation
    def upload(uploaded, user, ticket=None, draft_key=None, upload_key=None, pending_ids=None):
        """Sube un archivo pendiente de publicar.

        pending_ids son los adjuntos que el editor sigue teniendo en este mensaje:
        el límite se cuenta sobre ellos. Lo que quedó subido en una pestaña cerrada
        o recargada no se ve, así que no puede bloquear (lo borra la limpieza de
        huérfanos). Sin pending_ids (clientes antiguos) cuentan todos los pendientes.
        """
        if is_readonly(user):
            raise APIValidationError('forbidden', 'Tu rol es de solo lectura.', status=403)
        if ticket is not None and draft_key is not None:
            raise APIValidationError('invalid_upload_scope', 'Indica ticket o borrador, no ambos.', status=400)
        draft_key = AttachmentService.parse_uuid(draft_key, 'draft_key', required=ticket is None)
        upload_key = AttachmentService.parse_uuid(upload_key, 'upload_key', required=ticket is None)
        if pending_ids is not None:
            pending_ids = AttachmentService.parse_ids(pending_ids, from_post=True)
        scope = {'ticket_id': ticket.pk if ticket else None, 'draft_key': str(draft_key) if draft_key else None}
        AttachmentService.validate_files([uploaded])
        digest, size = hashlib.sha256(), 0
        max_bytes = getattr(settings, 'PUBLIC_ATTACHMENT_MAX_BYTES', 15 * 1024 * 1024)
        for chunk in uploaded.chunks():
            size += len(chunk)
            if size > max_bytes:
                raise APIValidationError(
                    'file_too_large',
                    f'«{AttachmentService.original_filename(uploaded.name)}» ocupa más de '
                    f'{AttachmentService.size_label(max_bytes)}, el máximo por archivo.',
                    status=400,
                )
            digest.update(chunk)
        uploaded.seek(0)
        if uploaded.size != size:
            raise APIValidationError('invalid_file_size', 'Tamaño de archivo incorrecto.', status=400)
        fingerprint = AttachmentService.fingerprint({
            'ticket': ticket.pk if ticket else None, 'draft_key': draft_key,
            'filename': AttachmentService.original_filename(uploaded.name), 'size': size,
            'file_type': uploaded.content_type, 'sha256': digest.hexdigest(),
        })
        stored = None
        try:
            with transaction.atomic():
                AttachmentService.lock_actor(user)
                if ticket:
                    ticket = Ticket.objects.select_for_update().get(pk=ticket.pk)
                    if not can_view_ticket(user, ticket):
                        raise APIValidationError('forbidden', 'No tienes acceso a este ticket.', status=403)
                cancelled = AttachmentService.replay(user, 'cancel', upload_key, AttachmentService.fingerprint(scope))
                if cancelled:
                    raise APIValidationError('upload_cancelled', 'La subida fue cancelada.', status=409)
                previous = AttachmentService.replay(user, 'upload', upload_key, fingerprint)
                if previous:
                    if previous.attachment_id is None:
                        raise APIValidationError('attachment_gone', 'Este adjunto ya fue eliminado.', status=410)
                    attachment = previous.attachment
                    if not AttachmentService.can_access(user, attachment):
                        raise APIValidationError('forbidden', 'No tienes acceso a este adjunto.', status=403)
                    return attachment
                draft = None
                if ticket is None:
                    draft, _ = AttachmentDraft.objects.get_or_create(uploaded_by=user, key=draft_key)
                    if draft.published_at:
                        raise APIValidationError('draft_already_published', 'Este borrador ya se publicó.', status=409)
                pending = Attachment.objects.filter(uploaded_by=user, ticket=ticket, draft=draft, comment__isnull=True)
                if pending_ids is not None:
                    pending = pending.filter(id__in=pending_ids)
                AttachmentService.validate_batch([*pending, uploaded], adding=uploaded)
                stored = AttachmentService.save(uploaded, ticket, user, draft=draft)
                AttachmentService.remember(user, 'upload', upload_key, fingerprint, attachment=stored, result={'scope': scope})
            return stored
        except Exception:
            if stored is not None:
                AttachmentService.delete_blob_by_key(stored.storage_backend, stored.storage_key, suppress_errors=True)
            raise

    @staticmethod
    @retry_attachment_mutation
    @transaction.atomic
    def cancel_upload(actor, upload_key, draft_key=None, ticket_id=None):
        AttachmentService.lock_actor(actor)
        upload_key = AttachmentService.parse_uuid(upload_key, 'upload_key', required=True)
        if ticket_id is not None and (type(ticket_id) is not int or ticket_id <= 0 or ticket_id > 2**63 - 1):
            raise APIValidationError('invalid_ticket_id', 'ticket_id debe ser un entero positivo.', status=400)
        if ticket_id is not None and draft_key is not None:
            raise APIValidationError('invalid_upload_scope', 'Indica ticket o borrador, no ambos.', status=400)
        draft_key = AttachmentService.parse_uuid(draft_key, 'draft_key', required=ticket_id is None)
        if ticket_id:
            ticket = Ticket.objects.filter(pk=ticket_id).first()
            if ticket is None or not can_view_ticket(actor, ticket):
                raise APIValidationError('forbidden', 'No tienes acceso a este ticket.', status=403)
        scope = {'ticket_id': ticket_id, 'draft_key': str(draft_key) if draft_key else None}
        fingerprint = AttachmentService.fingerprint(scope)
        if AttachmentService.replay(actor, 'cancel', upload_key, fingerprint):
            return
        upload = AttachmentRequest.objects.filter(actor=actor, operation='upload', key=upload_key).first()
        if upload:
            if upload.result.get('scope') != scope:
                raise APIValidationError('idempotency_conflict', 'La clave pertenece a otro ámbito.', status=409)
            if upload.attachment_id:
                AttachmentService.delete_pending(upload.attachment_id, actor)
        AttachmentService.remember(actor, 'cancel', upload_key, fingerprint, result={'scope': scope})

    @staticmethod
    def open_local(attachment):
        try:
            return default_storage.open(attachment.storage_key, "rb")
        except OSError as exc:
            AttachmentService._raise_storage_error(
                "download", "local", exc, attachment_id=attachment.id,
            )

    @staticmethod
    def read(attachment):
        """Devuelve el contenido para proveedores de correo, sin exponer S3."""
        try:
            if attachment.storage_backend == "s3":
                response = AttachmentService._s3_client().get_object(
                    Bucket=(getattr(settings, "PUBLIC_ATTACHMENTS_S3_BUCKET", "") or "").strip(),
                    Key=attachment.storage_key,
                )
                return response["Body"].read()
            with default_storage.open(attachment.storage_key, "rb") as stored:
                return stored.read()
        except (BotoCoreError, ClientError, OSError) as exc:
            AttachmentService._raise_storage_error(
                "read", attachment.storage_backend, exc, attachment_id=attachment.id,
            )

    @staticmethod
    @contextmanager
    def open_blob(attachment):
        stored = None
        try:
            if attachment.storage_backend == 's3':
                stored = AttachmentService._s3_client().get_object(
                    Bucket=getattr(settings, 'PUBLIC_ATTACHMENTS_S3_BUCKET', ''),
                    Key=attachment.storage_key,
                )['Body']
            else:
                stored = default_storage.open(attachment.storage_key, 'rb')
            yield stored
        except (BotoCoreError, ClientError, OSError) as exc:
            AttachmentService._raise_storage_error('preview', attachment.storage_backend, exc, attachment_id=attachment.id)
        finally:
            if stored is not None:
                stored.close()

    @staticmethod
    def secure_response(response):
        response['X-Content-Type-Options'] = 'nosniff'
        response['Content-Security-Policy'] = "default-src 'none'; sandbox"
        response['Cache-Control'] = 'private, no-store'
        response['Referrer-Policy'] = 'no-referrer'
        response['X-Frame-Options'] = 'SAMEORIGIN'
        return response

    @staticmethod
    def download_response(attachment):
        if not attachment.storage_key:
            raise APIValidationError('attachment_content_unavailable', 'El archivo heredado no está almacenado de forma segura.', status=410)
        if attachment.storage_backend == 's3':
            response = HttpResponseRedirect(AttachmentService.presigned_url(attachment))
        else:
            response = FileResponse(AttachmentService.open_local(attachment), as_attachment=True,
                                    filename=AttachmentService.filename(attachment), content_type='application/octet-stream')
        return AttachmentService.secure_response(response)

    @staticmethod
    def preview_response(attachment, thumbnail=False):
        metadata = AttachmentService.metadata(attachment)
        kind = metadata['preview_kind']
        if kind is None or (thumbnail and kind != 'image'):
            raise APIValidationError('preview_not_available', 'Vista previa no disponible.', status=415)
        limit = (AttachmentService.PREVIEW_MAX_BYTES if kind in {'text', 'csv'} else
                 getattr(settings, 'PUBLIC_ATTACHMENT_MAX_BYTES', 15 * 1024 * 1024))
        with AttachmentService.open_blob(attachment) as stored:
            content = stored.read(limit + 1)
        truncated = len(content) > limit
        content = content[:limit]
        if kind in {'text', 'csv'}:
            # Bound the raw read, decoding and CSV dimensions independently. JSON
            # strings stay inert, including HTML and formula-looking CSV cells.
            encoding = 'utf-16' if content.startswith((b'\xff\xfe', b'\xfe\xff')) else 'utf-8-sig'
            text = content.decode(encoding, errors='replace')
            if kind == 'text':
                response = JsonResponse({'kind': 'text', 'text': text, 'truncated': truncated})
            else:
                rows = []
                try:
                    try:
                        dialect = csv.Sniffer().sniff(text[:4096], delimiters=',;\t|')
                    except csv.Error:
                        dialect = csv.excel
                    for row in csv.reader(io.StringIO(text, newline=''), dialect):
                        if len(rows) >= AttachmentService.CSV_MAX_ROWS:
                            truncated = True
                            break
                        if len(row) > AttachmentService.CSV_MAX_COLUMNS:
                            truncated = True
                        cells = row[:AttachmentService.CSV_MAX_COLUMNS]
                        if any(len(cell) > AttachmentService.CSV_MAX_CELL_CHARS for cell in cells):
                            truncated = True
                        rows.append([cell[:AttachmentService.CSV_MAX_CELL_CHARS] for cell in cells])
                except csv.Error:
                    truncated = True
                response = JsonResponse({'kind': 'csv', 'rows': rows, 'truncated': truncated})
        else:
            if truncated:
                raise APIValidationError('preview_too_large', 'Archivo demasiado grande para vista previa.', status=413)
            if kind == 'image':
                # Re-encode raster pixels, never serve user-supplied HTML/SVG or
                # image metadata verbatim. Restrict formats before invoking decoders.
                from PIL import Image, UnidentifiedImageError
                try:
                    with warnings.catch_warnings():
                        warnings.simplefilter('error', Image.DecompressionBombWarning)
                        with Image.open(io.BytesIO(content), formats=['PNG', 'JPEG', 'GIF', 'WEBP', 'BMP']) as img:
                            if img.width * img.height > 16_000_000:
                                raise ValueError('image dimensions')
                            img.load()
                            img = img.convert('RGBA')
                            img.thumbnail((480, 480) if thumbnail else (4096, 4096))
                            output = io.BytesIO()
                            img.save(output, format='PNG')
                            content = output.getvalue()
                except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
                    raise APIValidationError('invalid_preview', 'La imagen no es válida.', status=415) from exc
                content_type = 'image/png'
            else:
                if not content.startswith(b'%PDF-'):
                    raise APIValidationError('invalid_preview', 'El PDF no es válido.', status=415)
                content_type = 'application/pdf'
            response = HttpResponse(content, content_type=content_type)
            response['Content-Disposition'] = content_disposition_header(False, metadata['filename'])
        return AttachmentService.secure_response(response)

    @staticmethod
    @retry_attachment_mutation
    @transaction.atomic
    def delete_pending(attachment_id, actor):
        AttachmentService.lock_actor(actor)
        attachment = Attachment.objects.select_for_update().select_related('ticket', 'comment').get(pk=attachment_id)
        if attachment.uploaded_by_id != actor.id or not AttachmentService.can_access(actor, attachment):
            raise APIValidationError('forbidden', 'No tienes acceso a este adjunto.', status=403)
        if attachment.comment_id:
            raise APIValidationError('attachment_already_sent', 'El adjunto ya forma parte de una respuesta.', status=409)
        backend, key = attachment.storage_backend, attachment.storage_key
        attachment.delete()
        # A transaction rollback must leave both the record and its bytes intact.
        transaction.on_commit(lambda: AttachmentService.delete_blob_by_key(backend, key, suppress_errors=True))

    @staticmethod
    def delete_blob_by_key(backend, storage_key, suppress_errors=False):
        if not storage_key:
            return True
        try:
            if backend == "s3":
                bucket = (getattr(settings, "PUBLIC_ATTACHMENTS_S3_BUCKET", "") or "").strip()
                if not bucket:
                    raise RuntimeError("PUBLIC_ATTACHMENTS_S3_BUCKET no está configurado")
                AttachmentService._s3_client().delete_object(Bucket=bucket, Key=storage_key)
            else:
                default_storage.delete(storage_key)
            return True
        except (BotoCoreError, ClientError, OSError, RuntimeError) as exc:
            logger.exception(
                "attachment_delete_failed",
                extra={"storage_backend": backend, "storage_key": storage_key},
            )
            if suppress_errors:
                return False
            raise APIValidationError(
                "attachment_delete_failed",
                "No se pudo eliminar el adjunto. Inténtalo de nuevo.",
                status=503,
            ) from exc

    @staticmethod
    def delete(attachment, delete_record=True):
        AttachmentService.delete_blob_by_key(
            attachment.storage_backend, attachment.storage_key,
        )
        if delete_record:
            attachment.delete()

    @staticmethod
    def cleanup_orphans(cutoff=None, batch_size=500):
        cutoff = cutoff or (
            timezone.now()
            - timedelta(hours=getattr(settings, "ATTACHMENT_ORPHAN_MAX_AGE_HOURS", 24))
        )
        queryset = Attachment.objects.filter(
            comment__isnull=True,
            uploaded_at__lt=cutoff,
        ).order_by("uploaded_at", "id")[:batch_size]
        deleted = failed = 0
        for attachment in list(queryset):
            try:
                with transaction.atomic():
                    if attachment.uploaded_by_id:
                        User.objects.filter(pk=attachment.uploaded_by_id).update(id=F('id'))
                    current = Attachment.objects.select_for_update().filter(
                        pk=attachment.pk, comment__isnull=True, uploaded_at__lt=cutoff,
                    ).first()
                    if current:
                        AttachmentService.delete(current)
                        deleted += 1
            except APIValidationError:
                failed += 1
        return {"deleted": deleted, "failed": failed}

    @staticmethod
    def presigned_url(attachment):
        bucket = getattr(settings, "PUBLIC_ATTACHMENTS_S3_BUCKET", "")
        try:
            return AttachmentService._s3_client().generate_presigned_url(
                "get_object",
                Params={
                    "Bucket": bucket,
                    "Key": attachment.storage_key,
                    "ResponseContentDisposition": content_disposition_header(True, AttachmentService.filename(attachment)),
                    "ResponseContentType": "application/octet-stream",
                },
                ExpiresIn=getattr(settings, "ATTACHMENT_DOWNLOAD_URL_TTL_SECONDS", 300),
            )
        except (BotoCoreError, ClientError) as exc:
            AttachmentService._raise_storage_error(
                "presign", "s3", exc, attachment_id=attachment.id,
            )
