# Plan de mejoras frontend — TicketFlow v2.2.0

## Objetivo

Este documento recoge la auditoría visual y funcional realizada sobre TicketFlow, junto con las decisiones de producto tomadas después de revisarla punto por punto. Su finalidad es conservar el contexto necesario para implementar las mejoras de forma progresiva y verificable.

La aplicación de escritorio es la experiencia principal. La adaptación responsive debe ser aditiva: ninguna mejora para tablet o móvil puede degradar, simplificar indebidamente o romper el comportamiento actual en PC.

## Alcance revisado

La auditoría incluye:

- Dashboard.
- Lista y vistas de tickets.
- Lista y perfiles de clientes.
- Creación y detalle de tickets.
- Reporting.
- Administración: Usuarios, Roles, Grupos, Plantillas y Motivos CSAT.
- Modales y acciones asociadas.
- Perfil del usuario y tickets asociados.
- Búsqueda global y notificaciones.
- Temas claro y oscuro.
- Documentación integrada.
- Ruta antigua de Ajustes.
- Navegación SPA y pestañas.
- Comportamiento en escritorio, tablet y móvil.

## Decisiones e invariantes de producto

Estas decisiones se consideran obligatorias durante toda la implementación:

1. **PC es la plataforma principal.** Antes y después de cada cambio responsive se debe verificar expresamente la experiencia de escritorio.
2. **Los perfiles de cliente continúan abriéndose como pestañas.** Se corregirá la navegación SPA; no se eliminará esta funcionalidad.
3. **La etiqueta lateral de la sección seleccionada se conserva.** Su aparición al seleccionar una sección se considera cómoda y no debe eliminarse.
4. **Los estados de ticket permanecen en inglés.** Se mantienen valores como `open`, `pending`, `resolved` y `closed` en todas las superficies de la interfaz.
5. **El reparto por agente en la vista de tickets no resueltos es inamovible.** La tabla debe repartir sus resultados en partes iguales entre todos los agentes que tengan tickets. La paginación debe avanzar simultáneamente a la siguiente página de resultados de cada agente.
6. **No se realizará la reconversión semántica general propuesta en el punto 17.** Solo se aplicarán las mejoras de foco, etiquetado y accesibilidad aprobadas expresamente en el punto 18.
7. **No se realizará un rebranding.** Se mantienen la identidad, la paleta morada, el modo oscuro, los degradados y la densidad propia de una herramienta operativa.
8. **Versión actual del producto: `2.2.0`.** Debe mostrarse de forma visible en la web y proceder de una única fuente de configuración.

## Estrategia general de validación

Cada bloque implementado debe comprobarse, como mínimo, en:

- Escritorio principal: 1440 × 900.
- Escritorio compacto: 1280 × 720.
- Tablet: aproximadamente 768 × 1024.
- Móvil: aproximadamente 390 × 844.
- Tema claro y tema oscuro.
- Navegación directa y navegación por fragmentos/pestañas.
- Regreso a una ventana ya visitada para comprobar reentrancia y conservación del estado.
- Tablas con datos, sin datos, textos largos y scroll.

Los cambios responsive no se considerarán terminados hasta verificar que la versión de PC mantiene su estructura, densidad, accesos y comportamiento.

---

## 1. Responsive global

**Decisión:** aprobado, con la condición de no romper la versión de PC, que sigue siendo la principal.

### Cambios

- Añadir `meta viewport` a la plantilla principal.
- Crear breakpoints globales para navbar, pestañas y sidebar.
- Evitar solapamientos entre Añadir, pestañas, buscador, notificaciones y perfil.
- Adaptar la sidebar a móvil mediante una solución compacta o desplegable que conserve todos los accesos.
- Evitar contenido recortado por `overflow: hidden` y elementos posicionados fuera del viewport.
- Mantener en escritorio la distribución y densidad operativa actual, salvo las mejoras aprobadas en otros puntos.

### Criterios de aceptación

- Ningún control principal queda oculto o solapado en móvil.
- Todo contenido fuera del ancho visible dispone de una alternativa utilizable.
- La navbar y la sidebar mantienen su comportamiento actual en PC.
- No aparecen scrollbars globales involuntarios.

## 2. Sistema unificado de tablas

**Decisión:** aprobado.

### Cambios

- Definir un patrón común para Dashboard, Tickets, Clientes, perfiles, Reporting y Administración.
- Mantener cabeceras sticky cuando la tabla tenga scroll vertical.
- Encapsular el scroll horizontal dentro de la tabla y evitar scrollbars duplicados.
- Establecer columnas prioritarias y secundarias por pantalla.
- Mantener visibles las acciones importantes; valorar una columna de acciones sticky.
- Truncar textos largos de manera controlada y mostrar el contenido completo mediante tooltip o detalle.
- En móvil, utilizar una tabla resumida o filas tipo card cuando el número de columnas impida una interacción razonable.
- Unificar estilos de paginación, selector de tamaño y textos de registros.

### Criterios de aceptación

- Dashboard deja de necesitar scroll horizontal en escritorio para sus columnas esenciales.
- Las acciones de Administración no quedan escondidas al final de una tabla muy ancha.
- No existe doble scrollbar horizontal en perfiles.
- El mismo patrón visual de paginación se utiliza en todas las listas.

## 3. Navegación SPA y pestañas de perfiles

**Decisión:** aprobado. Los perfiles deben seguir tratándose como pestañas.

### Problema actual

Los perfiles de cliente son identificados como URLs de pestaña, pero el cargador SPA intenta encontrar un elemento `.ticket-pane`. Al no existir en el fragmento del perfil, se produce un error en consola y se termina realizando una recarga completa.

### Cambios

- Extender el sistema de pestañas para soportar tipos de panel distintos: ticket y perfil de cliente.
- No forzar que un perfil utilice la estructura interna de un ticket.
- Mantener caché, activación, cierre y restauración de pestañas para ambos tipos.
- Evitar la recarga completa y eliminar el error `No .ticket-pane in fragment response`.
- Verificar apertura repetida del mismo perfil sin duplicados innecesarios.
- Conservar correctamente el estado de la ventana anterior al cambiar de pestaña.

### Criterios de aceptación

- Un cliente se abre como pestaña sin errores de consola.
- Volver a una pestaña de perfil no recarga toda la aplicación.
- Tickets y perfiles pueden coexistir en la barra de pestañas.
- Cerrar una pestaña activa selecciona correctamente la siguiente ventana disponible.

## 4. Retirada de Ajustes antiguos

**Decisión:** eliminar la ruta/pantalla antigua.

### Cambios

- Eliminar la vista y plantilla vacía de `/settings/`.
- Eliminar referencias, URLs o accesos residuales asociados.
- Comprobar que ninguna funcionalidad real depende de esa ruta.
- Si existe un enlace antiguo guardado, devolver una respuesta coherente o redirigir a Administración/Perfil según corresponda.

### Criterios de aceptación

- No queda una pantalla vacía accesible desde la aplicación.
- No se rompe Administración, Perfil ni el menú de usuario.

## 5. Etiqueta activa de la sidebar

**Decisión:** se conserva. No se considera un problema porque solo aparece al seleccionar la sección y resulta cómoda.

### Actuación

- No eliminar ni sustituir el patrón visual de la etiqueta activa.
- Verificar únicamente que siga apareciendo en el momento previsto y que no quede bloqueando permanentemente una interacción.
- Mantener su estilo dentro de la estética actual.

## 6. Jerarquía común de páginas

**Decisión:** aprobado, con verificación explícita para no romper vistas existentes.

### Cambios

- Definir una escala coherente para título de página, título de sección, etiquetas y datos.
- Unificar gutters, espaciado superior y relación entre encabezado y contenido.
- Tomar como referencia los componentes más modernos de Administración y Reporting sin rediseñar la herramienta.
- Corregir casos donde un encabezado secundario tiene más peso que el título de página.
- Mantener la estructura funcional actual de cada ventana.

### Validación obligatoria

- Comparar capturas antes/después de todas las rutas principales en PC.
- Verificar que tablas, filtros y títulos no cambian de posición de forma inesperada.

## 7. Dashboard

**Decisión:** aprobado. Aplicar y revisar después.

### Cambios

- Dar prioridad visual a las métricas principales.
- Reubicar o ajustar “Actualizaciones recientes” como panel secundario.
- Traducir `YOU` y `GROUPS`; mantener textos consistentes en español salvo estados de ticket.
- Mejorar la señal visual del KPI seleccionado.
- Adaptar la tabla para mostrar columnas esenciales sin scroll horizontal en PC.
- Reducir el vacío visual cuando existen pocos registros.
- Reutilizar el estado vacío compartido si no hay tickets de atención.

### Criterios de aceptación

- Los contadores continúan filtrando la tabla correcta.
- El número mostrado coincide con las filas de su filtro.
- Ordenación, selección múltiple, fusión, eliminación y paginación siguen funcionando.
- La versión PC se verifica después de aplicar los cambios.

## 8. Lista y vistas de tickets

**Decisión:** aprobado, incluyendo un requisito adicional inamovible para tickets no resueltos.

### Cambios generales

- Organizar la larga lista de vistas en grupos comprensibles y permitir búsqueda o plegado.
- Traducir los nombres de vistas heredados que no sean estados de ticket.
- En móvil, ofrecer un control visible para abrir/cerrar los filtros.
- Mostrar claramente qué vista está activa junto a la tabla.
- Reutilizar un estado vacío informativo en lugar de limitarse a “No hay tickets”.
- Unificar filtro de empresa, tabla, orden y paginación con el resto de listas.

### Requisito inamovible: reparto por agente

En la vista de **tickets no resueltos**, la tabla debe dividir sus resultados en partes iguales entre todos los agentes que tengan tickets.

Comportamiento requerido:

- Detectar los agentes que tengan al menos un ticket dentro de esa vista y de los filtros activos.
- Repartir el número de filas de la página de la forma más equitativa posible entre esos agentes.
- Ningún agente con tickets debe quedar excluido de una página por consumir otro agente todas las filas disponibles, salvo que sea matemáticamente imposible por existir más agentes que filas configuradas.
- Al avanzar de página se debe cargar la siguiente página o tramo de resultados **para cada agente**, no continuar únicamente con el conjunto global de uno de ellos.
- Ordenación, filtros y tamaño de página deben mantener este reparto.
- La implementación no debe alterar el comportamiento de otras vistas de tickets.

### Comportamiento confirmado para la implementación

- Se aplica únicamente a la vista **“Todos los tickets no resueltos (sin tareas)”** (`all_unsolved_no_tareas`).
- El conjunto de agentes procede de los miembros activos seleccionados en reglas activas del panel **Administración → Asignación**. Se usa la unión de esos miembros y solo participan quienes tengan tickets tras aplicar la vista y los filtros activos.
- Los usuarios no seleccionados, miembros inactivos, tickets sin asignar, tareas y estados `resolved`/`closed` no forman parte de esta vista concreta.
- El reparto usa un turno rotatorio estable: el resto cambia de agente entre páginas y, si uno agota sus tickets, sus filas se redistribuyen entre los demás.
- Los bloques de agentes se muestran alfabéticamente; dentro de cada bloque se respeta la ordenación activa de la tabla.
- Si hay más agentes con tickets que filas por página, la página siguiente continúa por el siguiente agente del turno para que los primeros no acaparen siempre los huecos.

### Criterios de aceptación

- El reparto equitativo se prueba con varios agentes y cantidades desiguales.
- La página 2 representa el segundo tramo de cada agente.
- Cambiar de página no pierde filtros ni orden.
- Los contadores siguen representando el total real de la vista.

## 9. Clientes

**Decisión:** aprobado, siempre que no se rompa la experiencia de PC.

### Cambios

- Corregir la estructura móvil que actualmente desplaza la tabla fuera del viewport.
- Hacer que buscador y filtros se distribuyan correctamente según el ancho.
- Priorizar nombre y email en móvil; tratar el ID como dato secundario.
- Añadir una señal visual clara de que la fila abre un perfil.
- Usar el sistema unificado de tabla y paginación.
- Mantener en PC la densidad y la cantidad de información actual.

### Validación obligatoria

- Búsqueda, filtros, ordenación, paginación y apertura de perfiles se prueban nuevamente en PC.
- La tabla completa permanece visible y operativa en escritorio.

## 10. Reporting

**Decisión:** aprobado. Comprobar que no se rompa al aplicarlo.

### Cambios

- Aclarar visual y funcionalmente la relación entre presets (`7d`, `30d`, `90d`, `1a`) y rango personalizado.
- Al activar fechas personalizadas, reflejarlo como modo activo; al usar un preset, evitar que las fechas parezcan un segundo filtro simultáneo.
- Reorganizar fechas y exportaciones en móvil.
- Reducir la altura de gráficas vacías y mostrar un estado sin datos con contexto.
- Mantener la altura suficiente cuando sí haya datos relevantes.
- Mejorar truncado y tooltip de asuntos largos.
- Evitar cortes en nombres de agente y columnas finales.
- Mantener los estados de ticket en inglés.

### Criterios de aceptación

- Cambiar de preset actualiza datos y fechas correctamente.
- El rango personalizado funciona sin ambigüedad.
- Exportar CSV/PDF conserva el rango y empresa seleccionados.
- KPIs, gráficas y tablas se verifican con y sin datos.
- La versión PC se compara antes y después.

## 11. Administración

**Decisión:** aprobado.

### Cambios

- Permitir que buscador, selects y botón de creación se redistribuyan sin cortarse.
- Mantener accesibles las acciones de fila.
- Añadir una señal clara de desplazamiento para las pestañas en móvil.
- Ajustar anchos de las tablas de Roles, Grupos, Plantillas y Motivos CSAT.
- Unificar capitalización y nombres visibles de roles/grupos cuando no sean valores técnicos.
- Integrar visualmente los pies de los modales con su contenedor.
- Conservar selección múltiple, acciones masivas y cabecera sticky.

### Criterios de aceptación

- Crear, editar y cerrar cada modal sin cambios de layout inesperados.
- Las acciones por fila siguen disponibles en PC y móvil.
- Las cinco pestañas administrativas pueden abrirse en cualquier ancho.

## 12. Creación y detalle de tickets

**Decisión:** aprobado con excepciones y añadidos.

### Idioma

- Mantener los estados de ticket en inglés: `open`, `pending`, `resolved`, `closed`, etc.
- Traducir el resto de etiquetas cuando no sean nombres contractuales o técnicos que deban conservarse.
- Revisar especialmente `Subject`, `Security related`, `Monitoring` y `Approval status`.

### Cambios de layout e interacción

- Corregir el recorte de Tipo/Prioridad en anchos intermedios.
- En móvil, presentar los metadatos en un panel desplegable y reservar el ancho principal a conversación y compositor.
- Eliminar mínimos de ancho que dejan contenido inaccesible.
- Adaptar la toolbar del editor sin ocultar adjuntos, IA o envío.
- Mantener visible y utilizable la barra de publicación.
- Plegar firmas y texto citado de emails extensos.
- Ocultar correctamente el texto residual `Visit URL: EditRemove` cuando el editor no lo utiliza.
- Mejorar los nombres accesibles de botones de icono y cierre de pestaña.

### Contraste específico en modo oscuro

- Cambiar a blanco el texto/icono de los botones **Asignarme** y **Fusionar**.
- Cambiar a blanco el contenido visible del botón **IA** de la caja de mensajes.
- Revisar los estados hover, focus, active y disabled de estos botones para que sigan siendo distinguibles.

### Criterios de aceptación

- Crear un ticket sigue funcionando con todos los campos actuales.
- Abrir, editar y publicar en tickets existentes mantiene datos y comentarios.
- Cambiar visibilidad público/interno, adjuntar, usar IA, macros, fusionar y asignarse sigue funcionando.
- Los estados continúan en inglés en todos los controles.
- Los botones indicados se leen correctamente en modo oscuro.

## 13. Perfiles

**Decisión:** aprobado.

### Cambios

- Reducir la repetición entre título de página, “Opciones de perfil” e “Información general”.
- Crear una ficha más compacta que aproveche el ancho disponible.
- Titular la pestaña del cliente con su nombre, no solo con su ID.
- Mantener estados de tickets en inglés.
- Representar prioridades y valores ausentes de manera consistente, evitando mostrar `None`.
- Eliminar scrollbars horizontales duplicados en tickets asociados.
- Usar el paginador compartido.
- Mantener el buen estado vacío actual de “Sin tickets asociados”.

## 14. Estados vacíos

**Decisión:** aprobado.

### Cambios

- Extraer como componente compartido el patrón usado en perfiles.
- Aplicarlo a Tickets, Dashboard, Clientes, Reporting, Notificaciones y búsquedas sin resultados.
- Incluir título, explicación breve y acción siguiente solo cuando exista una acción útil.
- Diferenciar entre “sin datos”, “sin resultados por filtros” y “error de carga”.

## 15. Documentación

**Decisión:** aprobado.

### Cambios

- Integrar la documentación con los tokens, tipografía y temas de TicketFlow.
- Mantener una navegación clara de vuelta a la aplicación.
- Incorporar una tabla de contenidos lateral o sticky en escritorio y plegable en móvil.
- Actualizar instrucciones desfasadas sobre creación de tickets/clientes, acciones masivas y campos disponibles.
- Sustituir “Appearance” por “Apariencia”.
- Revisar que todos los atajos documentados coincidan con el comportamiento real.
- Añadir favicon y mostrar la versión de la aplicación.

## 16. Búsqueda global y notificaciones

**Decisión:** aprobado, manteniendo los estados en inglés.

### Cambios

- Mantener `open`, `pending`, `resolved`, `closed`, etc. en los resultados de búsqueda.
- Traducir y homogeneizar el resto de textos de categorías y metadatos.
- Mejorar el estado vacío de notificaciones.
- Corregir contraste y separación en ambos temas.
- Añadir etiquetas claras a botones de cierre y notificaciones.
- Permitir navegación por teclado en resultados sin realizar la reconversión semántica general descartada en el punto 17.

## 17. Reconversión semántica general

**Decisión:** descartado.

No se sustituirán de forma general todos los `div`/`li` interactivos por botones, tabs o enlaces semánticos. Este trabajo queda fuera del alcance aprobado.

Sí se podrán realizar ajustes puntuales imprescindibles para cumplir el punto 18, siempre que no impliquen una reestructuración general ni cambien la interacción visual existente.

## 18. Foco y botones de icono

**Decisión:** aprobado.

### Cambios

- Garantizar foco visible en sidebar y controles donde actualmente se elimina sin alternativa.
- Añadir `aria-label` o nombre accesible a editar, contraseña, eliminar, adjuntar, enviar, notificaciones y cierres.
- Aumentar el área interactiva de iconos demasiado pequeños cuando no afecte a la densidad de PC.
- Mantener estados hover/focus coherentes con cada tema.

### Criterios de aceptación

- Se puede recorrer la navegación y acciones principales con teclado.
- El foco siempre es visible.
- Los botones de icono tienen un nombre comprensible para tecnologías de asistencia.

## 19. Contraste

**Decisión:** aprobado. Incluye expresamente el caso descrito en el punto 12.

### Cambios

- Reforzar divisores y estados de bajo contraste en modo claro.
- Revisar pills verdes, textos secundarios y botones deshabilitados.
- En modo oscuro, hacer blancos los botones Asignarme, Fusionar e IA indicados anteriormente.
- Mantener diferenciación entre estado normal, hover, focus, active y disabled.
- Conservar la paleta actual; el objetivo es legibilidad, no cambiar la identidad visual.

## 20. Consolidación CSS

**Decisión:** aprobado, con la condición de no romper nada.

### Cambios

- Migrar progresivamente Home y Perfiles desde floats, anchos rígidos y estilos inline hacia los tokens/componentes compartidos.
- Definir componentes únicos para:
  - Encabezado de página.
  - Toolbar y filtros.
  - Tabla.
  - Paginación.
  - Modal.
  - Estado vacío.
  - Badge/pill.
- Evitar reescrituras masivas en un único paso.
- Mantener compatibilidad con la navegación por fragmentos y scripts reentrantes.
- Subir los parámetros `?v=` de los assets modificados.

### Estrategia de seguridad

- Migrar una pantalla cada vez.
- Comparar visualmente antes/después en PC.
- Probar regreso a la misma pestaña para detectar listeners duplicados o estilos perdidos.
- No eliminar CSS heredado hasta confirmar que ya no tiene consumidores.

## 21. Versión visible de la aplicación

**Decisión:** aprobado. La versión actual es `2.2.0`.

### Cambios

- Mostrar `TicketFlow v2.2.0` en el pie del menú de perfil, evitando sobrecargar la navbar.
- Mostrar también la versión en Documentación/Ayuda.
- Utilizar una única fuente de configuración para la versión; no duplicarla manualmente en varias plantillas.
- Permitir opcionalmente consultar el identificador de build o commit mediante tooltip o detalle técnico.
- Mantener la versión visible tanto en tema claro como oscuro.

### Criterios de aceptación

- La versión se ve sin abrir herramientas de desarrollo.
- Menú de perfil y documentación muestran exactamente el mismo valor.
- Un cambio futuro de versión requiere modificar un único origen.

---

## Orden recomendado de implementación

### Fase 1 — Base y regresiones críticas

1. Punto 21: fuente única y versión visible.
2. Punto 4: retirada de Ajustes antiguos.
3. Punto 3: pestañas SPA para perfiles.
4. Punto 1: base responsive global, preservando PC.
5. Punto 2: sistema común de tablas y paginación.

### Fase 2 — Flujos operativos principales

1. Punto 8: Tickets, incluyendo el reparto inamovible por agente.
2. Punto 12: creación y detalle de tickets.
3. Punto 7: Dashboard.
4. Punto 9: Clientes.

### Fase 3 — Gestión y análisis

1. Punto 11: Administración.
2. Punto 10: Reporting.
3. Punto 13: Perfiles.
4. Punto 14: estados vacíos.

### Fase 4 — Consistencia y acabado

1. Punto 6: jerarquía común.
2. Punto 15: documentación.
3. Punto 16: búsqueda y notificaciones.
4. Punto 18: foco y botones de icono.
5. Punto 19: contraste.
6. Punto 20: consolidación CSS progresiva.

El punto 5 se conserva como decisión de diseño y el punto 17 queda expresamente descartado.

## Estado de ejecución — 11 de agosto de 2026

La implementación aprobada está completada. El punto 17 permanece fuera de alcance por decisión de producto y el punto 5 conserva deliberadamente el patrón existente.

Evidencias de cierre:

- Matriz visual revisada en 1440 × 900, 1280 × 720, 768 × 1024 y 390 × 844, tanto en tema claro como oscuro.
- Rutas principales verificadas: Dashboard, Tickets, Clientes, perfiles de cliente y usuario, creación/detalle de ticket, Reporting, Administración y Documentación.
- Navegación SPA validada con coexistencia, reactivación y cierre de pestañas de tickets y perfiles, sin duplicados ni errores de consola.
- Vista `all_unsolved_no_tareas` validada con reparto equilibrado por agentes configurados en reglas activas de Asignación, incluyendo segunda página, filtros y ordenación.
- Dashboard validado cruzando KPI y número real de filas; Clientes validado con búsqueda, limpieza de filtros, orden ascendente/descendente y apertura de perfil.
- Reporting validado con presets, rango personalizado y parámetros compartidos por carga y exportación.
- Las seis secciones administrativas y sus modales de creación se han abierto y cerrado en móvil y escritorio.
- La antigua URL `/settings/` conserva únicamente una redirección de compatibilidad: Administración para administradores y Perfil para el resto de usuarios autenticados; la vista y plantilla antiguas siguen eliminadas.
- Validación estática de los scripts modificados y `git diff --check` sin errores.
- Suite Django completa ejecutada con SQLite: **97 tests superados**.

## Checklist de cierre por cambio

- [x] Funciona mediante navegación directa.
- [x] Funciona mediante navegación SPA/fragmentos.
- [x] Funciona al regresar a una pestaña ya abierta.
- [x] Verificado en 1440 × 900.
- [x] Verificado en 1280 × 720.
- [x] Verificado en tablet.
- [x] Verificado en móvil.
- [x] Verificado en modo claro.
- [x] Verificado en modo oscuro.
- [x] No hay errores nuevos en consola.
- [x] No se pierden filtros, orden ni paginación.
- [x] Los assets modificados tienen su versión de caché actualizada.
- [x] Se han ejecutado las comprobaciones/tests proporcionales al cambio.
