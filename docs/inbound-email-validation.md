# Validación de remitentes en la ingesta

DEV y STA comparten con PRO el barrido de `mailbox_sweep.py`, la comparación
exacta de identificadores de Graph y las migraciones 0065–0067. Un remitente
con correo inválido se registra como `skip_invalid_sender`, sin bloquear al
resto. Otros errores pasan por los reintentos y la cuarentena del barrido.

La ingesta delta anterior y sus pruebas se sustituyen por `tests_inbound_sweep`.
Las copias se validan antes de crear usuarios y se comprueban en `tests_email_cc`.
