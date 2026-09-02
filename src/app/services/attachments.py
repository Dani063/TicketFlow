"""Almacenamiento y descarga segura de adjuntos de tickets."""

import logging
import os
import re
import uuid
from datetime import timedelta

import boto3
from botocore.exceptions import BotoCoreError, ClientError
from django.conf import settings
from django.core.files.storage import default_storage
from django.utils import timezone
from django.urls import reverse

from app.api import APIValidationError
from app.models import Attachment


_SAFE_CHARS = re.compile(r"[^A-Za-z0-9._-]+")
logger = logging.getLogger(__name__)


class AttachmentService:
    STORAGE_ERROR_MESSAGE = "No se pudo guardar el adjunto. Inténtalo de nuevo en unos minutos."

    @staticmethod
    def validate_files(files):
        files = list(files or [])
        max_files = getattr(settings, "PUBLIC_ATTACHMENT_MAX_FILES", 5)
        max_bytes = getattr(settings, "PUBLIC_ATTACHMENT_MAX_BYTES", 15 * 1024 * 1024)
        max_total_bytes = getattr(settings, "PUBLIC_ATTACHMENT_MAX_TOTAL_BYTES", 25 * 1024 * 1024)
        allowed = getattr(settings, "PUBLIC_ATTACHMENT_ALLOWED_EXTENSIONS", set())
        if len(files) > max_files:
            raise APIValidationError("too_many_files", f"Puedes adjuntar un máximo de {max_files} archivos.", status=400)
        if sum(getattr(uploaded, "size", 0) for uploaded in files) > max_total_bytes:
            mb = max_total_bytes // (1024 * 1024)
            raise APIValidationError(
                "attachments_too_large",
                f"El conjunto de adjuntos debe ocupar como máximo {mb} MB.",
                status=400,
            )
        for uploaded in files:
            name = os.path.basename((uploaded.name or "archivo").replace("\\", "/"))
            ext = os.path.splitext(name)[1].lower().lstrip(".")
            if not ext or ext not in allowed:
                raise APIValidationError("file_type_not_allowed", f"El tipo de archivo .{ext or '?'} no está permitido.", status=400)
            if uploaded.size > max_bytes:
                mb = max_bytes // (1024 * 1024)
                raise APIValidationError("file_too_large", f"Cada archivo debe ocupar como máximo {mb} MB.", status=400)
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
    def _storage_key(ticket, original_name):
        prefix = (getattr(settings, "ATTACHMENT_STORAGE_PREFIX", "") or "").strip("/ ")
        parts = [part for part in (prefix, "attachments", "tickets", str(ticket.id)) if part]
        return "/".join(parts + [f"{uuid.uuid4().hex}-{original_name}"])

    @staticmethod
    def _s3_client():
        return boto3.client(
            "s3",
            region_name=getattr(settings, "PUBLIC_ATTACHMENTS_S3_REGION", None),
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
    def save(uploaded, ticket, user, comment=None):
        AttachmentService.validate_files([uploaded])
        original_name = AttachmentService.safe_name(uploaded.name)
        storage_key = AttachmentService._storage_key(ticket, original_name)
        bucket = (getattr(settings, "PUBLIC_ATTACHMENTS_S3_BUCKET", "") or "").strip()
        backend = AttachmentService._storage_backend()

        try:
            if backend == "s3":
                extra = {
                    "ContentType": getattr(uploaded, "content_type", "application/octet-stream"),
                    "ServerSideEncryption": "AES256",
                }
                AttachmentService._s3_client().upload_fileobj(
                    uploaded, bucket, storage_key, ExtraArgs=extra,
                )
            else:
                storage_key = default_storage.save(storage_key, uploaded)
        except (BotoCoreError, ClientError, OSError) as exc:
            AttachmentService._raise_storage_error(
                "upload", backend, exc, ticket_id=ticket.id, original_name=original_name,
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
            )
        except Exception:
            # Si la base de datos falla después de almacenar el objeto, no debe
            # quedar un fichero privado sin registro ni propietario funcional.
            AttachmentService.delete_blob_by_key(backend, storage_key, suppress_errors=True)
            raise
        attachment.file_url = reverse("attachment_download", args=[attachment.id])
        attachment.save(update_fields=["file_url"])
        return attachment

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
                AttachmentService.delete(attachment)
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
                    "ResponseContentDisposition": f'attachment; filename="{attachment.original_name}"',
                },
                ExpiresIn=getattr(settings, "ATTACHMENT_DOWNLOAD_URL_TTL_SECONDS", 300),
            )
        except (BotoCoreError, ClientError) as exc:
            AttachmentService._raise_storage_error(
                "presign", "s3", exc, attachment_id=attachment.id,
            )
