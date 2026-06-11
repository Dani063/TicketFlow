# Arquitectura del backend (capa de servicios)

> Refactor del backend monolítico (`views.py` + `tasks.py`) a una arquitectura por
> capas. Las vistas y tareas pasan a ser *finas*: validan/serializan y delegan toda
> la lógica de negocio en `app/services/`. Este documento describe los archivos y la
> lógica nuevos. Para infraestructura (Celery/SQS/SSM) ver [architecture.md](architecture.md).

## Visión general por capas

```
HTTP (views.py)            Celery (tasks.py)
      │                          │
      ▼                          ▼
 ┌──────────────────────────────────────────┐
 │  app/services/  (lógica de negocio)        │
 │  TicketService · CommentService            │
 │  MergeService · AssignmentService          │
 │  AutomationService · SLAService            │
 │  EmailIngestionService · NotificationService│
 │  OutboundEmailService · ResponseTemplateService│
 │  TicketAIService · ai (Azure OpenAI)        │
 │  msgraph (token Graph) · metrics            │
 └──────────────────────────────────────────┘
      │            │            │
      ▼            ▼            ▼
 permissions    sanitizers    api (envelope JSON + errores)
      │
      ▼
   models (ORM / MySQL)
```

Reglas de la arquitectura:

- **Las vistas no contienen lógica de negocio.** Parsean la petición, llaman a un
  servicio y traducen el resultado/excepción a JSON con los helpers de `api.py`.
- **Los servicios son la única puerta a las mutaciones de dominio.** Cada operación
  que toca varias tablas es `@transaction.atomic` y usa `select_for_update()` cuando
  hay riesgo de carrera.
- **Permisos centralizados** en `permissions.py` — nunca se compara el `role_name`
  a mano en las vistas.
- **Idempotencia** en los flujos que pueden reintentarse (email, automatizaciones).

---

## Módulos transversales

### `app/api.py` — contrato HTTP/JSON

Unifica el formato de respuesta y el manejo de errores de las APIs.

| Elemento | Uso |
|---|---|
| `json_ok(data=None, **extra)` | Respuesta de éxito `{"ok": true, "data": ...}` |
| `json_error(code, message, status)` | Error `{"ok": false, "error": {"code", "message"}, "detail": message}` |
| `parse_json_body(request)` | `json.loads` del body; lanza `APIValidationError("invalid_json")` |
| `APIValidationError(code, message, status)` | Excepción de negocio. Los servicios la lanzan; las vistas la capturan y la convierten con `json_error`. |
| `@api_login_required` | 401 JSON si no autenticado, 403 si inactivo (en vez de redirigir a login como `@login_required`). |
| `@api_permission_required(predicate, ...)` | 401/403 JSON según un predicado de `permissions.py`. |

> El campo `detail` (y a veces campos planos duplicados como `assignee_id`) se
> mantienen por **compatibilidad** con el frontend existente, que lee algunas
> respuestas en formato antiguo.

### `app/permissions.py` — roles y visibilidad

Fuente única de verdad sobre roles. Normaliza `role_name` (case-insensitive, ES/EN) y
trata al `is_superuser` siempre como agente/admin.

| Función | Significado |
|---|---|
| `is_admin(user)` | superuser o rol en `{admin, administrator, administrador}` |
| `is_agent(user)` | superuser o rol de staff (agent, soporte, supervisor, manager, lead…) |
| `is_end_user(user)` | rol cliente (`end user`, `cliente`, `customer`) |
| `can_view_ticket(user, t)` | agente, o requester/creador/CC del ticket |
| `can_update_ticket(user, t)` | solo agentes |
| `can_comment_ticket(user, t)` | igual que `can_view_ticket` |
| `can_manage_users(user)` | solo admin |
| `agent_user_filter()` | `Q(...)` para filtrar el queryset de usuarios que son agentes |

### `app/sanitizers.py` — saneado de HTML de email

`sanitize_email_html(html)` limpia el HTML entrante de correos con **bleach** sobre una
allow-list de etiquetas/atributos/protocolos. Puntos clave:

- Si bleach no está instalado, hace *fallback* a `html.escape` (degrada seguro).
- El atributo `style` se sanea con `CSSSanitizer` (bleach `[css]` → `tinycss2`) contra
  `ALLOWED_CSS_PROPERTIES`. **Sin** el `CSSSanitizer`, bleach ≥5 dejaría pasar el CSS
  inline sin filtrar (vector XSS vía CSS).
- Elimina restos de spans vacíos de Quill (`ql-*`).

---

## Servicios de dominio (`app/services/`)

### `TicketService` — `tickets.py`

Orquesta el ciclo de vida del ticket. Punto de entrada desde la vista:
`create_or_update_from_post(actor, post_data, ticket_id=None)`.

- `_payload_from_post` normaliza el `POST` (estados/prioridades válidos, ints opcionales,
  listas de tags/ccs, flags booleanos) a un `payload` homogéneo.
- `create_ticket` (atómico): valida solicitante y permisos, auto-asigna vía
  `AssignmentService` si no hay asignado, crea el ticket, aplica `SLAService.apply_policy`,
  emite `TicketEvent("created")`, añade el comentario inicial, notifica y dispara
  automatizaciones (`event="ticket_created"`).
- `update_ticket` (atómico, `select_for_update`): comprueba `can_update_ticket`, registra
  un `TicketEvent` por cada campo cambiado (status, priority, subject, assignee, grupo,
  tags), re-aplica SLA y lanza automatizaciones (`event="ticket_updated"`).

### `CommentService` — `comments.py`

`add_comment(ticket, actor, content, ...)` (atómico):

- Exige `can_comment_ticket`; rechaza comentarios vacíos.
- Los comentarios de **end users** se fuerzan a públicos (`is_public=True`).
- Permite cambio de estado *inline* (`new_status`), validado y con su `TicketEvent`.
- Vincula adjuntos pendientes, registra primera respuesta SLA y notifica.
- Devuelve `(comment, applied_status)`.

### `MergeService` — `merge.py`

Fusión de tickets, atómica y validada:

- `merge_ticket(source_id, target_id, actor)` y `bulk_merge(source_ids, target_id, actor)`.
- Mueve `Comment` y `TicketEvent` del origen al destino, marca el origen `merged_into` +
  `closed`, registra `TicketEvent("merge")` y notifica.
- Bloquea fusionarse consigo mismo, origen ya fusionado o destino inválido.

### `AssignmentService` — `assignment.py`

Asignación automática basada en **reglas de BD** (sustituye al pool hardcodeado anterior).

- `choose_assignee(group_id, service, channel)`: evalúa `AssignmentRule` activas, las
  puntúa por especificidad (grupo+servicio+canal) y elige al miembro con menor carga
  relativa (`tickets_abiertos / weight`), respetando `capacity`.
- **Fallback**: si ninguna regla aplica, balancea entre los primeros 50 agentes por carga.
- `auto_assign_unassigned()`: tarea periódica que asigna tickets sin asignado y emite
  `TicketEvent` + métrica `tickets.auto_assigned`.

> El comportamiento histórico (repartir entre Laura/Guillermo/José María) se reproduce
> con la regla sembrada por la migración `0038` (ver más abajo).

### `AutomationService` — `automations.py`

Motor de reglas condición→acción sobre tickets.

- `run_for_ticket(ticket, actor, event, event_key)`: recorre `AutomationRule` activas por
  `priority`, evalúa condiciones y aplica acciones.
- **Condiciones** (`conditions` JSON): `status`, `priority`, `service`, `channel`,
  `assigned_group`, `tags` (igualdad o pertenencia a lista; tags por subconjunto).
- **Acciones** (`actions` JSON): cambiar `status`/`priority`/`assignee_id`/`assigned_group_id`,
  `add_tags`, `notify_requester` + `notification_message`. Cada cambio genera su `TicketEvent`.
- **Idempotencia**: `AutomationExecution(rule, ticket, event_key)` con `unique_together`
  garantiza que una regla se aplica **una sola vez por evento**.

### `SLAService` — `sla.py`

Políticas de SLA de primera respuesta y resolución.

- `find_policy(ticket)`: elige la `SLAPolicy` activa más específica
  (prioridad/servicio/**marca**/grupo — especificidad 0–4).
- `apply_policy(ticket)`: fija `first_response_due_at` y `resolution_due_at`/`due_at` desde
  `created_at`. **Solo arma la primera respuesta si aún no se ha respondido**
  (`not ticket.first_responded_at`) — evita que una edición posterior resucite un SLA ya cumplido.
- `record_first_response(ticket, actor)`: en la primera respuesta pública de un agente,
  sella `first_responded_at`, **persiste `first_response_met`** (el due se limpia, así que
  el cumplimiento no es computable a posteriori) y limpia `first_response_due_at`.
- `annotate_urgency(qs)`: anota `sla_next_due` (próximo vencimiento, con guardas Case
  porque `LEAST()` en MySQL devuelve NULL si algún argumento es NULL). Es la fuente única
  para el orden de cola por SLA y la detección de riesgo.
- `mark_breaches()`: tarea periódica que marca `sla_breached_at` en tickets vencidos no
  resueltos, **escala la prioridad** (low→normal→high→urgent, con `TicketEvent` y
  `sla_escalated_at`), notifica a requester/assignee y **a todo el grupo asignado**, y
  registra la métrica `sla.breach`.
- `notify_at_risk(window_minutes=60)`: tarea periódica que avisa al asignado y a su grupo
  cuando el próximo vencimiento entra en la ventana; idempotente vía `sla_risk_notified_at`.

### `ResponseTemplateService` — `email_templates.py`

Plantillas de respuesta al cliente por clave + marca + idioma (ES/EN).

- `pick_language(ticket)`: `ticket.language` (normaliza valores Zendesk
  'español'/'inglés') → `brand.language` → `'es'`.
- `resolve(key, brand, language)`: cascada marca+idioma → global+idioma → marca+es → global+es.
- `render(template, context)`: motor de plantillas de Django sobre strings con contexto de
  **solo escalares** (nunca instancias de modelo — evita traversal de atributos desde una
  plantilla editada en el panel).

### `OutboundEmailService` — `email_outbound.py`

Envío de email saliente por marca (`Brand.mailbox_type`): `ses` → API SESv2 con boto3
(**rol IAM del pod**, sin credenciales SMTP), `m365` → Graph `sendMail` (token MSAL de
`msgraph.py`, requiere permiso `Mail.Send`).

- `queue_ticket_confirmation(ticket)`: anti-bucles (kill switch `OUTBOUND_EMAIL_ENABLED`,
  buzones propios, patrones noreply, correo entrante auto-generado vía
  `EmailIngestionService.is_auto_generated`), renderiza la plantilla `ticket_created`,
  crea `OutboundEmailLog` con `dedup_key` único y encola `send_outbound_email` en
  `transaction.on_commit`. Toda supresión queda registrada con su motivo.
- `deliver(log)`: entrega real, llamada desde la tarea. SES devuelve `MessageId`; Graph
  devuelve 202 (la copia queda en Enviados).

### `TicketAIService` — `ticket_ai.py` (+ `ai.py`)

Clasificación ITIL automática al crear un ticket (Azure OpenAI; `ai.py` es la única capa
que importa el SDK y mapea errores a `AIRetryableError`/`AIPermanentError`).

- Prompt en español con definiciones ITIL (incident = fallo puntual no planificado;
  problem = causa raíz/patrón recurrente) + few-shots; JSON Schema estricto con enums y
  validación defensiva en código.
- **Política fill-empty-only**: solo rellena campos vacíos (`type`, `category`, `priority`,
  `language`) con confianza ≥ `AI_AUTO_APPLY_CONFIDENCE`; lo escrito por humanos nunca se
  sobrescribe. Cada campo aplicado emite `TicketEvent` con el usuario sistema
  `ai-assistant@ticketflow.local`. Si aplica `priority`, re-ejecuta
  `SLAService.apply_policy` (los plazos no pudieron computarse con priority=NULL).
- Idempotente por `input_fingerprint` (sha256 del input + versión de prompt) frente a
  redeliveries de SQS. La llamada HTTP ocurre fuera de locks; el apply usa
  `select_for_update` corto.
- Histórico completo en `TicketAIAnalysis` (confianza por campo, razonamiento, versión de
  prompt/modelo, tokens, latencia) — base del feedback loop del Q3.

### `EmailIngestionService` — `email_ingestion.py`

Convierte un mensaje de Graph API en ticket/comentario. Llamado por `tasks._process_message`.

`process_message(message, brand)` envuelve el procesado con auditoría en `InboundEmailLog`
(estado received→processed/duplicate/failed). El núcleo `_process_message`:

1. Extrae remitente y cuerpo (`extract_body` + `html_to_text`), sanea el HTML.
2. **Dedup** por `email_message_id` en `Comment`/`Ticket`.
3. Resuelve el solicitante (`get_or_create_requester`, rol *End user*).
4. **Threading en 3 niveles** (`find_ticket_for_message`):
   - Nivel 1: `conversationId` de Graph (lo más fiable).
   - Nivel 2: patrón `[Ticket #N]` en el asunto (id o zendesk_id).
   - Nivel 3: asunto normalizado + mismo requester + ticket abierto/pendiente < 7 días.
5. Si encuentra ticket → añade comentario y reabre si estaba pending/resolved.
   Si no → crea ticket nuevo vía `TicketService.create_ticket` (canal `email`).

### `NotificationService` — `notifications.py`

`notify_ticket_users(ticket, message, actor)` crea `Notification` para requester y
assignee (sin duplicar ni notificar al propio actor). `notify_users(ticket, message, users)`
y `notify_group(ticket, message, group, exclude_ids)` para los avisos de SLA al grupo
completo. `unread_for_user(user)` para el badge.

### `metrics.py`

`record_metric(name, value=1, labels={})` inserta un `OperationalMetric`. Nunca rompe el
flujo de negocio: si falla, lo registra en el log y devuelve `None`.

---

## Modelos nuevos (`app/models.py`)

| Modelo | Propósito | Claves |
|---|---|---|
| `AssignmentRule` | Regla de auto-asignación | filtros: `group`/`service`/`channel`, `active` |
| `AssignmentRuleMember` | Agente dentro de una regla | `weight`, `capacity`; `unique(rule,user)` |
| `SLAPolicy` | Tiempos de SLA | `first_response_minutes`, `resolution_minutes`; filtros prioridad/servicio/grupo |
| `AutomationRule` | Regla condición→acción | `priority`, `conditions` (JSON), `actions` (JSON) |
| `AutomationExecution` | Marca de ejecución (idempotencia) | `unique(rule,ticket,event_key)` |
| `InboundEmailLog` | Auditoría de email entrante | estado, `result`, `error`, `payload`; `unique(brand,message_id)` |
| `OutboundEmailLog` | Auditoría de email saliente | queued/sent/failed/suppressed + motivo, `provider`, `provider_message_id`, `dedup_key` único (190 chars por límite utf8mb4) |
| `ResponseTemplate` | Plantilla de respuesta al cliente | `unique(key,brand,language)`; brand NULL = global; variables `{{ticket_id}}`… |
| `TicketAIAnalysis` | Histórico de clasificación IA | sugerencias + confianza por campo, `input_fingerprint` (idempotencia), `prompt_version`, tokens/latencia |
| `OperationalMetric` | Métrica operativa puntual | `name`, `value`, `labels` (JSON), `recorded_at` |

**Campos en `Ticket`** (sprint SLA + sprint junio 2026): `first_response_due_at`,
`first_responded_at`, `resolution_due_at`, `sla_breached_at`, `first_response_met`,
`sla_risk_notified_at`, `sla_escalated_at`, `problem` (self-FK problema↔incidentes,
`related_name='incidents'`), `type`/`channel` con `choices` (taxonomía en
`app/constants.py`), más índices compuestos para la paginación de listas (incl.
`app_ticket_live_brand_idx`).

### `app/constants.py` — taxonomías de dominio

Módulo sin imports de Django: `TICKET_TYPE_CHOICES` (question/incident/problem/task,
compatible Zendesk), `LEGACY_TYPE_MAP`/`LEGACY_CHANNEL_MAP` (normalización de los valores
españoles históricos del formulario), `CHANNEL_CHOICES`, `normalize_language` ('español'→es),
`PRIORITY_ESCALATION` y `SLA_AT_RISK_WINDOW_MINUTES`.

### Migraciones

| Migración | Contenido |
|---|---|
| `0034`, `0035` | Índices de paginación/filtrado de tickets |
| `0036` | Modelos operativos + campos SLA en `Ticket` |
| `0037` | Campo `Ticket.first_responded_at` |
| `0038` | **Data migration**: siembra la `AssignmentRule "Default inbound"` con Laura Moccia / Guillermo Soret / José María (reversible e idempotente; si no encuentra usuarios, no siembra nada). |
| `0039` | Taxonomías (choices type/channel), `Ticket.problem`, campos SLA nuevos, índice por marca, `SLAPolicy.brand` |
| `0040` | **Data migration** (red de seguridad): normaliza valores legacy de type/channel (la BBDD ya estaba canónica) |
| `0041` | `ResponseTemplate` + `OutboundEmailLog` |
| `0042` | **Data migration**: siembra plantillas `ticket_created` ES/EN (subject con `[Ticket #N]` para threading) |
| `0043` | `TicketAIAnalysis` |

> **Importante**: estas migraciones deben aplicarse en cada entorno (`python src/main.py migrate`).
> El `docker-entrypoint.sh` lo hace al arrancar; en local hay que ejecutarlo a mano.

---

## APIs de administración (solo admin)

CRUD para configurar la lógica operativa desde el panel, protegidas con `@_admin_required`
(401/403 JSON en rutas `/api/`):

| Endpoint | Modelo |
|---|---|
| `/api/admin/assignment-rules/` | `AssignmentRule` (+ miembros) |
| `/api/admin/sla-policies/` | `SLAPolicy` (incluye `brand_id`) |
| `/api/admin/automation-rules/` | `AutomationRule` |
| `/api/admin/response-templates/` | `ResponseTemplate` (+ `POST ?action=preview` con contexto de ejemplo) |

Soportan `GET` (listar), `POST` (crear), `PUT` (actualizar) y `DELETE` (desactivar, *soft delete*).

### Reporting por entidad (agentes/admin)

| Endpoint | Contenido |
|---|---|
| `/reporting/data/` | KPIs (creados, resueltos vía `TicketEvent`, backlog, % SLA, breaches, satisfacción), series diarias creados/cerrados, distribuciones (estado/prioridad/tipo/canal) y desglose por marca (incluye bucket «Sin marca»). Filtros `brand`, `from`, `to` (≤366 días); cache 60 s. |
| `/reporting/export.csv` | Export CSV en streaming (BOM UTF-8 para Excel, `;` como separador) del conjunto filtrado. |

> Limitación documentada: el cumplimiento de primera respuesta (`first_response_met`)
> solo acumula desde su introducción — el due se limpia al responder y no es
> retro-computable. `sla_breached_at` es la fuente de verdad del incumplimiento.

---

## Tareas periódicas (Celery Beat)

| Tarea | Frecuencia | Servicio |
|---|---|---|
| `poll_m365_mailboxes` | 120 s | `EmailIngestionService` |
| `auto_assign_unassigned_tickets` | 300 s | `AssignmentService.auto_assign_unassigned` |
| `mark_sla_breaches` | 300 s | `SLAService.mark_breaches` |
| `notify_sla_at_risk` | 300 s | `SLAService.notify_at_risk` (aviso 60 min antes del vencimiento) |

### Tareas event-driven (encoladas con `transaction.on_commit`)

| Tarea | Disparador | Notas |
|---|---|---|
| `send_outbound_email(log_id)` | `TicketService.create_ticket` → confirmación al solicitante | Idempotente (`status=='sent'` + `dedup_key`); autoretry ×5 con backoff; al agotar marca `failed` en `OutboundEmailLog` |
| `classify_ticket_ai(ticket_id)` | `TicketService.create_ticket` (si `AI_CLASSIFICATION_ENABLED`) | Idempotente por fingerprint; retryable ×3 con backoff; errores permanentes marcan `TicketAIAnalysis.failed` sin envenenar la cola. El dispatch va en try/except: si el broker está caído, el ticket se crea igual. |

---

## Health checks (`src/health/`)

| Endpoint | Para qué |
|---|---|
| `livez` | El proceso está vivo |
| `readyz` | Listo para recibir tráfico |
| `internalz` | Diagnóstico interno: ping a BD, estado del broker Celery, último email entrante y última métrica. Pensado para uso **intra-clúster** — debe protegerse en el ingress. |

---

## Configuración de entorno relevante

| Variable | Efecto |
|---|---|
| `USE_SQLITE=1` | Usa SQLite local (tests/desarrollo sin MySQL) |
| `DISABLE_AWS_SSM=1` | Vacía `AWS_SSM_PREFIX` para no leer secretos de SSM |

Para ejecutar la suite de tests sin infraestructura:

```bash
USE_SQLITE=1 DISABLE_AWS_SSM=1 python src/main.py test app
```
