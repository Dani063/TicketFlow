"""Valores de almacenamiento por entorno, sobrescribibles desde el despliegue."""


def attachment_storage_config(environment):
    # Infra entrega buckets separados. Solo DEV se activa en esta versión;
    # STA requiere configurar explícitamente su bucket al promocionar.
    dev = environment.get("SHORT_ENV", "").strip().upper() == "DEV"
    bucket = environment.get(
        "PUBLIC_ATTACHMENTS_S3_BUCKET", "dev-ticketflow-filestorage" if dev else "",
    ).strip()
    region = environment.get("PUBLIC_ATTACHMENTS_S3_REGION", environment.get("AWS_REGION", "eu-west-1"))
    return {
        "ATTACHMENT_STORAGE_BACKEND": environment.get("ATTACHMENT_STORAGE_BACKEND", "s3" if dev else "auto").strip().lower(),
        "ATTACHMENT_STORAGE_PREFIX": environment.get("ATTACHMENT_STORAGE_PREFIX", "dev" if dev else "").strip("/ "),
        "PUBLIC_ATTACHMENTS_S3_BUCKET": bucket,
        "PUBLIC_ATTACHMENTS_S3_REGION": region,
        "HELP_CONTENT_S3_BUCKET": environment.get("HELP_CONTENT_S3_BUCKET", bucket),
        "HELP_CONTENT_S3_REGION": environment.get("HELP_CONTENT_S3_REGION", region),
    }
