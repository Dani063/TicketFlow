# Instrucciones para agentes: pruebas

Ejecuta los tests **desde la raíz del repositorio** con `scripts/run_tests.py`.
Este ejecutor usa `TicketFlow.settings_test`: SQLite, sin acceso a AWS/RDS y
contraseñas de prueba con hash rápido. Mantiene las migraciones y todos los
tests; no modifica la configuración de producción.

Durante el desarrollo, ejecuta los tests relacionados con el cambio:

```bash
python scripts/run_tests.py app.tests_ticket_views
python scripts/run_tests.py app.tests.NombreDeLaClase.test_nombre
```

Antes de dar por terminado un cambio de código o integrarlo, ejecuta la suite
completa y comprueba su resultado final:

```bash
python scripts/run_tests.py app
```

Para investigar lentitud, añade `--timing --durations 20`. Los logs contienen
errores provocados deliberadamente por algunos tests; usa el código de salida
y el `OK` o `FAILED` final como resultado. No sustituyas la suite completa por
los tests focalizados al cerrar el trabajo.
