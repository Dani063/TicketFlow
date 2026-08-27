# Arquitectura del sistema

> Para la lógica de negocio (capa de servicios, modelos operativos, SLA,
> asignación, automatizaciones, ingesta de email) ver
> [backend-architecture.md](backend-architecture.md). Este documento cubre la
> infraestructura (Celery/SQS/SSM/IAM) y el flujo a alto nivel.

## Actores

| Actor | Tecnología | Rol |
|---|---|---|
| **Web** | Django + Gunicorn | Sirve la interfaz HTTP a los agentes |
| **Beat** | Celery Beat | Scheduler — encola tareas periódicas |
| **Worker** | Celery Worker | Ejecuta las tareas encoladas |
| **SQS** | AWS SQS FIFO | Cola de mensajes entre Beat y Worker |
| **MySQL/RDS** | MySQL | Persistencia de tickets, usuarios, marcas |
| **Graph API** | Microsoft Graph | Lectura de buzones M365 |
| **SSM** | AWS Parameter Store | Secretos en arranque (credenciales, claves) |

---

## Flujo principal: ingesta de email

```
Beat (cada 120 s)
  │
  ├─► SQS: publica mensaje "poll_m365_mailboxes"
  │
Worker (polling continuo)
  │
  ├─► SQS: recibe mensaje (long polling, espera hasta 20 s)
  │
  ├─► Azure AD: obtiene token OAuth2 (client_credentials)
  │
  ├─► Graph API: GET /users/{mailbox}/mailFolders/Inbox/messages
  │             ?$filter=isRead eq false &$top=50
  │
  │   Por cada mensaje no leído → EmailIngestionService.process_message:
  │   ├─► MySQL: registra InboundEmailLog (auditoría + dedup por brand+message_id)
  │   ├─► MySQL: ¿existe Comment/Ticket con ese email_message_id? → dedup
  │   │
  │   ├─► Threading en 3 niveles (find_ticket_for_message):
  │   │     1) conversationId de Graph
  │   │     2) patrón "[Ticket #N]" en el asunto
  │   │     3) asunto normalizado + mismo requester + ticket abierto <7 días
  │   │
  │   ├─► [Si encuentra ticket] añade Comment; si pending/resolved → reabre a open
  │   └─► [Si no] crea Ticket + Comment inicial (auto-asignado por reglas)
  │
  └─► Graph API: PATCH /messages/{id} → isRead: true
```

> El procesado vive en `EmailIngestionService` (ver
> [backend-architecture.md](backend-architecture.md)); `tasks.py` solo orquesta el poll.

---

## Flujo de arranque (todos los pods)

```
Pod arranca
  │
  ├─► docker-entrypoint.sh: check_sqs_queue()
  │     └─► SQS: GetQueueAttributes → valida que la cola existe
  │
  ├─► Django settings.py
  │     ├─► SSM: GetParameter /prefix/SecretKey
  │     ├─► SSM: GetParameters /prefix/RDSCredentials + /prefix/RDSEndpoint
  │     ├─► SSM: GetParameter /prefix/AzureCredentials (JSON)
  │     ├─► SSM: GetParameter /prefix/ZendeskCredentials (JSON)
  │     └─► SSM: GetParameter /prefix/CelerySQSQueueUrl
  │
  └─► Proceso principal según PYTHON_ARGS:
        start_web     → Gunicorn
        celery_worker → Celery worker
        celery_beat   → Celery beat
```

---

## Deduplicación de mensajes

Cada email tiene un `id` único de Graph API guardado como `email_message_id` en `Ticket` y `Comment`. Antes de procesar cualquier mensaje se comprueba si ya existe ese ID en base de datos — si existe, se descarta sin crear nada.

Esto permite que el worker procese el mismo email en múltiples ejecuciones del poll sin crear duplicados.

Además, cada mensaje queda auditado en `InboundEmailLog` con `unique(brand, message_id)`:
un segundo intento sobre el mismo mensaje se marca `duplicate` y se omite, y los fallos
quedan registrados con su error para diagnóstico.

---

## Gestión de hilos de conversación

`EmailIngestionService.find_ticket_for_message` decide si un email es **respuesta a un
ticket existente** o un **ticket nuevo** mediante tres niveles de coincidencia, en orden:

| Nivel | Criterio | Resultado |
|---|---|---|
| 1 | `conversationId` de Graph coincide con `email_conversation_id` | `Comment` en ese ticket |
| 2 | Asunto contiene `[Ticket #N]` (id o zendesk_id) y existe | `Comment` en ese ticket |
| 3 | Asunto normalizado + mismo requester + ticket abierto/pendiente < 7 días | `Comment` en ese ticket |
| — | Si el ticket estaba `pending`/`resolved` | Se reabre a `open` |
| — | Sin coincidencia | Nuevo `Ticket` + `Comment` inicial (auto-asignado por reglas) |
| — | Email sin remitente | Descartado (`skip_no_sender`) |

---

## Polling SQS

El worker usa **long polling**: cada petición a SQS espera hasta 20 segundos a que llegue un mensaje. Si no llega ninguno, vuelve vacía y repite inmediatamente. Con 2 workers en paralelo (`--concurrency 2`):

- ~17.000 peticiones SQS/día por worker
- Latencia máxima entre publicación y ejecución: 20 segundos
- Dentro del free tier de SQS (1M peticiones/mes) con hasta ~3 workers

---

## Permisos IAM requeridos (rol del pod)

### SQS

| Permiso | Pod | Por qué |
|---|---|---|
| `sqs:GetQueueAttributes` | web, worker, beat | `docker-entrypoint.sh` llama a `get_queue_attributes` antes de arrancar para verificar que la cola existe. Sin este permiso el pod no arranca. |
| `sqs:ReceiveMessage` | worker | Celery worker hace long polling a SQS para recibir tareas encoladas. Es la operación central del worker. |
| `sqs:DeleteMessage` | worker | Tras procesar una tarea, Celery elimina el mensaje de la cola para confirmarlo. Sin este permiso la tarea se reejecutaría al expirar el visibility timeout. |
| `sqs:SendMessage` | beat, web | Beat encola `poll_m365_mailboxes` cada 120 s. Web lo necesitaría si en el futuro se encolan tareas desde vistas HTTP. |
| `sqs:ChangeMessageVisibility` | worker | Celery puede extender el visibility timeout de un mensaje si la tarea tarda más de lo previsto (configurado a 3600 s). Sin este permiso las tareas largas se reejecutarían en paralelo. |

> El recurso a restringir es la ARN de la cola: `arn:aws:sqs:eu-west-1:{account_id}:DEV-Celery.fifo`

---

### SSM Parameter Store

| Permiso | Parámetro | Pod | Por qué |
|---|---|---|---|
| `ssm:GetParameter` | `{prefix}/SecretKey` | web, worker, beat | Django necesita `SECRET_KEY` para arrancar. Se lee en el primer import de `settings.py`. |
| `ssm:GetParameters` | `{prefix}/RDSCredentials` `{prefix}/RDSEndpoint` | web, worker, beat | Credenciales y host de MySQL. Se leen en una sola llamada batch al arrancar. |
| `ssm:GetParameter` | `{prefix}/AzureCredentials` | web, worker, beat | JSON con `AZURE_TENANT_ID`, `AZURE_CLIENT_ID`, `AZURE_CLIENT_SECRET`. El worker los usa para obtener el token de Graph API en cada poll. |
| `ssm:GetParameter` | `{prefix}/ZendeskCredentials` | web, worker, beat | JSON con credenciales de Zendesk. Usadas por los comandos de importación manual (`import_zendesk_ticket`, etc.). |
| `ssm:GetParameter` | `{prefix}/CelerySQSQueueUrl` | web, worker, beat | URL completa de la cola SQS. Se lee en `settings.py` para configurar el broker de Celery. |

> Todos los parámetros son `SecureString` — el permiso `ssm:GetParameter` con `WithDecryption=true` implica también `kms:Decrypt` sobre la clave KMS que los cifra (por defecto la clave gestionada de SSM en la cuenta; si se usa clave propia hay que añadir el permiso explícitamente).

---

### Azure AD (no es IAM, es Microsoft)

Estos permisos se configuran en el **App Registration** de Azure AD, no en AWS.

| Permiso | Tipo | Por qué |
|---|---|---|
| `Mail.Read` | Application | Graph API — leer mensajes del buzón de soporte |
| `Mail.ReadWrite` | Application | Graph API — marcar mensajes como leídos tras procesarlos |

> Requieren **consentimiento de administrador** en el tenant de Azure AD.
