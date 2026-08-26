"""Logger de gunicorn que emite en JSON, alineado con el LOGGING de Django.

Gunicorn no usa la configuración `LOGGING` de Django: monta sus propios handlers
sobre los loggers `gunicorn.error` y `gunicorn.access`, y les pone
`propagate = False`, así que nunca llegan al logger raíz. Sin esto, el access log
sale como texto plano y el colector del clúster no puede filtrarlo por nivel.

Esta clase hace dos cosas: cambia el formatter de gunicorn por el nuestro, y
convierte la línea de access log en campos estructurados (`status`, `path`,
`duration_ms`…) en vez de una cadena preformateada.

Se activa con `--logger-class TicketFlow.gunicorn_logging.JsonLogger`.

Ojo: gunicorn importa este módulo en el proceso maestro, antes de que exista
configuración de Django. No debe importar `django.conf.settings` — eso
dispararía las lecturas de SSM en el maestro además de en cada worker.
"""

import os

from gunicorn import glogging

from TicketFlow.logging_formatters import JsonFormatter


# Los probes de Kubernetes (exec + curl a localhost) y los health checks del ALB
# golpean /livez y /readyz cada pocos segundos y son la mayor parte del volumen
# del access log. Se silencian; los fallos siguen viendose en los propios probes.
_RUTAS_SILENCIADAS = tuple(
    ruta.strip()
    for ruta in os.getenv("ACCESS_LOG_SKIP_PATHS", "/livez,/readyz").split(",")
    if ruta.strip()
)


def _formato_json_activo():
    """Replica el criterio de `LOG_FORMAT` de settings.py leyendo el entorno."""
    formato = os.getenv("LOG_FORMAT", "").lower()
    if formato in ("json", "text"):
        return formato == "json"
    debug = os.getenv("DEBUG", "False").lower() in ("1", "true", "yes", "on")
    return not debug


def _entero(valor):
    """El status y el tamaño llegan como cadena; en JSON van mejor numéricos."""
    try:
        return int(valor)
    except (TypeError, ValueError):
        return valor


class JsonLogger(glogging.Logger):
    """Logger de gunicorn con salida JSON y access log estructurado."""

    def setup(self, cfg):
        super().setup(cfg)
        if not _formato_json_activo():
            return
        try:
            formatter = JsonFormatter()
            for handler in list(self.error_log.handlers) + list(self.access_log.handlers):
                handler.setFormatter(formatter)
        except Exception:
            # Un problema de formato de log no debe impedir el arranque del pod:
            # se queda con el formato por defecto de gunicorn y sigue.
            self.error_log.exception("No se pudo aplicar el formatter JSON a gunicorn")

    def access(self, resp, req, environ, request_time):
        if not self.cfg.accesslog:
            return
        if environ.get('PATH_INFO') in _RUTAS_SILENCIADAS:
            return
        if not _formato_json_activo():
            super().access(resp, req, environ, request_time)
            return
        try:
            atoms = self.atoms(resp, req, environ, request_time)
            duracion_ms = request_time.seconds * 1000 + request_time.microseconds / 1000
            self.access_log.info("request", extra={
                'remote_ip': atoms.get('h'),
                'method': atoms.get('m'),
                'path': atoms.get('U'),
                'query_string': atoms.get('q'),
                'protocol': atoms.get('H'),
                'status': _entero(atoms.get('s')),
                'response_length': _entero(atoms.get('B')),
                'duration_ms': round(duracion_ms, 2),
                'referer': atoms.get('f'),
                'user_agent': atoms.get('a'),
            })
        except Exception:
            # Si algo falla montando el JSON, no perdemos la línea de access log.
            super().access(resp, req, environ, request_time)
