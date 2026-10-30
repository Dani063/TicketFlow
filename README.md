# TicketFlow

Aplicación de gestión de tickets de soporte que sustituye a Zendesk, multi-marca (cada
«entidad» es una marca, con su propio buzón de soporte). Backend en Django + Celery,
frontend sin framework (plantillas + JS/CSS servidos directamente). Se despliega en EKS;
el código vive en Azure Repos y las pipelines son de Azure DevOps, pero toda la
infraestructura de runtime es AWS (ALB, SSM, SQS, SES, RDS MySQL).

## Qué es este repositorio

A pesar del nombre, **`TicketFlow.UI` no es solo el frontend**: es el monorepo completo,
backend y frontend en el mismo sitio. No existe un repositorio de backend separado.

## Stack técnico

- **Backend**: Django, Celery (SQS en producción, Redis en local)
- **Base de datos**: MySQL / Amazon RDS
- **Email**: Microsoft Graph (M365) para entrada y salida, Amazon SES como canal alternativo de salida
- **Identidad**: Azure AD (SSO corporativo)
- **Infraestructura**: EKS (AWS), ALB, SSM Parameter Store, SQS, SES
- **Frontend**: plantillas Django + JS/CSS sueltos, sin build ni framework (SPA por fragmentos)

## Estructura del repositorio

| Ruta | Contenido |
|---|---|
| `src/` | Código Django (`app/`), configuración (`TicketFlow/`), `main.py` como `manage.py` |
| `resources/` | Plantillas y estáticos (CSS/JS/vendor) del frontend |
| `docs/` | Documentación de arquitectura, despliegue, CI/CD e integraciones — ver [`docs/README.md`](docs/README.md) |
| `k8s/` | Manifiestos de Kubernetes de referencia (llevan placeholders; el despliegue real lo gestionan las pipelines de Azure DevOps) |

## Arranque en local

Dos modos, siempre desde `src/`.

Rápido, sin AWS ni túnel, contra una SQLite con datos clonados:

```bash
cd src
USE_SQLITE=1 DISABLE_AWS_SSM=1 python main.py runserver
```

Completo, contra la base de datos de desarrollo compartida (necesita un túnel SSM a RDS en
otra terminal — detalles en `TicketFlow.UI/CLAUDE.md`):

```bash
cd src
python main.py runserver
```

**Autenticación en local**: entra por `/dev-login/` (solo pide email, sin contraseña; solo
existe con `DEBUG=True`), no por `/login/` (redirige al SSO corporativo).

## Tests

Las instrucciones para asistentes de código están también en [`AGENTS.md`](AGENTS.md).

```bash
# Desde la raíz del repositorio. Usa SQLite, evita AWS y acelera el hash
# de las contraseñas creadas por los tests (sin cambiar producción).
python scripts/run_tests.py app

# Durante el desarrollo, ejecutar solo el módulo afectado.
python scripts/run_tests.py app.tests_ticket_views

# Antes de integrar cambios, repetir la suite completa y ver los más lentos.
python scripts/run_tests.py app --timing --durations 20
```

El ejecutor rápido usa exactamente la misma suite y aplica todas las migraciones.
Para comprobar la configuración sin ejecutar tests:
`python src/main.py check --settings=TicketFlow.settings_test`.

La suite escupe tracebacks y warnings a propósito (hay tests que provocan fallos de IA, SES
y CSRF deliberadamente); el resultado que importa es el `OK`/`FAILED` final.

## Documentación

El resto de la documentación técnica (infraestructura, arquitectura de servicios,
despliegue, CI/CD, integración de email) está en [`docs/`](docs/README.md).

## Convenciones

Commits en español con prefijo de conventional commits (`feat(home):`, `fix(ui):`,
`fix(ia):`...). Comentarios y documentación, en español. La rama de trabajo es
`Feature/init`; `origin` es Azure Repos y existe además un remoto `github` secundario.
