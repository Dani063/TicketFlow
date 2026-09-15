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

### Ingesta de correo: un cursor por entorno

| Variable | Defecto | Descripción |
|---|---|---|
| `ENVIRONMENT_NAME` | derivado de `AWS_SSM_PREFIX` | Espacio de nombres del cursor de buzones. **Dos entornos no pueden compartirlo** |
| `INBOUND_MARK_READ_ENABLED` | `true` | Marca los mensajes como leídos tras ingerirlos. Debe quedar en `true` en **un solo entorno** |
| `INBOUND_DELTA_LOOKBACK_MINUTES` | `60` | Ventana que se ingiere al estrenar o reiniciar un cursor |
| `INBOUND_DELTA_MAX_PAGES` | `20` | Tope de páginas de delta por vuelta; el resto continúa en la siguiente |

El puntero de lectura vive en `MailboxSyncCursor`, en la BBDD de cada entorno, y no
en el flag `isRead` del buzón. Eso es lo que permite que DEV y STA lean el mismo
buzón físico sin quitarse el correo: antes el primero que pasaba marcaba el mensaje
como leído y el otro ya no lo veía.

Al desplegar por primera vez, **cada entorno arranca ingiriendo la última hora** del
buzón (`INBOUND_DELTA_LOOKBACK_MINUTES`). Es deliberado: sin esa cota, el primer
delta devolvería la carpeta entera y se crearían tickets de todo el histórico.

Borrar la fila del cursor en `/django-admin/app/mailboxsynccursor/` reinicia la
sincronización de ese buzón. Es también la vía para forzar un rearranque si el
cursor se queda en `last_result=error`.

En DEV deben fijarse explícitamente `OUTBOUND_EMAIL_ENABLED=false` e
`INBOUND_MARK_READ_ENABLED=false`, tanto en `DEV/TicketFlow.UI/.env` del bucket
`recordia-config` como en los ConfigMaps de sus procesos. DEV puede ingerir todo
el correo compartido sin enviar respuestas ni cambiar el estado de lectura.
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
| `app.tasks.poll_m365_mailboxes` | 120 s | Ingesta incremental de buzones M365 (delta query, cursor por entorno) |
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
