# Promoción a STA de 2.2.19-dev

Versión funcional: tag `2.2.19-dev`, commit
`448dd3f205d8e43e4f8917db94ea82e07e903801`, validado por el usuario en DEV.
La promoción incluye toda la versión: conversaciones para tickets nuevos e
históricos, adjuntos privados con vistas previas y correo, reporting por producto,
editor del centro de ayuda y correcciones de interfaz.

## Integración con develop

El código de aplicación y los recursos son los del tag validado. Se conserva
además `0053_seed_agent_reply_templates`, ya presente en el historial de STA.
`0059_merge_sta_dev` une esa rama con `0058_attachment_drafts_requests` sin
operaciones ni cambios en dependencias anteriores. Las dos migraciones de
plantillas usan `get_or_create`: respetan las plantillas existentes y sus ajustes.
No se debe renumerar el historial ni ejecutar `migrate --fake`.

Las migraciones de DEV añaden revisiones de artículos, productos, índices y
borradores de adjuntos. Inicializan revisiones de artículos y asignan producto
a tickets existentes a partir de su servicio. Se debe prever tiempo para estos
recorridos del histórico durante el job de migración de STA.

## Configuración previa al despliegue de STA

La aprobación e integración de la PR preceden al CD de STA. Los manifiestos reales
están en **Recordia-Kubernetes**; los ficheros `k8s/` son solo una referencia.
Verificar en los cuatro componentes (web, worker, beat y migrate):

```env
SHORT_ENV=STA
ATTACHMENT_STORAGE_BACKEND=s3
ATTACHMENT_STORAGE_PREFIX=sta
PUBLIC_ATTACHMENTS_S3_BUCKET=sta-ticketflow-filestorage
PUBLIC_ATTACHMENTS_S3_REGION=eu-west-1
HELP_CONTENT_S3_BUCKET=sta-ticketflow-filestorage
HELP_CONTENT_S3_REGION=eu-west-1
```

STA requiere estos valores explícitos: los valores automáticos de esta versión
solo activan S3 en DEV. La configuración del proceso prevalece sobre el `.env`.
Verificar la identidad IAM de STA y los permisos del bucket descritos en
[attachment-storage.md](attachment-storage.md). Mantener los interruptores de
correo, acuses y encuestas de STA; esta promoción no solicita activarlos.
Los correos Graph con adjuntos grandes requieren `Mail.ReadWrite` y `Mail.Send`.

Los adjuntos históricos guardados en disco mantienen su backend. Antes de retirar
pods antiguos, inventariar y conservar los archivos locales disponibles: activar
S3 no los copia ni recupera automáticamente.

## Comprobación de la entrega

- CI y CD de DEV completados: ejecuciones 41643 y 41644; Argo CD Healthy/Synced.
- Pruebas de integración local de ambas rutas de migración, incluidas plantillas
  personalizadas, y suite Django sobre la rama de promoción.
- Tras aprobación y CD de STA: comprobar migraciones, salud, conversación de un
  ticket histórico, un comentario nuevo y las subidas/vistas previas/descargas.
- Ejecutar `python3 main.py check_attachment_storage` y la variante `--size-mb 9`
  en web y worker de STA. El comando crea y elimina solo su propio objeto de prueba.

Para revertir, usar la imagen anterior manteniendo las nuevas columnas y tablas;
no deshacer las migraciones de datos ni borrar plantillas o archivos.
