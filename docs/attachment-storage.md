# Almacenamiento privado de adjuntos

## Solicitud para Infra

Texto listo para enviar (Infra puede adaptar el nombre a su convención):

> Necesitamos un bucket S3 privado para los adjuntos de TicketFlow en STA, en
> `eu-west-1`. El bucket no debe tener acceso público y debe tener activados los
> cuatro controles de S3 Block Public Access, Object Ownership `Bucket owner
> enforced`, cifrado por defecto y una policy que deniegue conexiones sin TLS.
>
> Los pods `ticketflow-web` y `ticketflow-worker` necesitan acceso mediante una
> identidad IAM de workload (EKS Pod Identity o IRSA), sin access keys estáticas.
> La identidad debe poder hacer `s3:GetObject`, `s3:PutObject`,
> `s3:DeleteObject` y `s3:AbortMultipartUpload` únicamente sobre
> `arn:aws:s3:::<BUCKET>/*`. No necesitan acceso público ni listar otros
> buckets. Preferimos un ServiceAccount dedicado llamado `ticketflow`.
>
> Como protección operativa, solicitamos versionado y una lifecycle rule para
> abortar multipart uploads incompletos después de 7 días; las versiones no
> actuales pueden expirar según la política corporativa (por ejemplo, 30 días).
>
> Cuando esté creado, necesitamos: nombre del bucket, región, ARN del rol IAM y
> nombre/namespace del ServiceAccount asociado. No necesitamos credenciales ni
> secretos de AWS.

Policy IAM mínima de referencia:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "TicketFlowPrivateAttachments",
      "Effect": "Allow",
      "Action": [
        "s3:GetObject",
        "s3:PutObject",
        "s3:DeleteObject",
        "s3:AbortMultipartUpload"
      ],
      "Resource": "arn:aws:s3:::<BUCKET>/*"
    }
  ]
}
```

Si Infra elige SSE-KMS en lugar de SSE-S3, deberá añadir al rol los permisos
mínimos de uso de la clave y comunicar el alias/ARN para ajustar la aplicación.
La implementación actual solicita SSE-S3 (`AES256`) en cada escritura.

## Comportamiento implementado

- En local, `ATTACHMENT_STORAGE_BACKEND=auto` usa `MEDIA_ROOT` si no hay bucket.
- En Kubernetes se activará `ATTACHMENT_STORAGE_BACKEND=s3`. En este modo la
  ausencia del bucket o un `AccessDenied` devuelve un error controlado 503; no
  se guarda una copia efímera en otro pod.
- Los objetos usan claves aleatorias bajo
  `<prefijo>/attachments/tickets/<ticket-id>/`; el nombre original saneado queda
  en base de datos.
- El bucket nunca se expone al navegador. TicketFlow valida permisos sobre el
  ticket y genera una URL firmada de cinco minutos para la descarga.
- Se admiten hasta 5 archivos, 15 MB por archivo y 25 MB en conjunto.
- Una subida pendiente se puede eliminar desde el editor. Las abandonadas se
  borran automáticamente después de 24 horas mediante Celery Beat.
- Si falla la escritura de base de datos tras subir el objeto, la aplicación
  elimina el objeto compensatoriamente.
- Los adjuntos de una respuesta pública del agente viajan también en el correo
  saliente (SES o Microsoft Graph).
- Los adjuntos de correos entrantes de Microsoft 365 se guardan en el ticket.
  Se omiten imágenes inline de firma, tipos no permitidos y excesos de tamaño.
  Los reintentos son idempotentes.

## Variables

```env
ATTACHMENT_STORAGE_BACKEND=s3
ATTACHMENT_STORAGE_PREFIX=sta
PUBLIC_ATTACHMENTS_S3_BUCKET=<bucket-entregado-por-infra>
PUBLIC_ATTACHMENTS_S3_REGION=eu-west-1
PUBLIC_ATTACHMENT_MAX_FILES=5
PUBLIC_ATTACHMENT_MAX_BYTES=15728640
PUBLIC_ATTACHMENT_MAX_TOTAL_BYTES=26214400
ATTACHMENT_ORPHAN_MAX_AGE_HOURS=24
ATTACHMENT_DOWNLOAD_URL_TTL_SECONDS=300
OUTBOUND_EMAIL_ATTACHMENT_MAX_BYTES=26214400
```

`HELP_CONTENT_S3_BUCKET` puede reutilizar el mismo bucket; los recursos del
centro de ayuda se guardan bajo `help-content/`. Si se usa otro bucket, se debe
conceder el mismo conjunto mínimo de acciones para ese ARN.

## Activación en STA

1. Infra crea bucket, policy e identidad de workload y devuelve sus nombres.
2. Añadir `serviceAccountName: ticketflow` a los deployments web y worker si
   Infra ha elegido ese ServiceAccount.
3. Descomentar el bucket en `k8s/configmap.yaml` y cambiar el backend a `s3`.
4. Aplicar ConfigMap y deployments; reiniciar web y worker.
5. Probar subida, visualización, descarga, eliminación de una subida pendiente,
   respuesta pública con adjunto y correo entrante con adjunto.
6. Verificar en S3 que el objeto es privado, está cifrado y usa el prefijo de
   STA. Revisar logs para confirmar que no hay `attachment_storage_failed`.

Rollback: volver temporalmente a la imagen anterior. No cambiar a `local` en un
cluster con varias réplicas, porque los archivos quedarían ligados al pod que
atendió la subida.
