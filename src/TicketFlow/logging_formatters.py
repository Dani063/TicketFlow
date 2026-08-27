"""Formatters de logging para TicketFlow.

`JsonFormatter` emite una línea JSON por registro, con el nivel, el timestamp y
el módulo como campos independientes. Es lo que permite al colector de logs del
clúster (Grafana Alloy) filtrar por severidad sin tener que parsear texto libre:
en Grafana se consulta `level="ERROR"` en vez de buscar la subcadena "ERROR".

En local se usa el formatter de texto (ver `LOG_FORMAT` en `settings.py`), que
es legible por humanos.
"""

import datetime
import json
import logging

# Atributos que `logging` pone de serie en cada LogRecord. Todo lo que aparezca
# en `record.__dict__` y no esté aquí viene de un `extra={...}` de quien llama,
# y se incluye tal cual en el JSON — así se pueden añadir campos estructurados
# sin tocar este fichero:  logger.info("ticket creado", extra={"ticket_id": 42})
_ATRIBUTOS_ESTANDAR = frozenset((
    'args', 'asctime', 'created', 'exc_info', 'exc_text', 'filename',
    'funcName', 'levelname', 'levelno', 'lineno', 'message', 'module',
    'msecs', 'msg', 'name', 'pathname', 'process', 'processName',
    'relativeCreated', 'stack_info', 'taskName', 'thread', 'threadName',
))


class JsonFormatter(logging.Formatter):
    """Serializa cada registro de log como una única línea JSON."""

    def format(self, record):
        marca_tiempo = datetime.datetime.fromtimestamp(
            record.created, datetime.timezone.utc
        ).isoformat(timespec='milliseconds').replace('+00:00', 'Z')

        payload = {
            'timestamp': marca_tiempo,
            'level': record.levelname,
            'logger': record.name,
            'message': record.getMessage(),
            'module': record.module,
            'line': record.lineno,
            'process': record.process,
        }

        if record.exc_info:
            payload['exception'] = self.formatException(record.exc_info)
        if record.stack_info:
            payload['stack'] = self.formatStack(record.stack_info)

        for clave, valor in record.__dict__.items():
            if clave not in _ATRIBUTOS_ESTANDAR and not clave.startswith('_'):
                payload[clave] = valor

        # default=str para que un `extra` no serializable no tumbe el log.
        return json.dumps(payload, default=str, ensure_ascii=False)
