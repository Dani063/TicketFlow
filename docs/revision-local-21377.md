# Guía de revisión local conjunta de TicketFlow

Preparada para Daniel el 27/10/2026. Incluye las tarjetas de Codex y Claude en
una rama de revisión local. La regla de On-hold que se prueba aquí es la última
confirmada por Daniel: **el agente lo activa; a las 72 horas pasa a Closed;
las notas internas y los cambios de campos no lo interrumpen ni reinician el
plazo; una respuesta del cliente lo devuelve a Open y reanuda el SLA restante**.
Resolved es un estado independiente y no controla ese plazo.

## 1. Qué vas a abrir

| Elemento | Valor |
|---|---|
| Carpeta de revisión | `C:\repo\TicketFlow.RevisionLocal` |
| Rama | `review/21377-local` |
| Base de Claude y otros cambios locales | `Feature/init` @ `1e7055a` |
| Parte de Codex incorporada | `feat/21377-vistas-papelera` @ `014c238` |
| Integración y reglas finales de On-hold | `2b989b8` |
| Python compartido | `C:\repo\TicketFlow.UI\.venv\Scripts\python.exe` |
| Aplicación | <http://127.0.0.1:8010/> |
| Entrada sin SSO | <http://127.0.0.1:8010/dev-login/> |
| Datos de revisión | `.review-local\db.sqlite3`, dentro de esta carpeta |

La rama de revisión contiene ambas partes y las correcciones de integración.
La migración 0070 depende de la 0069: hay una sola cadena hasta la 0073.
Se conserva la versión más reciente de `create_ticket.js` y se cargan los
módulos de presencia y papelera.
La rama de revisión queda en local, sin push ni despliegue.

Esta revisión usa datos sintéticos: **no se conecta a RDS, no consulta AWS SSM
y no envía correo**. Los adjuntos se guardan en `.review-local\media`.
No necesita VPN ni Redis. El navegador carga las bibliotecas estáticas desde
sus CDN, por lo que conviene tener conexión a Internet.

## 2. Arranque: copiar y ejecutar en PowerShell

```powershell
Set-Location C:\repo\TicketFlow.RevisionLocal
& C:\repo\TicketFlow.UI\.venv\Scripts\python.exe scripts/review_local.py prepare
& C:\repo\TicketFlow.UI\.venv\Scripts\python.exe scripts/review_local.py run
```

Deja esa consola abierta mientras pruebas. **Ctrl+C** detiene el servidor.
El lanzador establece las variables locales de forma explícita; no necesitas
editar ni copiar un `.env`. Aplica migraciones únicamente en la SQLite de
revisión y comprueba que el correo y la IA estén desactivados.

Si el puerto está ocupado, usa `--port 8011` tanto en `prepare` como en `run`
y abre el enlace que indique la consola.

Después de `prepare`, abre el índice generado:

[`../.review-local/escenarios.md`](../.review-local/escenarios.md)

Ese índice contiene los **IDs y enlaces reales de los 28 tickets de ejemplo**.
En esta guía nos referimos a ellos por su código 01–28, no por un ID fijo.

### Cuentas

En `/dev-login/` introduce el email; no pide contraseña:

| Cuenta | Email | Uso |
|---|---|---|
| Administrador | `qa.admin@example.test` | Administración y permisos |
| Agente A | `qa.agente.a@example.test` | Sesión principal de las pruebas |
| Agente B | `qa.agente.b@example.test` | Presencia y contraste de asignación |
| Cliente | `qa.cliente@example.test` | Respuesta del cliente y acceso limitado |
| Persona en copia | `qa.cc@example.test` | Visibilidad como CC |

Para usar dos identidades a la vez, abre un perfil normal y otro de incógnito,
o dos perfiles distintos. Dos pestañas de un mismo perfil comparten la sesión.

### Restablecer los ejemplos

```powershell
& C:\repo\TicketFlow.UI\.venv\Scripts\python.exe scripts/review_local.py reset-data
```

Esto borra y vuelve a crear **solo los tickets sintéticos cuyo asunto empieza
por `[QA 21377]`**, dentro de esta SQLite. Cambian los IDs: vuelve a abrir el
índice. Úsalo entre rondas si has cerrado, borrado o cambiado muchos ejemplos.

## 3. Recorrido manual recomendado

Primero prueba vistas y contadores sin editar los ejemplos. Después prueba
estados, papelera y presencia. Al final ejecuta las pruebas automáticas de correo.

Puedes anotar cada caso como **OK / Fallo / Pendiente**, indicando el código
del ejemplo, el usuario y lo que has observado.

### 01 · Vistas, home y contadores — Codex, #21383

**Usuario:** Agente A. **Ejemplos:** 01–09 y filas 15–27.

1. Abre la home y «Tus tickets no resueltos (sin tareas)».
2. Compara las filas de ambas listas. El ejemplo 02 tiene 120 días y debe salir.
3. Comprueba que el 04, asignado a B, no aparece en la cola personal de A.
4. Comprueba que no se incluyen la tarea 05, el resuelto 06, el On-hold 07,
   el cerrado 08 ni el eliminado 09.
5. Abre «Todos los tickets no resueltos (sin tareas)»: incluye el 03 en
   «Sin asignar», el 04 y los tickets personales de A.
6. Exporta esa vista a CSV y compara IDs y número de filas con la lista.
7. Abre una vista histórica y cambia el rango: ahí sí debe aplicarse la fecha.

**Resultado:** la home y la cola personal coinciden; lista, contador y CSV usan
el mismo conjunto. Las colas activas indican «Sin límite de fecha». On-hold
tiene su vista de espera y queda fuera de las colas no resueltas según el
criterio de esta feature; eso no lo convierte al estado Resolved.

El índice de ejemplos muestra los contadores iniciales. Dejan de ser una
referencia fija cuando empieces a modificar tickets.

### 02 · Actualizar y volver a montar la lista — Codex, #21387

1. Deja abierta la lista en una vista, orden y página que puedas reconocer.
2. Desde otra pestaña crea un ticket de prueba asignado a A, con producto
   Recordia y un asunto fácil de encontrar.
3. Vuelve a la lista y pulsa su botón de actualizar, sin recargar el navegador.
4. Cambia tres veces entre home, lista y ticket. Repite la actualización y
   pasa el ratón por un asunto para abrir la vista previa.

**Resultado:** se actualizan filas y contadores, se mantienen vista, página y
orden, y cada acción ocurre una vez. Si el orden coloca el ticket nuevo en otra
página, búscalo allí: actualizar conserva la página actual.

El cambio de día de los rangos relativos está cubierto por la prueba de navegador;
en una vista histórica puedes comprobar manualmente que un rango personalizado
no se sustituya por uno relativo.

### 03 · Cambiar estado sin comentario — Claude, #21393

**Ejemplo:** 11.

1. Deja el compositor vacío y cambia de Open a Pending.
2. Guarda/publica el cambio y vuelve a abrir el ticket.
3. Comprueba el historial y que el mensaje inicial no haya desaparecido.

**Resultado:** estado actualizado sin comentario vacío. Si no cambias ni estado
ni contenido, no debe generarse un comentario nuevo.

### 04 · Guardar los CC antes de enviar — Claude, #21385

1. En un ticket editable, selecciona `QA Copia` y `QA Agente B` como CC.
2. Escribe una respuesta pública y pulsa directamente el botón de enviar.
3. Vuelve a abrir el ticket y comprueba que ambas personas siguen en CC.
4. Repite quitando una de ellas antes de la siguiente respuesta.

**Resultado:** los CC guardados corresponden a lo seleccionado en el momento
de pulsar enviar. El entorno local guarda el comentario pero **no manda correo**.
La inclusión de esas copias en la entrega se valida en el apartado de tests.

### 05 · Volver a la cola tras guardar — Claude, #21382

1. Abre un ticket desde la lista de tickets no resueltos.
2. Escribe una respuesta o modifica un campo y guarda.
3. Comprueba la navegación y la cabecera al volver a abrirlo.

**Resultado:** regresa a la cola definida por la aplicación
(`all_unsolved_no_tareas`), sin dejar una ficha desactualizada. El estado y la
fecha de actualización reflejan el guardado.

### 06 · Hora española — Claude

**Ejemplo:** 12.

1. Busca su fila, abre la vista previa y entra en el ticket.
2. Compara la hora del mensaje de referencia en esos lugares y en una exportación.

**Resultado:** el instante de prueba, **27/10/2026 08:00 UTC**, aparece como
**09:00 en Madrid**. No debe aparecer una hora distinta según se abra por
navegación normal o por las pestañas internas de la aplicación.

### 07 · On-hold, notas internas y SLA — Claude e integración, #21384

**Ejemplo:** 07. Antes de empezar puedes consultar los relojes:

```powershell
& C:\repo\TicketFlow.UI\.venv\Scripts\python.exe scripts/review_local.py inspect --case 07
```

1. Anota `on_hold_at`, los vencimientos y `sla_paused_seconds`.
2. Añade una nota interna como agente A y guarda.
3. Cambia el asunto, la prioridad u otro campo manteniendo el estado On-hold.
4. Ejecuta de nuevo `inspect --case 07`.
5. Desde la sesión del cliente, responde públicamente en ese mismo ticket.
6. Consulta de nuevo los relojes.

**Resultado:** la nota y la edición conservan On-hold y el `on_hold_at` original.
No reinician las 72 horas. La respuesta del cliente lo pasa a Open, limpia
`on_hold_at` y suma el tiempo de espera a `sla_paused_seconds`. El vencimiento
se desplaza por ese tiempo: el SLA sigue con el presupuesto que quedaba,
no vuelve a empezar desde cero. También puedes salir de On-hold mediante un
cambio explícito de estado del agente.

### 08 · Cierre a las 72 horas y tickets bloqueados — Claude, #21384

**Ejemplos:** 13 (73 horas), 14 (71 horas), 28 (Resolved antiguo) y 08 (Closed).

1. Si quieres probarlo, añade una nota interna o edita un campo del 13,
   conservando su estado On-hold.
2. En otra consola ejecuta:

```powershell
& C:\repo\TicketFlow.UI\.venv\Scripts\python.exe scripts/review_local.py close-expired
```

3. Actualiza los ejemplos 13, 14 y 28.
4. Intenta editar, responder, asignarte o subir un archivo al 08 y al 13 cerrado.

**Resultado:** se cierra el 13; el 14 continúa en On-hold. El 28 permanece
Resolved: su reloj de resolución no participa en este cierre. Los cerrados
quedan en solo lectura. El cierre de prueba usa la misma función de negocio
del sistema, únicamente sobre la SQLite local.

No necesitas esperar tres días ni arrancar Celery beat. La ingesta simulada
comprueba además que una respuesta del cliente a un ticket ya cerrado genera
un seguimiento nuevo, conservando la referencia al anterior.

### 09 · Papelera y restauración — Codex, #21390

**Ejemplo inicial:** 09.

1. Abre «Tickets eliminados» y restaura el 09 desde la lista.
2. Comprueba que desaparece de la papelera y vuelve a las listas que le corresponden.
3. Borra otro ejemplo editable, y comprueba que se guarda actor y hora.
4. Abre ese ticket directamente por su enlace y restaura desde el aviso de la ficha.
5. Repite una restauración ya hecha y alterna varias veces entre pestañas.

**Resultado:** se conserva contenido, CC y adjuntos; se registra un evento por
transición real. Un eliminado queda bloqueado hasta restaurarlo y no aparece
en los listados habituales ni en el historial del perfil. Restaurar una ficha
no borra los borradores que tengas abiertos en otras.

### 10 · Enlaces — Codex, #21386

**Ejemplo:** 10.

1. Pulsa «Enlace web de prueba» en la conversación.
2. Despliega «Ver mensaje original» y repite desde allí.
3. Comprueba los enlaces de correo y teléfono.

**Resultado:** HTTP/HTTPS abre otra pestaña y mantiene el ticket abierto.
Correo y teléfono conservan su acción nativa. El atributo de los enlaces web
es `rel="noopener noreferrer"` si lo inspeccionas en las herramientas del navegador.

### 11 · Párrafos e imágenes inline — Codex, #21381

**Ejemplo:** 10, creado por la ingesta con un correo y archivos simulados.

1. Comprueba que los dos párrafos se ven separados.
2. Debe verse un rectángulo violeta dentro del mensaje.
3. La galería debe mostrar `informe-qa.txt`, sin duplicar la imagen inline.
4. Descarga el archivo y revisa el mensaje original.
5. Cierra sesión y abre la URL de la imagen: debe requerir acceso al ticket.

**Resultado:** imagen visible mediante una URL protegida de TicketFlow,
sin un enlace `cid:` roto ni un archivo inline duplicado. Los tests verifican
también el caso `hasAttachments=false` y los reintentos sin duplicar adjuntos.

### 12 · Cognitia y Rightfax — Codex, #21388

1. Crea o edita un ticket y abre **Producto / servicio**.
2. Selecciona Cognitia; guarda y vuelve a abrir. Repite con Rightfax.
3. Comprueba orden e iconos.

**Resultado:** ambos productos son seleccionables y aparecen antes de
«Otros servicios». Es un selector distinto del campo «Servicio»; los tickets
antiguos no deben reclasificarse automáticamente.

### 13 · Dos agentes viendo y escribiendo — Codex, #21379

**Ejemplo:** 01. Necesitas las sesiones A y B en perfiles separados.

1. Abre el mismo ticket en ambas y deja las dos ventanas visibles.
2. Espera hasta 20 segundos: cada agente debe ver al otro.
3. Escribe un borrador en una sesión, sin enviarlo.
4. Comprueba en la otra el aviso de escritura; deja de escribir unos 10 segundos.
5. Cambia a otro ticket, oculta la pestaña o cierra una de las ventanas.

**Resultado:** el aviso de escritura vuelve a visualización al quedar inactivo;
al salir del ticket desaparece la presencia en menos de un minuto. No se
muestran otros agentes en un ticket diferente. Reabrir pestañas no multiplica
los avisos ni los latidos.

### 14 · Permisos y regresión básica — ambas partes

1. Entra como cliente y como persona en CC.
2. Comprueba que ven los tickets en los que participan, pero no las notas internas.
3. Intenta acceder a un ticket de otro cliente, a presencia y a restauración.
4. Revisa que adjuntos normales, búsqueda, filtros y navegación básica sigan funcionando.

**Resultado:** se conservan las restricciones de acceso; los avisos de presencia
y las acciones de papelera son para agentes. Un usuario final no puede cambiar
el estado administrativo ni ver contenido interno.

## 4. Correo: qué comprobar automáticamente en local

La revisión visual anterior no prueba una entrega de Microsoft 365. Aquí el
envío está desactivado para no usar el buzón real. Los siguientes tests usan
Graph/SES y buzones simulados; sí comprueban los datos que se enviarían.

```powershell
& C:\repo\TicketFlow.UI\.venv\Scripts\python.exe scripts/review_local.py test --tests app.tests_email_cc app.tests_email_thread app.tests_email_inline app.tests_sweep_folders app.tests_on_hold
```

| Tarjeta | Prueba que debe quedar cubierta |
|---|---|
| #21385 / CC | Guardar las copias antes de responder; snapshot y entrega con los CC correctos; notas privadas sin envío. |
| #21380 / acuse | Crear un ticket con respuesta pública del agente produce un solo correo, sin un segundo acuse automático. |
| #21389 / hilo | Asunto limpio y cabeceras `In-Reply-To`, `References` y `Message-ID`; la siguiente respuesta sigue el hilo correcto. |
| #21381 / contenido | Párrafos, CID, imágenes protegidas, paginación y ausencia de adjuntos duplicados. |
| #21419 / carpetas | Inbox y carpetas de usuario; carpetas de sistema excluidas; nuevas carpetas detectadas; mensajes movidos sin duplicar. |
| #21384 / On-hold | Un único aviso de cierre al entrar en espera, SLA pausado, respuesta del cliente que reabre, cierre a las 72 h y seguimiento de tickets cerrados. |

La confirmación de recepción real, la copia en Enviados y la recepción efectiva
por el solicitante y sus CC se comprueban después en un entorno desplegado con
cuentas de prueba controladas. No arranques el barrido del buzón real desde
esta carpeta para intentar probarlo en local.

## 5. Comprobaciones generales

Comprobaciones realizadas sobre la rama conjunta el 27/10/2026:

- **440 tests de `app` correctos**, 444,485 segundos.
- `check` sin incidencias y `makemigrations --check --dry-run` sin cambios pendientes.
- Chrome con la aplicación local real: home y cola personal coherentes, ticket
  antiguo incluido, On-hold excluido, imagen inline visible, enlace en otra
  pestaña, restauración desde ficha, presencia y escritura entre dos agentes.
- Los tests de On-hold incluyen conservación del reloj con notas/ediciones,
  respuesta del cliente que reabre y primera respuesta dentro de un SLA pausado.

Logs de esta comprobación, fuera de Git:
`C:\repo\.codex-tmp\21377-review-full.log` y
`C:\repo\.codex-tmp\21377-review-browser-result.json`.

```powershell
# Configuración y modelos/migraciones.
& C:\repo\TicketFlow.UI\.venv\Scripts\python.exe scripts/review_local.py check

# Suite completa de ambas partes, en una base de pruebas separada.
& C:\repo\TicketFlow.UI\.venv\Scripts\python.exe scripts/review_local.py test
```

El resultado esperado es `No changes detected` en las migraciones y `OK` al
final de los tests. Algunas pruebas provocan fallos de proveedor, CSRF o IA
para comprobar cómo se gestionan; la conclusión es el resumen final, no cada
traceback intermedio. La suite completa puede tardar varios minutos.

## 6. Cambios que no se validan con esta aplicación local

- **Disponibilidad de PRO:** `k8s/gitops/pro-web-availability.patch` contiene el
  ajuste de dos webs, separación por nodo y PDB. Está versionado, pero necesita
  aplicarse en Recordia-Kubernetes y verificarse con ArgoCD/ALB.
- **Buzón común en PRO:** la configuración de las seis empresas se corrigió
  directamente en la BBDD de PRO. La SQLite usa una marca y direcciones ficticias;
  no es una copia ni una comprobación de esa configuración de producción.
- **Cambios posteriores:** esta rama parte de los hashes indicados al principio.
  Si se añaden más commits a las ramas originales, hay que incorporarlos antes
  de considerar que esta revisión cubre también esos cambios.

## 7. Cómo anotar un fallo útil

```text
Caso y tarjeta:
Usuario utilizado:
Ticket / código de ejemplo:
Pasos exactos:
Resultado esperado:
Resultado observado:
¿Se reproduce tras recargar o solo al cambiar de pestaña?:
Captura / error de consola:
```

Para dar la revisión por terminada, comprueba ambos perfiles de agente, al menos
un cliente, las transiciones de On-hold y los tests de correo, además del recorrido
visual. Conserva los casos fallidos para revisarlos antes de integrar o publicar.
