"""Taxonomías y constantes de dominio compartidas por modelos, servicios y vistas.

Mantener este módulo libre de imports de Django/modelos: las migraciones de datos
copian estos mapas congelados y los servicios los importan sin riesgo de ciclos.
"""

# --- Tipo de ticket (ITIL, compatible con Zendesk) ---------------------------
TICKET_TYPE_QUESTION = "question"
TICKET_TYPE_INCIDENT = "incident"
TICKET_TYPE_PROBLEM = "problem"
TICKET_TYPE_TASK = "task"

TICKET_TYPE_CHOICES = [
    (TICKET_TYPE_QUESTION, "Consulta"),
    (TICKET_TYPE_INCIDENT, "Incidencia"),
    (TICKET_TYPE_PROBLEM, "Problema"),
    (TICKET_TYPE_TASK, "Tarea"),
]
TICKET_TYPE_VALUES = {value for value, _ in TICKET_TYPE_CHOICES}

# Valores históricos del formulario en español (y sinónimos) -> canónico.
LEGACY_TYPE_MAP = {
    "incidencia": TICKET_TYPE_INCIDENT,
    "incident": TICKET_TYPE_INCIDENT,
    "consulta": TICKET_TYPE_QUESTION,
    "pregunta": TICKET_TYPE_QUESTION,
    "question": TICKET_TYPE_QUESTION,
    "mejora": TICKET_TYPE_PROBLEM,  # el select antiguo etiquetaba "Problema" como value="mejora"
    "problema": TICKET_TYPE_PROBLEM,
    "problem": TICKET_TYPE_PROBLEM,
    "tarea": TICKET_TYPE_TASK,
    "task": TICKET_TYPE_TASK,
}

# --- Canal de entrada ---------------------------------------------------------
CHANNEL_CHOICES = [
    ("email", "Email"),
    ("web", "Web"),
    ("phone", "Teléfono"),
    ("api", "API"),
    ("chat", "Chat"),
    ("twitter", "Twitter"),
    ("twitter_dm", "Twitter DM"),
    ("twitter_like", "Twitter Like"),
    ("internal", "Interno"),
]
CHANNEL_VALUES = {value for value, _ in CHANNEL_CHOICES}

LEGACY_CHANNEL_MAP = {
    "telefono": "phone",
    "teléfono": "phone",
    "voice": "phone",
    "mail": "email",
    "email": "email",
    "web": "web",
    "web_form": "web",
    "api": "api",
    "chat": "chat",
    "twitter": "twitter",
    "twitter_dm": "twitter_dm",
    "twitter_like": "twitter_like",
    "internal": "internal",
    "interno": "internal",
}

# --- Servicio -----------------------------------------------------------------
# Ticket.service es CharField libre (arrastra valores de Zendesk), pero la UI
# solo ofrece esta lista. Es la misma del <select id="servicio"> del detalle de
# ticket (`_ticket_pane.html`), traida aqui para que el panel de admin no la
# duplique otra vez. Pendiente: que esa plantilla la consuma desde aqui.
SERVICE_CHOICES = [
    ("ecomfax", "EcomFax"),
    ("ecomfaxpro", "EcomFaxPro"),
    ("recordia", "Recordia"),
    ("aplicateca", "Aplicateca"),
    ("audiolog", "Audiolog"),
    ("cisco_telefonia", "Cisco Telefonia"),
    ("rightfax", "Rightfax"),
    ("internocomuny", "InternoComuny"),
    ("rts", "RTS"),
    ("cognitia", "Cognitia"),
    ("otros", "Otros"),
]
SERVICE_VALUES = {value for value, _ in SERVICE_CHOICES}

# --- Idioma (Ticket.language guarda valores libres heredados de Zendesk) ------
LANGUAGE_CHOICES = [("es", "Español"), ("en", "Inglés")]


def normalize_language(value):
    """'español'/'es-ES'/'es' -> 'es'; 'inglés'/'english'/'en-US' -> 'en'; resto None."""
    raw = (value or "").strip().lower()
    if not raw:
        return None
    if raw.startswith("es") or "espa" in raw:
        return "es"
    if raw.startswith("en") or "ingl" in raw or "engl" in raw:
        return "en"
    return None


# --- SLA -----------------------------------------------------------------------
SLA_AT_RISK_WINDOW_MINUTES = 60

# Escalado de prioridad al incumplir SLA.
PRIORITY_ESCALATION = {
    "low": "normal",
    "normal": "high",
    "high": "urgent",
    None: "high",
    "": "high",
}
