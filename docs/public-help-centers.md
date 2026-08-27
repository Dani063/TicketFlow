# Centros de ayuda públicos

TicketFlow incorpora los antiguos centros de Zendesk de eComFax y Recordia como portales públicos propios. Un visitante anónimo solo puede consultar documentación, buscar, enviar una solicitud o responder una encuesta de satisfacción mediante su token. Los dominios de soporte no permiten entrar en las pantallas internas de TicketFlow.

## Contenido migrado

- 230 artículos de cliente: eComFax (66 ES, 65 EN) y Recordia (49 ES, 50 EN).
- 18 categorías, 65 secciones, destacados, enlaces internos, vídeos y 331 recursos/documentos locales.
- 14 PDF, un manual DOCX y 316 recursos gráficos recuperados y servidos desde TicketFlow.
- EULA de eComFax ES/EN, certificado ENS y declaración LOPDGDD de Recordia recuperados como PDF local.
- Las tres publicaciones de la antigua comunidad se transformaron en artículos informativos ES/EN; no se replica el foro interactivo.
- Cuatro capturas históricas ya eliminadas en Zendesk se omiten para no mostrar imágenes rotas.

La importación autenticada distingue los segmentos de Zendesk: conserva el contenido anónimo y el destinado a clientes autenticados (`249029`), y excluye borradores y documentación exclusiva de agentes (`249009`). Los recursos que dejan de estar referenciados se retiran del árbol estático para que la documentación interna tampoco quede accesible mediante una URL directa.

El snapshot maestro reproducible está en `resources/help_content/help_centers.json`. Para que el artefacto corporativo no dependa de carpetas externas al paquete Python, las migraciones `0051_seed_help_center_content` y `0052_refresh_help_center_content` cargan la copia comprimida e integrada de `src/app/migration_data/help_content_snapshot_0052.py`. Tras refrescar el JSON maestro hay que regenerarla con:

```powershell
python scripts/build_help_content_snapshot.py
```

La prueba `HelpContentSnapshotTests.test_deployable_snapshot_matches_source` impide publicar ambas copias desincronizadas. Para refrescar todo el contenido se requieren `ZENDESK_EMAIL` y `ZENDESK_API_TOKEN`; el comando impide usar `--prune` con un inventario anónimo para no retirar artículos de cliente por error:

```powershell
cd src
$env:USE_SQLITE='1'
$env:DISABLE_AWS_SSM='1'
python main.py import_zendesk_help_centers --snapshot ..\resources\help_content\help_centers.json --download-assets --prune
```

Los contenidos también se pueden editar desde `/django-admin/`; el acceso exige un usuario `is_staff`.

## Formulario anónimo

El producto, servicio y marca se resuelven en servidor a partir del centro de ayuda. El navegador no puede elegirlos ni suplantarlos. Al enviar:

1. se crea o reutiliza un solicitante `End user` con contraseña inutilizable;
2. se crea un ticket normal con canal `web` y servicio `ecomfax` o `recordia`;
3. se ejecutan asignación, SLA y automatizaciones existentes;
4. se encola la confirmación habitual y el cliente continúa respondiendo al correo;
5. los adjuntos se validan, se guardan como privados y solo se descargan mediante una vista autorizada o una URL S3 firmada de cinco minutos.

La protección antiabuso combina CSRF, campo trampa, tiempo mínimo firmado y límites por hashes HMAC de IP/correo. No se persisten IP ni correo en claro en el registro de intentos.

## Configuración de producción

Variables principales:

- `ALLOWED_HOSTS`: debe incluir `support.ecomfax.com` y `support.recordia.net`.
- `CSRF_TRUSTED_ORIGINS`: debe incluir ambos orígenes HTTPS.
- `ECOMFAX_HELP_PUBLIC_URL` y `RECORDIA_HELP_PUBLIC_URL`: bases usadas en correos y encuestas.
- `PUBLIC_ECOMFAX_BRAND_NAME`: por defecto `eComFax`.
- `PUBLIC_RECORDIA_BRAND_NAME`: por defecto `Comunycarse Helpdesk`; cambiarlo si se crea una marca/buzón Recordia específico.
- `PUBLIC_ATTACHMENTS_S3_BUCKET`: obligatorio en producción con varias réplicas. El bucket debe ser privado y el rol del pod necesita `s3:PutObject`/`s3:GetObject` sobre `attachments/tickets/*`.
- `PUBLIC_CLIENT_IP_HEADER=HTTP_X_FORWARDED_FOR`: usarlo solo cuando el ingress de confianza sanea o añade esa cabecera; TicketFlow toma el último salto. Sin proxy, mantener `REMOTE_ADDR`.
- `OUTBOUND_EMAIL_ENABLED=true`: necesario para enviar confirmaciones y encuestas.

Antes del cambio de DNS hay que provisionar fuera del repositorio:

1. certificados TLS para ambos dominios;
2. reglas de ingress/ALB que lleven ambos hosts al servicio web de TicketFlow;
3. DNS de `support.ecomfax.com` y `support.recordia.net` hacia ese ingress;
4. bucket S3 privado y permisos IAM;
5. prueba de correo de ambos productos y verificación de que las respuestas vuelven al ticket.

Conviene mantener los dominios de Zendesk sin cambios hasta validar el nuevo ingress. Las URLs antiguas `/hc/<locale>/articles/<id>-<slug>` redirigen permanentemente al artículo migrado cuando llegan a TicketFlow.

## Verificación

```powershell
cd src
$env:USE_SQLITE='1'
$env:DISABLE_AWS_SSM='1'
python main.py check
python main.py test app.tests_public_help
python main.py test app
```
