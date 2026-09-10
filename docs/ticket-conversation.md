# Lectura de conversaciones

`app.services.conversation.prepare_comments` prepara los comentarios autorizados
antes de renderizar el detalle, tanto en página completa como en fragmentos SPA.
La respuesta de `add_comment` usa la misma plantilla `_conversation_body.html`.
Se aplica a todos los registros existentes y nuevos; no requiere migración ni
reescritura del histórico. El HTML del primer correo también se conserva al crear
un ticket por ingesta.

La presentación mantiene la respuesta actual visible y ofrece desplegables para
el historial citado, firmas, cabeceras y original saneado. Reconoce bloques de
Zendesk, cabeceras Outlook en español/inglés y delimitadores Gmail/Apple. Las
intervenciones con fechas reconocidas se ordenan cronológicamente. Un nombre
ambiguo permanece sin identificar, junto a su cabecera completa. Las respuestas
intercaladas en citas HTML y el código permanecen visibles.

Los rangos de texto se proyectan sobre el árbol HTML para mantener enlaces,
tablas, celdas vacías, imágenes y énfasis. Se eliminan fuentes, dimensiones y
espaciado heredados del correo. Los enlaces de texto plano siguen siendo activos.
El HTML se sanea también al leer registros históricos.

La deduplicación solo afecta a citas de la vista: requiere autor, minuto con zona
horaria, texto completo y enlaces/imágenes iguales. Nunca elimina comentarios.
Solo utiliza registros visibles; una nota privada no modifica la presentación
pública. Los datos originales, notificaciones, métricas y auditoría no cambian.

Si el formato no se reconoce con suficiente certeza, se conserva el contenido.
El original siempre está disponible. Los controles usan `details/summary`, con
soporte de teclado y sin listeners adicionales al reabrir pestañas. La vista
inicial se coloca en el comienzo del último comentario, evitando aterrizar en
firmas o controles al final de un mensaje largo.

Validación: `python src/main.py test app.tests_conversation` con SQLite y AWS/IA/
correo saliente desactivados. Incluye carga de tickets históricos, fragmentos,
publicación, privacidad, ingesta inicial, saneado, deduplicación y conservación
del contenido. El ejemplo recibido del ticket 140114 se verificó aparte, sin
versionar datos del cliente: una respuesta actual y nueve intervenciones citadas.
