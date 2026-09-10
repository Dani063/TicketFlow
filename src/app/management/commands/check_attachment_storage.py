"""Comprueba S3 con la identidad del proceso, sin crear tickets ni enviar correo."""

import hashlib
import io
import uuid

import requests
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from app.services.attachments import AttachmentService


class Command(BaseCommand):
    help = "Comprueba subida, lectura, descarga firmada y borrado de un objeto temporal en S3"
    requires_system_checks = []

    def add_arguments(self, parser):
        parser.add_argument(
            "--size-mb", type=int, default=1,
            help="Tamaño del archivo de prueba; usar 9 para comprobar multipart",
        )

    def handle(self, *args, **options):
        size = options["size_mb"] * 1024 * 1024
        if not 0 < size <= settings.PUBLIC_ATTACHMENT_MAX_BYTES:
            raise CommandError("El tamaño debe ser positivo y no superar el límite de adjuntos.")
        if AttachmentService._storage_backend() != "s3":
            raise CommandError("La comprobación requiere el backend S3 y un bucket configurado.")

        bucket = settings.PUBLIC_ATTACHMENTS_S3_BUCKET.strip()
        prefix = settings.ATTACHMENT_STORAGE_PREFIX.strip("/ ")
        key = "/".join(part for part in (
            prefix, "attachments", "checks", f"{uuid.uuid4().hex}.txt",
        ) if part)
        content = b"T" * size
        expected = hashlib.sha256(content).digest()
        client = AttachmentService._s3_client()
        self.stdout.write(f"S3: {bucket}; región: {settings.PUBLIC_ATTACHMENTS_S3_REGION}")
        errors = []
        try:
            client.upload_fileobj(
                io.BytesIO(content), bucket, key,
                ExtraArgs={"ContentType": "text/plain", "ServerSideEncryption": "AES256"},
            )
            stored = client.get_object(Bucket=bucket, Key=key)
            try:
                if hashlib.sha256(stored["Body"].read()).digest() != expected:
                    raise CommandError("El contenido leído desde S3 no coincide con el original.")
            finally:
                stored["Body"].close()
            if stored.get("ServerSideEncryption") != "AES256":
                raise CommandError("El objeto no tiene el cifrado SSE-S3 esperado.")

            url = client.generate_presigned_url(
                "get_object", Params={"Bucket": bucket, "Key": key}, ExpiresIn=60,
            )
            # No registrar la URL: contiene credenciales temporales de descarga.
            with requests.get(url, timeout=(5, 30)) as response:
                response.raise_for_status()
                if hashlib.sha256(response.content).digest() != expected:
                    raise CommandError("La descarga firmada no coincide con el archivo original.")
        except Exception as exc:
            # Los errores HTTP pueden incluir la URL firmada. Solo se muestra
            # el tipo de error, nunca su representación completa.
            errors.append(f"Fallo al comprobar subida/lectura/descarga: {type(exc).__name__}")
        finally:
            try:
                client.delete_object(Bucket=bucket, Key=key)
            except Exception as exc:
                errors.append(f"Fallo al limpiar el objeto {key}: {type(exc).__name__}")

        if errors:
            raise CommandError("; ".join(errors))
        self.stdout.write(self.style.SUCCESS(
            f"OK: {options['size_mb']} MB subidos, cifrados, leídos, descargados y borrados."
        ))
