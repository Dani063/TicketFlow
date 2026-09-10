import io
import os
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from urllib.parse import parse_qs, urlsplit

import boto3
import requests
from botocore.exceptions import ClientError
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase, override_settings

from TicketFlow.settings import _load_project_environment
from app.services.attachments import AttachmentService


class EnvironmentPrecedenceTests(SimpleTestCase):
    def test_kubernetes_storage_settings_win_over_stale_env_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".env"
            path.write_text(
                "ATTACHMENT_STORAGE_BACKEND=local\n"
                "PUBLIC_ATTACHMENTS_S3_BUCKET=old-bucket\n"
                "PUBLIC_ATTACHMENTS_S3_REGION=eu-west-1\n", encoding="utf-8",
            )
            with patch.dict(os.environ, {
                "ATTACHMENT_STORAGE_BACKEND": "s3",
                "PUBLIC_ATTACHMENTS_S3_BUCKET": "dev-ticketflow-filestorage",
            }, clear=True):
                _load_project_environment(path)
                self.assertEqual(os.environ["ATTACHMENT_STORAGE_BACKEND"], "s3")
                self.assertEqual(os.environ["PUBLIC_ATTACHMENTS_S3_BUCKET"], "dev-ticketflow-filestorage")
                self.assertEqual(os.environ["PUBLIC_ATTACHMENTS_S3_REGION"], "eu-west-1")

    def test_local_configuration_still_loads_from_env_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".env"
            path.write_text("ATTACHMENT_STORAGE_BACKEND=local\n", encoding="utf-8")
            with patch.dict(os.environ, {}, clear=True):
                _load_project_environment(path)
                self.assertEqual(os.environ["ATTACHMENT_STORAGE_BACKEND"], "local")


class SignedDownloadTests(SimpleTestCase):
    @override_settings(
        PUBLIC_ATTACHMENTS_S3_BUCKET="dev-ticketflow-filestorage",
        PUBLIC_ATTACHMENTS_S3_REGION="eu-west-1",
        ATTACHMENT_DOWNLOAD_URL_TTL_SECONDS=300,
    )
    def test_download_uses_sigv4_and_temporary_identity_credentials(self):
        session = boto3.Session(
            aws_access_key_id="test-key", aws_secret_access_key="test-secret",
            aws_session_token="test-session", region_name="eu-west-1",
        )
        attachment = SimpleNamespace(storage_key="dev/attachments/tickets/1/a.txt", original_name="a.txt")
        with patch("app.services.attachments.boto3", session):
            url = AttachmentService.presigned_url(attachment)
        query = parse_qs(urlsplit(url).query)
        self.assertEqual(query["X-Amz-Algorithm"], ["AWS4-HMAC-SHA256"])
        self.assertEqual(query["X-Amz-Security-Token"], ["test-session"])
        self.assertEqual(query["X-Amz-Expires"], ["300"])
        self.assertEqual(query["response-content-disposition"], ['attachment; filename="a.txt"'])


@override_settings(
    ATTACHMENT_STORAGE_BACKEND="s3",
    PUBLIC_ATTACHMENTS_S3_BUCKET="dev-ticketflow-filestorage",
    PUBLIC_ATTACHMENTS_S3_REGION="eu-west-1",
    ATTACHMENT_STORAGE_PREFIX="dev",
    PUBLIC_ATTACHMENT_MAX_BYTES=15 * 1024 * 1024,
)
class StorageDeploymentCheckTests(SimpleTestCase):
    def setUp(self):
        self.client = MagicMock()
        self.client.get_object.return_value = {
            "Body": io.BytesIO(b"T" * 1024 * 1024),
            "ServerSideEncryption": "AES256",
        }
        self.client.generate_presigned_url.return_value = "https://example.invalid/?private-signature"
        self.response = MagicMock()
        self.response.__enter__.return_value = self.response
        self.response.content = b"T" * 1024 * 1024
        self.client_patch = patch.object(AttachmentService, "_s3_client", return_value=self.client)
        self.client_patch.start()
        self.addCleanup(self.client_patch.stop)
        self.http_patch = patch("app.management.commands.check_attachment_storage.requests.get", return_value=self.response)
        self.http_patch.start()
        self.addCleanup(self.http_patch.stop)

    def test_checks_download_and_cleans_only_its_unique_probe(self):
        output = io.StringIO()
        call_command("check_attachment_storage", stdout=output)
        upload_args = self.client.upload_fileobj.call_args.args
        bucket, key = upload_args[1:]
        self.assertEqual(bucket, "dev-ticketflow-filestorage")
        self.assertTrue(key.startswith("dev/attachments/checks/"))
        self.client.delete_object.assert_called_once_with(Bucket=bucket, Key=key)
        self.assertIn("OK:", output.getvalue())
        self.assertNotIn("private-signature", output.getvalue())

    def test_failed_upload_reports_failure_and_attempts_cleanup(self):
        self.client.upload_fileobj.side_effect = ClientError(
            {"Error": {"Code": "AccessDenied", "Message": "Denied"}}, "PutObject",
        )
        with self.assertRaises(CommandError):
            call_command("check_attachment_storage", stdout=io.StringIO())
        self.client.delete_object.assert_called_once()

    def test_signed_download_failure_does_not_disclose_url(self):
        self.response.raise_for_status.side_effect = requests.HTTPError(
            "403 https://example.invalid/?private-signature",
        )
        with self.assertRaises(CommandError) as error:
            call_command("check_attachment_storage", stdout=io.StringIO())
        self.assertNotIn("private-signature", str(error.exception))
        self.client.delete_object.assert_called_once()

    def test_cleanup_failure_is_not_reported_as_success(self):
        self.client.delete_object.side_effect = ClientError(
            {"Error": {"Code": "AccessDenied", "Message": "Denied"}}, "DeleteObject",
        )
        with self.assertRaisesMessage(CommandError, "Fallo al limpiar"):
            call_command("check_attachment_storage", stdout=io.StringIO())

    def test_rejects_probe_larger_than_the_attachment_limit(self):
        with self.assertRaises(CommandError):
            call_command("check_attachment_storage", size_mb=16, stdout=io.StringIO())
        self.client.upload_fileobj.assert_not_called()
