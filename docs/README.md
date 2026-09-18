# Documentación de TicketFlow

## Vigente

Documentos que describen el sistema tal y como funciona hoy. Ante cualquier duda de
arquitectura o despliegue, empieza aquí.

| Documento | Contenido |
|---|---|
| [`architecture.md`](architecture.md) | Infraestructura, flujo de ingesta de email, permisos IAM y de Azure AD |
| [`backend-architecture.md`](backend-architecture.md) | Capa de servicios, modelos, SLA, asignación, migraciones, variables de entorno |
| [`deployment.md`](deployment.md) | Roles de pod, flags de arranque, tareas de beat |
| [`ci-cd-runtime-runbook.md`](ci-cd-runtime-runbook.md) | Procedimiento real de CI/CD, GitOps, ArgoCD, pods y configuración S3 |
| [`email-integration-setup.md`](email-integration-setup.md) | Configuración de los buzones M365 |
| [`public-help-centers.md`](public-help-centers.md) | Help Center público multi-marca (plantillas, obfuscación de contacto, edición in-page) |
| [`attachment-messaging.md`](attachment-messaging.md) | Adjuntos en conversación de tickets |
| [`attachment-storage.md`](attachment-storage.md) | Almacenamiento de adjuntos (S3, deduplicación, límites) |
| [`ticket-conversation.md`](ticket-conversation.md) | Hilo de conversación de un ticket |

**Partes desactualizadas conocidas**: ver la sección homónima en `CLAUDE.md` (no versionado
en este repo) antes de fiarte de un documento sin verificar contra el código.

## Histórico

Snapshots de planificación en un momento dado — no se mantienen al día, son referencia de
por qué se tomó una decisión, no la fuente de verdad del estado actual.

| Documento | Contenido |
|---|---|
| [`archive/roadmap-2026-Q3.md`](archive/roadmap-2026-Q3.md) | Roadmap del Q3 2026 |
| [`archive/sprint-2026-06.md`](archive/sprint-2026-06.md) | Planificación del sprint de junio 2026 |
| [`archive/Sprint-2026-06.docx`](archive/Sprint-2026-06.docx) | Versión .docx del mismo sprint |
| [`archive/promotion-sta-2.2.19.md`](archive/promotion-sta-2.2.19.md) | Notas de promoción a STA de la versión 2.2.19 |
| [`archive/plan-mejoras-frontend-v2.2.0.md`](archive/plan-mejoras-frontend-v2.2.0.md) | Plan de mejoras de frontend de la versión 2.2.0 |
| [`archive/inspect_email.py`](archive/inspect_email.py) | Script de diagnóstico puntual para tickets creados antes del *initial-comment fix*, obsoleto por definición (ver su propio docstring) |
