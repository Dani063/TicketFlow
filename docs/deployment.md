# Deployment

## Entrypoint

El Dockerfile de producción usa un entrypoint fijo:

```sh
exec python3 main.py $PYTHON_ARGS
```

`PYTHON_ARGS` se define en cada Deployment de Kubernetes y controla qué proceso arranca el pod.

---

## Roles y sus PYTHON_ARGS

| Deployment | `PYTHON_ARGS` | Descripción |
|---|---|---|
| `deployment-web.yaml` | `start_web` | Servidor web Gunicorn |
| `deployment-worker.yaml` | `celery_worker` | Consumidor de tareas Celery |
| `deployment-beat.yaml` | `celery_beat` | Scheduler de tareas periódicas |

### Migración de base de datos

No es un `Deployment` sino un `Job` (`k8s/job-migrate.yaml`, `name: ticketflow-migrate`) y
**no usa `PYTHON_ARGS`**: llama directamente a `entrypoint.sh migrate`, el mismo
`docker-entrypoint.sh` descrito en `CLAUDE.md` («Dos caminos de arranque divergentes»). Hay
que borrar el Job anterior antes de reaplicar (`kubectl delete job ticketflow-migrate
--ignore-not-found`) porque `spec.template` de un Job es inmutable.

---

## Flags disponibles

### `start_web`

```
python main.py start_web [opciones]
```

| Flag | Default | Descripción |
|---|---|---|
| `--workers N` | `3` | Nº de procesos Gunicorn (recomendado: `2*CPU+1`) |
| `--threads N` | `1` | Threads por worker |
| `--timeout N` | `60` | Segundos antes de matar un worker bloqueado |
| `--bind ADDR` | `0.0.0.0:8000` | Dirección y puerto de escucha |
| `--loglevel LEVEL` | `info` | Nivel de log: `debug`, `info`, `warning`, `error` |

#### Migraciones al arrancar el web

`start_web` aplica las migraciones antes de recoger los estáticos y arrancar
Gunicorn. `RUN_MIGRATIONS_ON_STARTUP` vale `true` por defecto, por lo que DEV, STA
y local conservan el comportamiento actual sin cambios de configuración.

Para delegar las migraciones en un Job únicamente en PRO, establecer
`RUN_MIGRATIONS_ON_STARTUP: "false"` en el ConfigMap del web de PRO, después de
preparar el Job y su ejecución antes del despliegue de web, worker y beat. No
desactivar este paso mientras no exista otro proceso responsable de migrar.
La variable no afecta al comando `python main.py migrate --noinput` del Job.

Se aceptan `true/false`, `1/0`, `yes/no` y `on/off`, sin distinguir mayúsculas ni
espacios exteriores. Un valor inválido detiene el arranque para evitar omitir
migraciones por un error de configuración. En modo desactivado se mantienen
`collectstatic` y el arranque de Gunicorn, y se registra la omisión en el log.

**Dimensionado de workers**

La fórmula recomendada por Gunicorn es `2 * CPU + 1`. Con los recursos definidos en el deployment (`500m` CPU = 0.5 cores):

| CPUs del pod | Workers recomendados |
|---|---|
| 0.5 | 2 |
| 1 | 3 |
| 2 | 5 |

Si añades `--threads` (ej. `--threads 2`), cada worker puede atender múltiples requests en paralelo con menos memoria que añadir más workers.

Ejemplo con HPA:

```yaml
- name: PYTHON_ARGS
  value: "start_web --workers 3 --threads 2 --timeout 60"
```

---

### `celery_worker`

```
python main.py celery_worker [opciones]
```

| Flag | Default | Descripción |
|---|---|---|
| `--concurrency N` | `2` | Nº de procesos paralelos |
| `--loglevel LEVEL` | `info` | Nivel de log: `debug`, `info`, `warning`, `error` |
| `--queues Q1,Q2` | todas | Colas a consumir |
| `--hostname NAME` | auto | Nombre del worker (ej. `worker1@%h`) |
| `--max-tasks-per-child N` | ilimitado | Reinicia el proceso cada N tareas |
| `--without-heartbeat` | — | Desactiva heartbeat (reduce tráfico SQS) |

Ejemplo recomendado con SQS:

```yaml
- name: PYTHON_ARGS
  value: "celery_worker --concurrency 2 --without-heartbeat"
```

### `celery_beat`

```
python main.py celery_beat [opciones]
```

| Flag | Default | Descripción |
|---|---|---|
| `--loglevel LEVEL` | `info` | Nivel de log: `debug`, `info`, `warning`, `error` |
| `--schedule PATH` | `/tmp/celerybeat-schedule` | Ruta del archivo de estado del scheduler |
| `--max-interval N` | `5` | Segundos máximos entre checks del scheduler |

> **Importante:** `deployment-beat.yaml` siempre debe tener `replicas: 1` y `strategy: Recreate`.
> Ejecutar dos instancias de beat simultáneamente duplica todas las tareas programadas.

---

## Variables de entorno requeridas

Todas vienen del ConfigMap `ticketflow-config` y el Secret `ticketflow-secrets`.

| Variable | Fuente | Descripción |
|---|---|---|
| `AWS_SSM_PREFIX` | ConfigMap | Prefijo SSM para obtener secretos (ej. `/Recordia/Development/Ticketflow`) |
| `AWS_REGION` | ConfigMap | Región AWS (ej. `eu-west-1`) |
| `DB_NAME`, `DB_PORT` | ConfigMap | Configuración de base de datos |
| `SECRET_KEY` | SSM `{prefix}/SecretKey` | Clave secreta Django |
| `CELERY_SQS_QUEUE_URL` | SSM `{prefix}/CelerySQSQueueUrl` | URL completa de la cola SQS |
| `CELERY_BROKER_URL` | — | Solo en local con Redis; en producción no definir (usa SQS) |
| `ARTIFACT_VERSION` | Imagen CI/CD | Tag con la que se construyó la imagen; alimenta automáticamente el identificador visual de versión |
| `TICKETFLOW_VERSION` | Opcional | Override explícito de la versión visual; tiene prioridad sobre `ARTIFACT_VERSION` |
| `ALLOWED_HOSTS`, `CSRF_TRUSTED_ORIGINS` | ConfigMap | Dominios servidos por marca; en `k8s/configmap.yaml` llevan placeholders de ejemplo |
| `WEB_CONCURRENCY`, `GUNICORN_TIMEOUT` | ConfigMap | Nº de procesos y timeout de Gunicorn (no confundir con los flags `--workers`/`--timeout` de `start_web`; solo aplican en el camino de `docker-entrypoint.sh`) |
| `SSO_LOGIN_API_URL`, `SSO_LOGIN_UI_URL` | ConfigMap | Endpoints del SSO corporativo |
| `OUTBOUND_EMAIL_ENABLED`, `OUTBOUND_TICKET_CREATED_ENABLED`, `OUTBOUND_AGENT_REPLY_ENABLED` | ConfigMap | Kill switches de email saliente (global y por tipo) |
| `SATISFACTION_SURVEY_ENABLED` | ConfigMap | Activa la encuesta CSAT nativa |
| `PUBLIC_HELP_ENABLED`, `PUBLIC_TICKET_INTAKE_ENABLED` | ConfigMap | Activan el Help Center público y la creación pública de tickets |
| `ECOMFAX_HELP_PUBLIC_URL`, `RECORDIA_HELP_PUBLIC_URL`, `PUBLIC_ECOMFAX_BRAND_NAME`, `PUBLIC_RECORDIA_BRAND_NAME` | ConfigMap | URL y nombre visible de cada Help Center por marca |
| `ATTACHMENT_STORAGE_BACKEND`, `ATTACHMENT_STORAGE_PREFIX` | ConfigMap | Backend de almacenamiento de adjuntos y prefijo de claves S3 por entorno |
| `PUBLIC_ATTACHMENTS_S3_BUCKET`, `PUBLIC_ATTACHMENTS_S3_REGION` | ConfigMap | Bucket/región S3 de adjuntos públicos |
| `HELP_CONTENT_S3_BUCKET`, `HELP_CONTENT_S3_REGION` | ConfigMap | Bucket/región S3 de contenido del Help Center |
| `PUBLIC_ATTACHMENT_MAX_FILES`, `PUBLIC_ATTACHMENT_MAX_BYTES`, `PUBLIC_ATTACHMENT_MAX_TOTAL_BYTES` | ConfigMap | Límites de adjuntos por mensaje (editor y formularios públicos): 10 archivos, 15 MB y 50 MB por defecto |
| `ATTACHMENT_ORPHAN_MAX_AGE_HOURS` | ConfigMap | Antigüedad a partir de la cual `cleanup_orphan_attachments` borra adjuntos huérfanos |
| `ATTACHMENT_DOWNLOAD_URL_TTL_SECONDS` | ConfigMap | TTL de las URL firmadas de descarga de adjuntos |
| `OUTBOUND_EMAIL_ATTACHMENT_MAX_BYTES` | ConfigMap | Límite de tamaño de adjuntos en email saliente |
| `PUBLIC_CLIENT_IP_HEADER` | ConfigMap | Cabecera de la que se lee la IP del cliente; el ingress debe sanear/añadir `X-Forwarded-For` |

### Recogida de correo: barrido por ventana

| Variable | Defecto | Descripción |
|---|---|---|
| `ENVIRONMENT_NAME` | derivado de `AWS_SSM_PREFIX` | Espacio de nombres del cursor de buzones. **Dos entornos no pueden compartirlo** |
| `INBOUND_MARK_READ_ENABLED` | `false` | Marca como leído tras ingerir. Solo cortesía; como mucho en **un** entorno |
| `INBOUND_SWEEP_OVERLAP_MINUTES` | `20` | Solape de cada vuelta con lo ya barrido |
| `INBOUND_SWEEP_INITIAL_LOOKBACK_MINUTES` | `60` | Ventana con la que arranca un cursor; lo anterior va por backfill |
| `INBOUND_SWEEP_PAGE_SIZE` / `INBOUND_SWEEP_MAX_PAGES` | `200` / `25` | Tamaño de página y tope de páginas por vuelta |
| `INBOUND_SWEEP_PROCESS_BUDGET_SECONDS` | `40` | Tiempo de proceso por vuelta (el beat es de 60 s) |
| `INBOUND_SWEEP_LEASE_SECONDS` / `INBOUND_SWEEP_RECLAIM_MINUTES` | `180` / `10` | Lease del buzón y caducidad del reclamo de un mensaje |
| `INBOUND_SWEEP_MAX_ATTEMPTS` | `8` | Intentos antes de la cuarentena |
| `INBOUND_SWEEP_DEEP_HOURLY_HOURS` / `INBOUND_SWEEP_DEEP_DAILY_DAYS` | `48` / `14` | Ventanas de los barridos de seguridad |
| `INBOUND_CURSOR_STALE_MINUTES` / `INBOUND_SWEEP_PENDING_ALERT_MINUTES` | `5` / `10` | Umbrales de ERROR: sin vuelta buena / pendiente demasiado antiguo |
| `INBOUND_AUTOMATED_SENDER_PATTERNS` / `INBOUND_AUTOMATED_SUBJECT_PATTERNS` | vacío | Regex extra (separadas por tres barras verticales) para el clasificador del backfill |

La recogida no guarda estado en Graph: lista la carpeta por fecha de recepción y la
verdad de qué se ha recogido es `InboundEmailLog`. Ver `docs/backend-architecture.md`.

Al activarse en un entorno, **el cursor arranca una hora atrás**. Lo anterior se
recupera a mano:

```bash
python main.py reingest_mailbox --since 2026-09-29T10:00:00Z --dry-run   # informe por grupos
python main.py reingest_mailbox --since 2026-09-29T10:00:00Z             # registra lo que falta
```

El interruptor es `ingest_mode` en `/django-admin/app/mailboxsynccursor/`: `paused`
detiene la recogida de ese entorno sin reiniciar pods. Tras desplegar esta versión
por primera vez, el cursor existente queda en `paused` hasta activarlo a mano (para
que el delta antiguo de los pods viejos y el barrido no se solapen).

DEV, STA y PRO leen el mismo buzón físico. En DEV y STA deben fijarse
`OUTBOUND_EMAIL_ENABLED=false` e `INBOUND_MARK_READ_ENABLED=false` en su `.env` del
bucket `recordia-config`: pueden ingerir el correo compartido sin enviar respuestas a
clientes reales ni cambiar el estado de lectura.

El orden del historial es `0059_merge_sta_dev` → `0061_mailboxsynccursor` →
`0062_reporting_fact_tables`. La numeración salta el 0060 a propósito: las
tablas de hechos se escribieron antes, pero `0061` ya estaba aplicada en DEV
declarando `0059` como dependencia, así que colarlas por delante daría
`InconsistentMigrationHistory`. No reordenar la cadena.

---

## Tareas programadas (beat)

Definidas en `settings.py` bajo `CELERY_BEAT_SCHEDULE`:

| Tarea | Intervalo | Descripción |
|---|---|---|
| `app.tasks.sweep_m365_mailboxes` | 60 s | Recogida de correo M365 (barrido por ventana, lease por buzón) |
| `app.tasks.refresh_reporting_facts` | 300 s | Recalcula las tablas de hechos de `/reporting/` (ver abajo) |

---

## Tablas de hechos de `/reporting/`

La migración `0062_reporting_fact_tables` crea `TicketFact`, `TicketStatusInterval` y
`ReportingFactState`. Son derivados puros del modelo transaccional: **la migración las
crea vacías y hay que poblarlas**.

```bash
python main.py rebuild_reporting_facts            # backfill completo
python main.py rebuild_reporting_facts --stale-only  # solo lo rancio
```

Orden recomendado al desplegar:

1. `migrate`
2. `rebuild_reporting_facts` (una vez; en el histórico completo tarda minutos)
3. Arrancar web y beat con normalidad

**Qué pasa si se olvida el paso 2:** nada se rompe. Mientras `ReportingFactState` no
tenga marca de backfill, el reporting vuelve a reconstruir desde la auditoría — más
lento, pero correcto. Además `refresh_reporting_facts` (beat, cada 5 min) detecta que
faltan hechos y hace el backfill por su cuenta en la primera pasada, dentro del worker
y sin tocar el pool web. Ejecutar el comando a mano solo acelera el arranque.

La detección de qué recalcular es **autorreparable y sin estado propio**: compara
`Ticket.updated_at` con el que tenía cuando se calcularon sus hechos. Si beat estuvo
caído, la siguiente pasada recupera todo el retraso sola.

> El pod web nunca escribe en estas tablas. Si hiciera falta regenerarlas en caliente,
> el comando se puede lanzar desde un pod de worker sin parar el servicio: mientras
> corre, el reporting sigue sirviendo por el camino de reconstrucción.
