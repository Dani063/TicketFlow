# Remitentes no procesables en correo entrante

La ingesta valida la dirección y el nombre antes de crear un usuario. Los límites se leen del modelo `User` (actualmente 255 caracteres en ambos campos); la dirección también pasa los validadores de `EmailField`.

Si el remitente no es procesable:

- Se conserva el mensaje original en `InboundEmailLog.payload`, sin truncar ni sustituir su dirección o nombre.
- El registro queda con `status=failed`, `result=reject_invalid_sender`, el motivo en `error` y la fecha del intento en `processed_at`.
- No se crea usuario ni ticket, no se descargan adjuntos y no se marca el mensaje como leído en M365.
- El cursor puede continuar con los mensajes siguientes una vez guardado el rechazo. Los fallos de conexión, descarga o escritura siguen conservando el cursor para reintentar.

Los motivos son `sender_email_too_long`, `sender_email_invalid`, `sender_name_too_long` y `sender_name_invalid`. Los avisos y métricas incluyen el motivo y los identificadores del registro/marca, no los datos del remitente.

## Revisión y recuperación

Consultar los registros con `status=failed` y `result=reject_invalid_sender` en la base del entorno. El payload contiene datos de correo y solo debe revisarse con el acceso autorizado; no copiarlo a registros de diagnóstico ni a tarjetas públicas.

El mensaje sigue en el buzón. Revisar y corregir el origen o los datos con el responsable antes de solicitar un reprocesado. Un remitente corregido puede volver a pasar por `EmailIngestionService.process_message` con la misma marca e identificador: actualiza el registro existente y no necesita borrarlo. Este cambio no añade una tarea automática de reprocesado ni avanza cursores manualmente.

Los mensajes sin remitente mantienen el resultado previo `skip_no_sender`. No se amplían columnas ni se necesitan nuevas migraciones. La corrección entra en funcionamiento al publicar y desplegar una imagen que incluya el código.
