# Disponibilidad de la web en PRO

`pro-web-availability.patch` contiene el ajuste de infraestructura para evitar
el corte observado el 27/10/2026: Karpenter desalojó la única web de TicketFlow
por consolidación y hubo un intervalo sin destinos sanos en el balanceador.

El parche modifica únicamente estos archivos de **Recordia-Kubernetes**:

- `PRO/Recordia/Manifests/TicketFlow.UI/deployment-web.yaml`
- `PRO/Recordia/Manifests/TicketFlow.UI/pdb-web.yaml` (nuevo)

Mantiene dos webs en nodos distintos, un PDB con `minAvailable: 1` y un despliegue
con `maxUnavailable: 0`, `maxSurge: 1` y `minReadySeconds: 15`. Añade una espera de
20 segundos antes de SIGTERM y un plazo de terminación de 60 segundos. El PDB
se instala en la onda `-2`, antes del Job de migración (`-1`) y de la web (`0`).

El cambio conserva la imagen, configuración, probes y recursos por pod. Añade
una web de 400 MiB y 100m de CPU reservados. La anti-afinidad puede requerir
temporalmente un tercer nodo durante un despliegue. Protege la retirada
voluntaria de nodos y separa las webs por hostname; la distribución entre zonas
no forma parte de este ajuste.

## Aplicación

Este repositorio conserva el parche para su revisión y promoción. La CD actual
actualiza las imágenes en Recordia-Kubernetes; **publicar en `Feature/init` o
desplegar una imagen de TicketFlow no aplica este parche automáticamente**.
El runtime sigue la rama `develop` de Recordia-Kubernetes mediante ArgoCD.

Una persona con permisos de contribución en Recordia-Kubernetes debe aplicarlo
en un checkout limpio, actualizado desde `develop`. Adaptar las rutas locales:

```powershell
git -C C:\ruta\Recordia-Kubernetes apply --check C:\repo\TicketFlow.UI\k8s\gitops\pro-web-availability.patch
```

Si la comprobación termina correctamente:

```powershell
git -C C:\ruta\Recordia-Kubernetes apply C:\repo\TicketFlow.UI\k8s\gitops\pro-web-availability.patch
git -C C:\ruta\Recordia-Kubernetes diff --check
git -C C:\ruta\Recordia-Kubernetes diff -- PRO/Recordia/Manifests/TicketFlow.UI
```

Revisar los dos manifiestos, confirmar que la versión de la imagen se conserva
y publicar mediante el flujo de revisión habitual. Si `apply --check` falla,
revisar los cambios recientes de los manifiestos y adaptar el parche antes
de continuar. La base validada de Recordia-Kubernetes es `65078ab`.

## Verificación

El parche se ha aplicado en un índice Git temporal sobre la base indicada y el
resultado coincide con el cambio de infraestructura `c9b155d`. Los manifiestos
resultantes pasan validación con el OpenAPI oficial de Kubernetes v1.36.3 y
mantienen coherentes los selectores de Deployment, Service y PDB.

Después de publicar en Recordia-Kubernetes, comprobar:

1. ArgoCD `pro-recordia-ticketflow.ui-manifests-b` Healthy/Synced en la revisión
   que contiene el parche.
2. Dos webs Ready/Available en nodos diferentes y dos targets sanos en el ALB.
3. PDB `ticketflow-ui-web` con dos webs sanas y un mínimo requerido de una.
4. Respuesta correcta de `/livez`, `/readyz` y `/internalz`, incluidos los estados
   internos de base de datos, Celery e ingesta. Worker y beat deben seguir sanos.

El [runbook de CI/CD](../../docs/ci-cd-runtime-runbook.md) describe las fuentes
de verdad y las comprobaciones del entorno.
