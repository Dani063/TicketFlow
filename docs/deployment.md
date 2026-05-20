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
| `CELERY_SQS_QUEUE_URL` | ConfigMap | URL completa de la cola SQS |
| `DB_NAME`, `DB_PORT` | ConfigMap | Configuración de base de datos |
| `SECRET_KEY` | SSM | Clave secreta Django (vía `{AWS_SSM_PREFIX}/SecretKey`) |
| `CELERY_BROKER_URL` | — | Solo en local con Redis; en producción no definir (usa SQS) |

---

## Tareas programadas (beat)

Definidas en `settings.py` bajo `CELERY_BEAT_SCHEDULE`:

| Tarea | Intervalo | Descripción |
|---|---|---|
| `app.tasks.poll_m365_mailboxes` | 120 s | Consulta buzones M365 vía Microsoft Graph API |
