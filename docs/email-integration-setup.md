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
# Azure AD / Microsoft Graph (buzones M365)
AZURE_TENANT_ID=4810d7e9-64fd-4a61-aaf0-b6289ef860c3
AZURE_CLIENT_ID=ceccd3ef-7fad-49b8-8cd7-9fe2f5e3475c
AZURE_CLIENT_SECRET=<valor del secret — ver gestor de contraseñas>

# Amazon SES (email saliente + buzones Recordia/FxSigner)
AWS_SES_SMTP_HOST=email-smtp.eu-west-1.amazonaws.com
AWS_SES_SMTP_PORT=587
AWS_SES_SMTP_USER=<SMTP user de SES>
AWS_SES_SMTP_PASSWORD=<SMTP password de SES>
```

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

## Estado de implementación

| Componente | Estado |
|---|---|
| Azure AD App Registration | ✅ Completado |
| Exchange Online Access Policy | ✅ Completado |
| Email saliente (SES SMTP) | ⏳ Pendiente — falta config SES |
| Lectura buzones M365 (Graph API) | ⏳ Pendiente — implementación en TicketFlow |
| Lectura buzones SES (recordia, fxsigner) | ⏳ Pendiente — falta revisar receipt rules actuales |
| Notificaciones email al cliente | ⏳ Pendiente |
| Automations (Celery Beat) | ⏳ Pendiente |
| Horario laboral en BD | ⏳ Pendiente |
