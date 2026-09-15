# Runbook de CI/CD y comprobación de entornos

Este documento describe el camino real de TicketFlow desde un commit hasta un
pod en ejecución. Está escrito para que Claude pueda desplegar y verificar sin
confundir código, manifiestos, configuración y estado de runtime.

## El mapa completo

Hay cinco fuentes de verdad distintas. Comprobar solo una no demuestra que el
cambio esté desplegado.

| Capa | Fuente de verdad | Qué demuestra |
|---|---|---|
| Código | Azure Repos `Recordia/TicketFlow.UI` | Qué commit contiene el cambio |
| Imagen | ECR `recordia/ticketflow.ui:<tag>` | Qué artefacto construyó la CI |
| Despliegue | Azure Repos `Recordia-Kubernetes`, rama `develop` | Qué tag solicita DEV o STA |
| Runtime | ArgoCD y los pods de EKS | Qué imagen está ejecutándose y si está sana |
| Configuración | `s3://recordia-config/<ENTORNO>/TicketFlow.UI/.env` y ConfigMaps | Qué valores recibe realmente Django |

El flujo es:

```text
commit → tag → CI 1108 → imagen ECR
                         ↓
                CD 972/973 → commit en Recordia-Kubernetes/develop
                                      ↓
                              ArgoCD → pods EKS
                                      ↓
                       entrypoint descarga .env desde S3
```

Los manifiestos Kubernetes **no están en S3**. Viven en
`Recordia-Kubernetes`. S3 contiene el `.env` que el entrypoint copia dentro del
artefacto antes de arrancar Django.

## Identificadores y rutas

| Elemento | DEV | STA |
|---|---|---|
| Pipeline CD | `972` (`DEV-CD-Kubernetes`) | `973` (`STA-CD-Kubernetes`) |
| Aplicación ArgoCD | `dev-recordia-ticketflow.ui-manifests` | `sta-recordia-ticketflow.ui-manifests` |
| Ruta GitOps | `DEV/Recordia/Manifests/TicketFlow.UI` | `STA/Recordia/Manifests/TicketFlow.UI` |
| Configuración S3 | `DEV/TicketFlow.UI/.env` | `STA/TicketFlow.UI/.env` |
| `SHORT_ENV` | `DEV` | `STA` |
| `LONG_ENV` | `Development` | `Staging` |

La CI de TicketFlow es la pipeline `1108`, llamada `TicketFlow.UI`. Estos son
los identificadores actuales; si Azure devuelve `404`, volver a resolverlos por
nombre con `GET build/definitions?api-version=7.1` en lugar de adivinar otro ID.

Perfiles AWS usados en este equipo:

- `recordia`: cuenta de runtime `679818922288`; ECR, EKS y bucket
  `recordia-config`.
- `root`: cuenta `848507422632`; el secreto administrativo de ArgoCD está en
  `/INF/argocd/admin/password`.

Antes de operar, confirmar identidad sin mostrar credenciales:

```powershell
aws sts get-caller-identity --profile recordia --query '{Account:Account,Arn:Arn}' --no-cli-pager
aws sts get-caller-identity --profile root --query '{Account:Account,Arn:Arn}' --no-cli-pager
```

## 1. Preparar y verificar el código

No mezclar un despliegue urgente con cambios sin terminar. Si el árbol está
sucio, crear un worktree desde el commit base y copiar o aplicar únicamente el
cambio que se va a publicar.

```powershell
git status --short
git branch --show-current
git fetch origin --prune --tags
git worktree add -b fix/nombre C:/repo/TicketFlow.Nombre origin/develop
```

Para TicketFlow, las comprobaciones mínimas son:

```powershell
$env:SECRET_KEY = 'local-release-check'
$env:DISABLE_AWS_SSM = '1'
$env:USE_SQLITE = '1'
$env:DEBUG = 'true'
$env:ALLOWED_HOSTS = 'localhost,127.0.0.1,testserver'
$env:CELERY_BROKER_URL = 'memory://'
$env:OUTBOUND_EMAIL_ENABLED = 'false'
$env:AWS_EC2_METADATA_DISABLED = 'true'

python src/main.py test app --noinput
python src/main.py check
python src/main.py makemigrations --check --dry-run
git diff --check
```

Antes del commit, revisar `git diff --stat` y `git diff`. Después:

```powershell
git add <rutas exactas>
git commit -m 'fix(email): descripción concreta'
git push -u origin <rama>
```

No usar `git add .` en un árbol con trabajo ajeno.

## 2. CI: construir la imagen correcta

La pipeline `1108` se activa al publicar un tag del repositorio
`Recordia/TicketFlow.UI`.

- Un tag de DEV, como `2.2.20-dev`, puede apuntar a una rama de feature.
- Un tag estable, como `2.2.20`, debe apuntar a un commit contenido en
  `develop`.
- `TagOriginValidation.yml` rechaza un prerelease situado en `develop` y una
  versión estable situada fuera de `develop`.

```powershell
git tag -a 2.2.20-dev -m 'DEV: descripción del cambio'
git push origin refs/tags/2.2.20-dev
```

La ejecución automática puede tardar unos segundos en aparecer. No lanzar una
CI manual inmediatamente: una ejecución manual normal selecciona el ref por
defecto de `artifact-repo` y puede construir `develop` en lugar del tag.

### Acceso a Azure DevOps por API

Se usa Git Credential Manager para obtener un token OAuth efímero. Nunca se
imprime ni se guarda en un fichero:

```powershell
$credentialInput = "url=https://dev.azure.com/cloudworldwideservices/Recordia/_git/TicketFlow.UI`n`n"
$credentialOutput = $credentialInput | git -c credential.azreposCredentialType=oauth credential fill
$credential = @{}
$credentialOutput -split "`n" | ForEach-Object {
    if ($_ -match '^([^=]+)=(.*)$') { $credential[$matches[1]] = $matches[2] }
}
$headers = @{ Authorization = "Bearer $($credential.password)" }
$ado = 'https://dev.azure.com/cloudworldwideservices/Recordia/_apis'
```

Consultar las últimas ejecuciones:

```powershell
$uri = "$ado/build/builds?definitions=1108&queryOrder=queueTimeDescending&`$top=10&api-version=7.1"
$runs = (Invoke-RestMethod -Headers $headers -Uri $uri).value
$runs | Select-Object id,status,result,startTime,finishTime,triggerInfo
```

No basta con que la CI esté verde. Hay que comprobar que usó el tag y el commit
esperados:

```powershell
$runId = <ID_CI>
$run = Invoke-RestMethod -Headers $headers -Uri "$ado/pipelines/1108/runs/$runId`?api-version=7.1"
$run.resources.repositories.'artifact-repo' | Select-Object refName,version
git rev-parse '2.2.20-dev^{}'
```

`refName` debe ser `refs/tags/2.2.20-dev` y `version` debe coincidir con el
commit obtenido por Git.

Confirmar también la imagen en ECR:

```powershell
aws ecr describe-images `
  --repository-name recordia/ticketflow.ui `
  --image-ids imageTag=2.2.20-dev `
  --profile recordia --region eu-west-1 --no-cli-pager `
  --query 'imageDetails[].{digest:imageDigest,tags:imageTags,pushed:imagePushedAt}'
```

## 3. CD: actualizar GitOps y esperar a ArgoCD

La ejecución de CD recibe estos parámetros:

```json
{
  "templateParameters": {
    "KUBERNETES_ARTIFACT_FOLDER_NAME": "TicketFlow.UI",
    "ARTIFACT_VERSION": "2.2.20-dev",
    "PROJECT_NAME": "Recordia",
    "DEPLOYMENT_TYPE": "Manifests"
  },
  "resources": {
    "repositories": {
      "templates": {"refName": "refs/heads/feature/kubernetes"},
      "kubernetes-repo": {"refName": "refs/heads/develop"}
    }
  }
}
```

TicketFlow tiene `deployment-web.yaml`, `deployment-worker.yaml`,
`deployment-beat.yaml` y `deployment-migrate.yaml`. La versión de
`pipelines.templates` en `master` todavía busca un único `deployment.yaml` y
falla con `Neither deployment.yaml nor scaledjob.yaml found`. Hasta que ese
arreglo llegue a `master`, seleccionar explícitamente
`pipelines.templates@feature/kubernetes`.

Primero hacer un preview y revisar `finalYaml`:

```powershell
$body = @{
    previewRun = $true
    templateParameters = @{
        KUBERNETES_ARTIFACT_FOLDER_NAME = 'TicketFlow.UI'
        ARTIFACT_VERSION = '2.2.20-dev'
        PROJECT_NAME = 'Recordia'
        DEPLOYMENT_TYPE = 'Manifests'
    }
    resources = @{ repositories = @{
        templates = @{ refName = 'refs/heads/feature/kubernetes' }
        'kubernetes-repo' = @{ refName = 'refs/heads/develop' }
    }}
} | ConvertTo-Json -Depth 8

$cdPipeline = 972  # usar 973 para STA
$preview = Invoke-RestMethod -Method Post -Headers $headers -ContentType 'application/json' `
  -Uri "$ado/pipelines/$cdPipeline/runs?api-version=7.1" -Body $body
$preview.finalYaml | Select-String 'TicketFlow.UI|2.2.20-dev|deployment\*\.yaml|dev-recordia'
```

El preview debe mostrar el entorno correcto, el tag correcto, la ruta de
TicketFlow y `deployment*.yaml`. Para ejecutar, enviar el mismo cuerpo sin
`previewRun`.

El CD:

1. cambia el tag en todos los `deployment-*.yaml` de TicketFlow;
2. hace commit y push a `Recordia-Kubernetes/develop` con la identidad de
   Pipelines;
3. pide a ArgoCD un refresh;
4. espera hasta `Synced` y `Healthy`;
5. en STA ejecuta además gates k6/ZAP y puede revertir el commit GitOps si
   fallan.

Verificar el resultado de la pipeline y el commit GitOps:

```powershell
$cdRun = <ID_CD>
Invoke-RestMethod -Headers $headers -Uri "$ado/build/builds/$cdRun`?api-version=7.1" |
  Select-Object id,status,result,finishTime

git -C C:/repo/Recordia-Kubernetes fetch origin develop
git -C C:/repo/Recordia-Kubernetes show --stat origin/develop
git -C C:/repo/Recordia-Kubernetes diff <REV_ANTERIOR>..origin/develop -- `
  DEV/Recordia/Manifests/TicketFlow.UI
```

Un error anterior al commit de manifiestos no cambia el entorno. Comprobar la
tarea exacta de la timeline antes de hablar de rollback.

## 4. Acceder a ArgoCD cuando DEV no permite `kubectl`

El endpoint de EKS de DEV es privado. Desde este equipo `kubectl` puede agotar
el tiempo de espera aunque las credenciales sean correctas. No interpretar eso
como una caída de DEV.

ArgoCD sí permite consultar aplicaciones, pods, manifiestos y logs. La contraseña
se obtiene de SSM en la cuenta `root` y solo se usa para crear una sesión:

```python
import json
import boto3
import requests

base = "https://argocd.cloudworldwideservices.com"
app = "dev-recordia-ticketflow.ui-manifests"
password = boto3.Session(profile_name="root", region_name="eu-west-1").client("ssm").get_parameter(
    Name="/INF/argocd/admin/password", WithDecryption=True,
)["Parameter"]["Value"]

http = requests.Session()
response = http.post(base + "/api/v1/session", json={"username": "admin", "password": password}, timeout=30)
response.raise_for_status()
http.headers["Authorization"] = "Bearer " + response.json()["token"]
```

No imprimir `password`, el token de sesión ni el contenido completo de un
Secret.

### Estado de la aplicación

```python
data = http.get(base + "/api/v1/applications/" + app, timeout=30).json()
print(json.dumps({
    "source": data["spec"]["source"],
    "sync": data["status"].get("sync"),
    "health": data["status"].get("health"),
    "images": data["status"].get("summary", {}).get("images"),
}))
```

Revisar conjuntamente:

- `sync.status == Synced`;
- `health.status == Healthy`;
- `sync.revision` es el commit GitOps esperado;
- `summary.images` solo contiene el tag nuevo.

### Árbol de recursos y pods

```python
tree = http.get(base + "/api/v1/applications/" + app + "/resource-tree", timeout=30).json()
pods = [node for node in tree.get("nodes", []) if node.get("kind") == "Pod"]
print(json.dumps([{"name": p["name"], "health": p.get("health")} for p in pods]))
```

Durante un rolling update aparecen a la vez pods viejos y nuevos. No declarar el
despliegue terminado hasta que solo quede la imagen nueva.

### Manifiesto vivo de un recurso

El parámetro correcto es `resourceName`, no `name`:

```python
response = http.get(base + "/api/v1/applications/" + app + "/resource", params={
    "resourceName": "ticketflow-ui-worker-xxxxxxxxxx-yyyyy",
    "namespace": "recordia",
    "group": "",
    "version": "v1",
    "kind": "Pod",
}, timeout=30)
response.raise_for_status()
pod = json.loads(response.json()["manifest"])
print(json.dumps({
    "created": pod["metadata"].get("creationTimestamp"),
    "containers": [{"name": c["name"], "image": c["image"], "envFrom": c.get("envFrom")} for c in pod["spec"]["containers"]],
    "status": [{"name": c["name"], "ready": c["ready"], "restarts": c["restartCount"], "imageID": c.get("imageID")}
               for c in pod.get("status", {}).get("containerStatuses", [])],
}))
```

Cambiar `kind` y `resourceName` permite leer `Deployment` o `ConfigMap`. No
solicitar `Secret` para una comprobación rutinaria.

### Logs de un pod

```python
response = http.get(base + "/api/v1/applications/" + app + "/pods/<POD>/logs", params={
    "namespace": "recordia",
    "container": "ticketflow-ui",
    "tailLines": "800",
    "follow": "false",
}, timeout=30)
response.raise_for_status()
lines = [json.loads(row).get("result", {}).get("content", "") for row in response.text.splitlines()]
```

Buscar evidencia concreta, sin volcar todos los logs:

- `Copying config files from s3://recordia-config/DEV/TicketFlow.UI`;
- `Applying app.<migración>... OK`;
- carga de `app.tasks.poll_m365_mailboxes` en worker/beat;
- `Task app.tasks.poll_m365_mailboxes[...] succeeded`;
- ausencia de `OperationalError`, `InconsistentMigrationHistory`, errores de
  token y errores de delta.

Actualmente `deployment-migrate` tiene cero réplicas. `start_web` aplica las
migraciones al arrancar, protegido por un bloqueo MySQL para que varios pods no
migren a la vez. Por eso la evidencia de una migración nueva suele estar en los
logs del pod web, no en un pod `migrate`.

## 5. Cómo llega el `.env` de S3 al pod

Los ConfigMaps reales definen, entre otras cosas:

```yaml
SHORT_ENV: DEV
CONFIG_BUCKET_NAME: recordia-config
CONFIG_BUCKET_SERVICE_FOLDER: TicketFlow.UI
```

El entrypoint corporativo ejecuta al arrancar:

```bash
aws s3 cp \
  s3://$CONFIG_BUCKET_NAME/$SHORT_ENV/$CONFIG_BUCKET_SERVICE_FOLDER \
  /app/service/$ARTIFACT_NAME.$ARTIFACT_VERSION \
  --recursive
```

Por tanto, DEV descarga `s3://recordia-config/DEV/TicketFlow.UI/.env` y STA
descarga `s3://recordia-config/STA/TicketFlow.UI/.env`.

Django carga ese fichero con `load_dotenv(..., override=False)`. La precedencia
real es:

1. variable del proceso procedente del ConfigMap;
2. valor del `.env` descargado desde S3;
3. valor por defecto definido en `settings.py`.

Que una clave no aparezca en el ConfigMap de ArgoCD no significa que use el
default del código: puede venir del `.env` de S3. Para revisar configuración,
leer únicamente las claves no secretas necesarias:

```powershell
aws s3 cp s3://recordia-config/DEV/TicketFlow.UI/.env - `
  --profile recordia --only-show-errors |
  Select-String '^(OUTBOUND_EMAIL_ENABLED|INBOUND_MARK_READ_ENABLED)='
```

No imprimir el `.env` completo: contiene credenciales. Para cambiarlo de forma
segura:

1. descargar el objeto y guardar una copia local fuera de Git;
2. conservar todas las claves no afectadas y los metadatos del objeto;
3. escribir con condición `If-Match` sobre el ETag leído, para no pisar una
   edición concurrente;
4. volver a leer el objeto y comprobar solo las claves cambiadas;
5. crear pods nuevos, porque el `.env` se descarga únicamente al arrancar.

El bucket tiene versionado habilitado, pero la copia local permite una
recuperación inmediata y auditable.

No añadir la copia de seguridad ni el `.env` descargado al repositorio.

La forma declarativa de reiniciar por un cambio solo de configuración es editar
los ConfigMaps o una anotación del pod template en `Recordia-Kubernetes`, hacer
push a `develop` y dejar que ArgoCD aplique el rolling update. Un cambio en S3
sin pods nuevos no modifica procesos que ya están ejecutándose.

## 6. Qué comprobar antes de decir «desplegado»

La entrega está completa únicamente cuando se cumplen todos estos puntos:

- tests, `check`, migraciones y `git diff --check` correctos;
- rama y commit publicados;
- tag remoto apunta al commit esperado;
- CI finalizada con éxito;
- recurso `artifact-repo` de la CI muestra el tag y commit esperados;
- ECR contiene el tag construido;
- CD finalizado con éxito;
- `Recordia-Kubernetes/develop` contiene el nuevo tag solo en el entorno pedido;
- ArgoCD está `Synced` y `Healthy` en la revisión GitOps esperada;
- solo quedan pods nuevos, `ready=true`, sin reinicios inesperados;
- los pods muestran el digest/tag esperado;
- los logs confirman la descarga de la configuración del entorno correcto;
- la migración aparece aplicada o existe evidencia funcional equivalente;
- las tareas críticas completan una vuelta sin errores;
- los flags de seguridad se comprueban en la fuente que realmente los suministra.

## Errores de interpretación que hay que evitar

- `TicketFlow.UI/k8s/configmap.yaml` es una referencia del repositorio de la
  aplicación. El runtime se controla desde `Recordia-Kubernetes` y S3.
- Un ConfigMap sin una clave no prueba que Django use el default; revisar S3.
- `Healthy` en ArgoCD no prueba que el código correcto esté ejecutándose;
  revisar imagen, digest, revisión GitOps y pods.
- Una CI verde no prueba que construyó nuestro commit; revisar
  `resources.repositories.artifact-repo`.
- No ejecutar el CD de TicketFlow con la plantilla que solo admite
  `deployment.yaml`.
- Un timeout de `kubectl` contra DEV suele indicar el endpoint privado, no una
  caída. Usar ArgoCD para diagnóstico de solo lectura.
- Cambiar S3 no reinicia pods.
- No desplegar un prerelease en STA o PRO: la validación de CD lo bloquea.
- No mostrar tokens, contraseñas, SecureStrings ni el `.env` completo en logs o
  respuestas.
