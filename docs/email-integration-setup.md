# TicketFlow — Configuración de integración de email

## Contexto

TicketFlow reemplaza Zendesk como sistema de gestión de tickets. El correo es el canal principal de entrada de tickets. Este documento recoge toda la configuración realizada para habilitar la lectura de buzones de helpdesk y el envío de notificaciones a clientes.

---

## Infraestructura de email por marca

| Marca | Buzón de helpdesk | Proveedor de email | Método de integración |
|---|---|---|---|
| Cloudwws | helpdesk@cloudwws.com | Microsoft 365 | Microsoft Graph API |
| Comunycarse | helpdesk@comunycarse.com | Microsoft 365 | Microsoft Graph API |
| Comunycarse | soporte2@comunycarse.com | Microsoft 365 | Microsoft Graph API |
| eComFax | helpdesk@ecomfax.com | Microsoft 365 | Microsoft Graph API |
| Recordia | helpdesk@recordia.net | Amazon SES (eu-west-1) | SES Receipt Rules → webhook |
| FxSigner | helpdesk28224@fxsigner.com | Amazon SES (eu-west-1) | SES Receipt Rules → webhook |

### Cómo se descubrió el proveedor
Consulta de registros MX con `nslookup -type=MX <dominio>`:
- `*.mail.protection.outlook.com` → Microsoft 365
- `inbound-smtp.eu-west-1.amazonaws.com` → Amazon SES

---

## Azure AD — App Registration (Microsoft 365)

Permite que TicketFlow lea los buzones de helpdesk de los dominios en Microsoft 365 sin credenciales de usuario.

### Datos de la aplicación

| Campo | Valor |
|---|---|
| Display name | TicketFlow Mail Reader |
| Application (client) ID | `ceccd3ef-7fad-49b8-8cd7-9fe2f5e3475c` |
| Directory (tenant) ID | `4810d7e9-64fd-4a61-aaf0-b6289ef860c3` |
| Object ID | `9f5993af-4d45-4e13-9f1d-d680db57b024` |
| Supported account types | Single tenant (Cloud Worldwide Services, S.L.U) |
| Publisher domain | comunycarse.com |
| Client secret expiry | ~Mayo 2028 (24 meses desde creación) |

> **El client secret NO se almacena aquí.** Debe estar en el `.env` del servidor bajo la clave `AZURE_CLIENT_SECRET`.

### Permisos API concedidos

| Permiso | Tipo | Estado |
|---|---|---|
| Mail.Read | Application (sin usuario) | Granted (admin consent) |
| Mail.ReadWrite | Application (sin usuario) | Granted (admin consent) |
| Mail.Send | Application (sin usuario) | ⚠️ **PENDIENTE de conceder** — requerido por el envío saliente vía Graph `sendMail` (marcas con `mailbox_type=m365`). Tras conceder, re-verificar la Application Access Policy con `Test-ApplicationAccessPolicy` (RestrictAccess también limita el envío, que es lo deseado). |

### Dónde gestionar la app
[Azure Portal → App registrations → TicketFlow Mail Reader](https://portal.azure.com/#view/Microsoft_AAD_RegisteredApps/ApplicationMenuBlade/~/Overview/appId/ceccd3ef-7fad-49b8-8cd7-9fe2f5e3475c)

---

## Exchange Online — Application Access Policies

Restringe el acceso de la app únicamente a los buzones de helpdesk, impidiendo que pueda leer el correo de cualquier otro usuario del tenant.

### Política creada

| Campo | Valor |
|---|---|
| ScopeName | Helpdesk |
| AppId | ceccd3ef-7fad-49b8-8cd7-9fe2f5e3475c |
| AccessRight | RestrictAccess |
| Descripción | TicketFlow |

El grupo de ámbito "Helpdesk" engloba los cuatro buzones de helpdesk de M365. Verificado con `IsValid: True`.

### Cómo verificar el acceso a un buzón

```powershell
Connect-ExchangeOnline -UserPrincipalName <admin>@cloudwws.com
Test-ApplicationAccessPolicy -AppId ceccd3ef-7fad-49b8-8cd7-9fe2f5e3475c -Identity helpdesk@cloudwws.com
```

Resultado esperado: `AccessCheckResult : Granted`

### Cómo añadir un buzón nuevo en el futuro

```powershell
Connect-ExchangeOnline -UserPrincipalName <admin>@cloudwws.com
New-ApplicationAccessPolicy -AppId ceccd3ef-7fad-49b8-8cd7-9fe2f5e3475c -PolicyScopeGroupId <nuevo-buzon@dominio.com> -AccessRight RestrictAccess -Description "TicketFlow"
```

### Cómo revocar el acceso (si se desactiva TicketFlow)

```powershell
Get-ApplicationAccessPolicy | Where-Object {$_.AppId -eq "ceccd3ef-7fad-49b8-8cd7-9fe2f5e3475c"} | Remove-ApplicationAccessPolicy
```

---

## Variables de entorno necesarias en `.env`

```env
# Azure AD / Microsoft Graph (buzones M365 — lectura y envío)
AZURE_TENANT_ID=4810d7e9-64fd-4a61-aaf0-b6289ef860c3
AZURE_CLIENT_ID=ceccd3ef-7fad-49b8-8cd7-9fe2f5e3475c
AZURE_CLIENT_SECRET=<valor del secret — ver gestor de contraseñas>

# Email saliente
OUTBOUND_EMAIL_ENABLED=true            # kill switch global (false en local)
AWS_SES_REGION=eu-west-1
# SES_CONFIGURATION_SET=ticketflow     # opcional: tracking de bounces/quejas
TICKETFLOW_PUBLIC_URL=https://<host>   # base de los enlaces {{ticket_url}} en plantillas
```

> **SES sin credenciales SMTP.** El envío usa la API SESv2 con boto3 y el **rol IAM
> del pod** (igual que SQS/SSM): no hay usuario/contraseña SMTP que gestionar ni
> rotar. Requisitos en AWS:
> 1. `ses:SendEmail` en la policy del rol del worker (idealmente scoped a las
>    identidades verificadas).
> 2. Identidades de dominio verificadas (DKIM) para las marcas que envían por SES.
> 3. Cuenta SES **fuera del sandbox** en eu-west-1 (en sandbox solo se puede enviar
>    a destinatarios verificados → todas las confirmaciones fallarían; los fallos
>    quedan visibles en `OutboundEmailLog` con status `failed`).

---

## Buzones de Zendesk que se perderán al migrar

Estos son nativos de Zendesk (`@*.zendesk.com`) y desaparecerán cuando se cancele la suscripción. No hay acción técnica necesaria — los clientes usarán únicamente los buzones de dominio propio.

- support@comunycarse.zendesk.com
- internalsupport@comunycarse.zendesk.com
- devsupport@comunycarse.zendesk.com
- support@recordia.zendesk.com
- support@fxsigner.zendesk.com
- support@ecomfax.zendesk.com
- support@ifema.zendesk.com
- support@comunycarsesns.zendesk.com
- support@cloudwws.zendesk.com

---

## Triggers de Zendesk a replicar en TicketFlow

Los triggers con uso activo real que hay que implementar como notificaciones por email:

| Trigger | Cuándo | Destinatario | Idioma |
|---|---|---|---|
| Acuse de recibo | Ticket creado | Cliente (requester) | ES / EN |
| Respuesta de agente | Comentario público añadido | Cliente (requester) | ES / EN |
| Ticket resuelto | Estado → resolved | Cliente (requester) | ES / EN |
| Asignación | Ticket asignado a agente | Agente (assignee) | — |

---

## Automations de Zendesk a replicar

Implementadas como tareas periódicas de Celery Beat:

| Automation | Condición | Acción |
|---|---|---|
| Auto-cierre | 5 días en estado `resolved` | Cambiar a `closed` |
| CSAT | 1 hora en estado `resolved` | Enviar email solicitud de valoración |
| Alerta ticket abierto | 24 horas en estado `open` sin respuesta | Notificar al agente |

---

## Horario laboral

- **Zona horaria:** Europe/Madrid (GMT+2)
- **Horario:** Lunes a Viernes, 09:00 – 18:00
- **Festivos:** Sábado y domingo cerrado

---

## Email saliente — diseño implementado (sprint junio 2026)

- **Routing por marca** (`Brand.mailbox_type`): `ses` → API SESv2 con boto3 (rol IAM),
  `m365` → Graph `sendMail` con las credenciales MSAL existentes (requiere Mail.Send).
- **Plantillas** (`ResponseTemplate`): por clave + marca + idioma (ES/EN) con variables
  `{{ticket_id}}`, `{{requester_name}}`, etc. Administración en el panel admin
  (pestaña Plantillas, con vista previa) y API `api/admin/response-templates/`.
  El subject de `ticket_created` lleva `[Ticket #N]`: es el mecanismo de threading
  de las respuestas del cliente.
- **Confirmación de creación**: `TicketService.create_ticket` encola la confirmación
  al solicitante en su idioma (`ticket.language` → `brand.language` → es) desde la
  dirección de su marca; tarea Celery `send_outbound_email` con reintentos.
- **Anti-bucles** (defensa en profundidad): detección de cabeceras de auto-respuesta
  entrantes (`Auto-Submitted`, `X-Auto-Response-Suppress`, `Precedence`, `List-Id`),
  blocklist de buzones propios, patrones noreply, dedup por (plantilla, ticket,
  destinatario) y cabeceras de supresión en lo que enviamos.
- **Auditoría**: `OutboundEmailLog` (queued/sent/failed/suppressed + motivo), visible
  en el admin de Django.

---

## Estado de implementación

| Componente | Estado |
|---|---|
| Azure AD App Registration | ✅ Completado |
| Exchange Online Access Policy | ✅ Completado |
| Lectura buzones M365 (Graph API) | ✅ Completado — poll Celery cada 2 min |
| Email saliente (código: SESv2 + Graph sendMail) | ✅ Implementado — tras kill switch `OUTBOUND_EMAIL_ENABLED` |
| Permiso Graph `Mail.Send` + admin consent | ⏳ Pendiente (bloquea envío M365) |
| SES production access + identidades DKIM + IAM `ses:SendEmail` | ⏳ Pendiente (bloquea envío SES) |
| Confirmación de creación al cliente (acuse de recibo ES/EN) | ✅ Implementado |
| Lectura buzones SES (recordia, fxsigner) | ⏳ Pendiente — falta revisar receipt rules actuales |
| Triggers restantes (respuesta de agente, resuelto, asignación) | ⏳ Pendiente — el sistema de plantillas ya los soporta (añadir claves nuevas) |
| Automations (Celery Beat: auto-cierre, CSAT, alerta 24h) | ⏳ Pendiente |
| Horario laboral en BD | ⏳ Pendiente |
