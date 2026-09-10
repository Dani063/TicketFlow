# Adjuntos de mensajería

Alcance aprobado: tarjetas con nombre/tipo/tamaño, miniaturas, visor privado de
imágenes/PDF/texto/CSV, cola de subida con progreso y reintentos, selección/drop/
pegado, adjuntos en el primer mensaje, respuestas de solo archivos, aislamiento
por pestaña e idempotencia. Fuera de alcance: editar documentos, ZIP conjunto y
biblioteca global de archivos.

## Diseño

Se conserva Lato, nombre a 14px semibold y metadatos a 12–13px. Paleta basada en
los tokens existentes: acción violeta #7c3aed, superficie clara #ffffff, superficie
oscura #1e293b, bordes claros #e2e8f0, bordes oscuros #334155 y texto secundario
#64748b (con su equivalente del tema oscuro). Los colores de estado no sustituyen
las etiquetas. Sin fondos publicitarios ni animaciones decorativas.

La tarjeta alinea icono/miniatura, nombre y metadatos a la izquierda; vista previa
y descarga son acciones distintas. El editor separa los archivos de su barra de
acciones. El espacio disponible determina las columnas, sin recortar nombres o
controles. Las imágenes son la información visual principal; los otros archivos
se presentan como documentos compactos, no como miniaturas ficticias.

## Contrato y comportamiento

- Un controlador por panel mantiene su cola; no hay IDs de adjuntos globales.
- La subida acaba solo cuando responde el servidor, no al alcanzar 100% de bytes.
- Un error conserva el borrador. Se reintenta o se retira el archivo antes de publicar.
- Los identificadores de subida/publicación permiten repetir una solicitud sin
  crear copias si la respuesta anterior se perdió.
- Los borradores de archivos pertenecen a su usuario y no crean un ticket vacío.
- Los ficheros se asocian al comentario antes de preparar su correo.
- Las vistas previas y descargas comprueban acceso al ticket, al comentario
  interno y al propietario de los pendientes. El almacenamiento S3 sigue privado.
- La vista previa de texto/CSV es limitada, no ejecuta contenido y no envía datos
  a visores externos. Los formatos no compatibles siguen disponibles para descargar.
- Los nombres visibles mantienen Unicode, espacios y extensión; las claves físicas
  se sanean por separado. Los datos históricos ausentes no se inventan.

## Verificación antes de publicar

Pruebas backend de permisos, nombres hostiles/Unicode, límites, duplicados,
cancelación, borradores, primer mensaje, solo archivos, privacidad y previews.
Pruebas frontend de estados, errores/reintentos, doble clic, cambio de pestaña,
recarga, galerías, zoom, teclado y temas claro/oscuro a distintos anchos.
Regresión de comentarios públicos/internos, actualización de campos, primer
correo y contenido de sus adjuntos. No enviar correos reales durante QA local.

## Implementación y comprobaciones (10/09/2026)

- Migración `0058_attachment_drafts_requests`: borradores privados e identificadores
  persistentes de subida, cancelación y publicación. No exige importar datos.
- `attachments.js` / `attachments.css`: controlador por panel, tarjetas, cola y
  visores. `create_ticket.js` integra creación y respuestas; el gestor de pestañas
  conserva los JSON de metadatos y espera la limpieza al cerrar un borrador.
- PDF.js 6.3.289 se sirve localmente bajo `tickets/vendor/pdfjs/`, con licencia y
  hashes documentados. Motor y worker mantienen los bytes oficiales; extensión
  `.js` para que los servidores Windows devuelvan el MIME correcto.
- 226 pruebas Django pasan, incluidas 43 nuevas del contrato de adjuntos. Las tres
  expectativas antiguas afectadas se actualizan con comprobaciones de rollback,
  rechazo completo de IDs ajenos y conservación del nombre Unicode.
- 13 pruebas aisladas de navegador cubren el componente, también cancelaciones
  fallidas, límites, HTML inerte, PDF y liberación de recursos.
- Las pruebas E2E simulan 502 y pérdida de respuesta después del commit, comprueban
  descarga/nombre/contenido, primer mensaje, mensajes de solo archivos, notas
  internas, doble clic, campos sin comentario vacío, pegado/drop y pestañas.
- La prueba visual genera capturas claro/oscuro a 1540, 1024, 760 y 390 px y
  comprueba imagen cargada, píxeles del PDF, zoom, galería, Escape y foco restaurado.
- Cinco imágenes pendientes se comprueban en 1024×768 y 390×844: la cola tiene
  desplazamiento propio y el editor y envío permanecen visibles y accesibles.

### Repetir QA local

Se necesita una instancia DEBUG con SQLite y almacenamiento local, **correo
saliente, encuestas e IA desactivados**, Playwright y Chrome instalados.
`scripts/seed_attachment_demo.py` crea únicamente datos ficticios locales y es
idempotente. Se ejecuta desde la raíz del repositorio mediante el shell Django:

```powershell
python src/main.py shell -c "exec(open('scripts/seed_attachment_demo.py', encoding='utf-8').read())"
# Usar el ticket_id devuelto por el script:
$env:ATTACHMENT_QA_EMAIL = 'attachments-qa@ticketflow.local'
$env:ATTACHMENT_QA_TICKET = '<ticket_id local>'
node --test tests/js/attachments.test.js
node scripts/test_attachment_browser.cjs
node scripts/test_attachment_visual.cjs
```

Los E2E rechazan hosts no locales y crean tickets/comentarios ficticios señalados
como QA. Las capturas quedan en `tmp/attachment-browser/`. No probarlos contra
DEV, STA o PRO. Para el siguiente despliegue se necesitan tanto la migración como
los nuevos estáticos completos, incluido el directorio del visor PDF.

Publicación autorizada para DEV como `2.2.17-dev`, junto con los fixes preexistentes
de Claude (pestañas y paginación). Se excluyen temporales, capturas y datos locales.
La CI de Azure construye el tag; el CD `DEV-CD-Kubernetes` usa el recurso
`templates` en `refs/heads/feature/kubernetes` y `kubernetes-repo` en
`refs/heads/develop`. No promocionar a STA como parte de esta entrega.
La prueba real de S3 en DEV debe repetirse al publicar; las pruebas descritas
arriba usan almacenamiento local y simulaciones del contrato S3.
