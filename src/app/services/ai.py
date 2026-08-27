"""Capa de proveedor de IA (Azure OpenAI).

Único módulo que importa el SDK `openai`. Si en el futuro cambia el proveedor,
solo cambia este fichero. El próximo trimestre añadirá embed() para la búsqueda
de casos similares.
"""

import json
import logging
import time

from django.conf import settings

logger = logging.getLogger(__name__)


class AIError(Exception):
    """Error de la capa de IA."""


class AIRetryableError(AIError):
    """Timeouts, rate limits, 5xx, respuestas malformadas — merece reintento."""


class AIPermanentError(AIError):
    """Credenciales/deployment inválidos, content filter — no reintentar."""


def _use_azure():
    """Azure OpenAI si hay endpoint; si no, OpenAI directo con OPENAI_API_KEY."""
    return bool(settings.AZURE_OPENAI_ENDPOINT and settings.AZURE_OPENAI_API_KEY)


def is_configured():
    return _use_azure() or bool(getattr(settings, "OPENAI_API_KEY", ""))


def _model_name():
    """El identificador de modelo que espera cada proveedor: en Azure es el nombre
    del *deployment*; en OpenAI directo es el nombre del modelo."""
    return settings.AZURE_OPENAI_DEPLOYMENT if _use_azure() else settings.OPENAI_MODEL


def _embedding_model():
    if _use_azure():
        return settings.AZURE_OPENAI_EMBEDDING_DEPLOYMENT
    return settings.OPENAI_EMBEDDING_MODEL


def embed(texts):
    """Embeddings de una lista de textos. Devuelve (vectors, model_name).

    Misma capa de proveedor que chat_json (Azure OpenAI u OpenAI directo). Los
    errores se mapean a AIRetryableError/AIPermanentError igual que el chat.
    """
    if isinstance(texts, str):
        texts = [texts]
    model = _embedding_model()
    if not model:
        raise AIPermanentError("No hay modelo/deployment de embeddings configurado")
    client = _get_client()
    try:
        resp = client.embeddings.create(model=model, input=texts)
    except Exception as exc:
        raise _map_exception(exc) from exc
    vectors = [item.embedding for item in resp.data]
    return vectors, getattr(resp, "model", model)


def _get_client():
    # Import perezoso: el paquete openai solo es necesario con la feature activa.
    if _use_azure():
        from openai import AzureOpenAI

        return AzureOpenAI(
            azure_endpoint=settings.AZURE_OPENAI_ENDPOINT,
            api_key=settings.AZURE_OPENAI_API_KEY,
            api_version=settings.AZURE_OPENAI_API_VERSION,
            timeout=settings.AI_REQUEST_TIMEOUT_SECONDS,
            max_retries=0,  # Celery gobierna los reintentos; no apilar los del SDK
        )

    from openai import OpenAI

    return OpenAI(
        api_key=settings.OPENAI_API_KEY,
        timeout=settings.AI_REQUEST_TIMEOUT_SECONDS,
        max_retries=0,
    )


def _map_exception(exc):
    import openai

    if isinstance(exc, (openai.APITimeoutError, openai.APIConnectionError,
                        openai.RateLimitError, openai.InternalServerError)):
        return AIRetryableError(str(exc))
    if isinstance(exc, (openai.AuthenticationError, openai.PermissionDeniedError,
                        openai.NotFoundError, openai.BadRequestError)):
        return AIPermanentError(str(exc))
    return AIRetryableError(str(exc))


def chat_json(messages, json_schema=None, temperature=0.0, max_tokens=600):
    """Llamada de chat con salida JSON. Devuelve (dict, meta).

    meta = {model, input_tokens, output_tokens, latency_ms}.
    Intenta response_format=json_schema (estricto); si el deployment/api-version
    no lo soporta, el 400 hace caer a json_object (estrictamente más compatible).
    El caller DEBE validar los enums igualmente — nunca confiar en el wire.
    """
    import openai

    client = _get_client()
    model = _model_name()
    started = time.monotonic()

    def _call(response_format):
        return client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            response_format=response_format,
        )

    try:
        if json_schema is not None:
            try:
                response = _call({"type": "json_schema", "json_schema": json_schema})
            except openai.BadRequestError:
                # Cualquier 400 sobre la petición json_schema (deployment/api-version
                # sin soporte estricto) cae a json_object, que cualquier modelo de
                # chat acepta. El mensaje exacto de Azure es inestable, así que no
                # filtramos por substring: si json_object también falla con 400, se
                # propaga abajo y se mapea a AIPermanentError.
                response = _call({"type": "json_object"})
        else:
            response = _call({"type": "json_object"})
    except Exception as exc:
        raise _map_exception(exc) from exc

    latency_ms = int((time.monotonic() - started) * 1000)
    choice = response.choices[0]
    finish_reason = getattr(choice, "finish_reason", None)
    if finish_reason == "length":
        # Respuesta truncada por max_tokens -> JSON inválido garantizado. A
        # temperature=0 reintentar produce el mismo corte, así que es permanente.
        raise AIPermanentError(
            f"Respuesta truncada (finish_reason=length, max_tokens={max_tokens})"
        )
    content = (choice.message.content or "").strip()
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError as exc:
        # Flake del modelo: un reintento suele bastar
        raise AIRetryableError(f"Respuesta no-JSON del modelo: {content[:200]}") from exc

    usage = getattr(response, "usage", None)
    meta = {
        "model": getattr(response, "model", model),
        "input_tokens": getattr(usage, "prompt_tokens", None) if usage else None,
        "output_tokens": getattr(usage, "completion_tokens", None) if usage else None,
        "latency_ms": latency_ms,
    }
    return parsed, meta
