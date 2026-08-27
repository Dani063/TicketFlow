"""Almacenamiento y descarga segura de adjuntos de tickets."""

import os
import re
import uuid

import boto3
from django.conf import settings
from django.core.files.storage import default_storage
from django.urls import reverse

from app.api import APIValidationError
from app.models import Attachment


_SAFE_CHARS = re.compile(r"[^A-Za-z0-9._-]+")


class AttachmentService:
    @staticmethod
    def validate_files(files):
        files = list(files or [])
        max_files = getattr(settings, "PUBLIC_ATTACHMENT_MAX_FILES", 5)
        max_bytes = getattr(settings, "PUBLIC_ATTACHMENT_MAX_BYTES", 15 * 1024 * 1024)
        allowed = getattr(settings, "PUBLIC_ATTACHMENT_ALLOWED_EXTENSIONS", set())
        if len(files) > max_files:
            raise APIValidationError("too_many_files", f"Puedes adjuntar un máximo de {max_files} archivos.", status=400)
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
    def save(uploaded, ticket, user, comment=None):
        AttachmentService.validate_files([uploaded])
        original_name = AttachmentService.safe_name(uploaded.name)
        storage_key = f"attachments/tickets/{ticket.id}/{uuid.uuid4().hex}-{original_name}"
        bucket = (getattr(settings, "PUBLIC_ATTACHMENTS_S3_BUCKET", "") or "").strip()
        backend = "s3" if bucket else "local"

        if backend == "s3":
            client = boto3.client("s3", region_name=getattr(settings, "PUBLIC_ATTACHMENTS_S3_REGION", None))
            extra = {"ContentType": getattr(uploaded, "content_type", "application/octet-stream"), "ServerSideEncryption": "AES256"}
            client.upload_fileobj(uploaded, bucket, storage_key, ExtraArgs=extra)
        else:
            storage_key = default_storage.save(storage_key, uploaded)

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
        attachment.file_url = reverse("attachment_download", args=[attachment.id])
        attachment.save(update_fields=["file_url"])
        return attachment

    @staticmethod
    def open_local(attachment):
        return default_storage.open(attachment.storage_key, "rb")

    @staticmethod
    def presigned_url(attachment):
        bucket = getattr(settings, "PUBLIC_ATTACHMENTS_S3_BUCKET", "")
        client = boto3.client("s3", region_name=getattr(settings, "PUBLIC_ATTACHMENTS_S3_REGION", None))
        return client.generate_presigned_url(
            "get_object",
            Params={
                "Bucket": bucket,
                "Key": attachment.storage_key,
                "ResponseContentDisposition": f'attachment; filename="{attachment.original_name}"',
            },
            ExpiresIn=300,
        )
