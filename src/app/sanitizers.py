try:
    import bleach

    _HAS_BLEACH = True
except ImportError:  # pragma: no cover
    bleach = None
    _HAS_BLEACH = False

try:
    from bleach.css_sanitizer import CSSSanitizer

    _HAS_CSS_SANITIZER = True
except ImportError:  # pragma: no cover
    CSSSanitizer = None
    _HAS_CSS_SANITIZER = False


ALLOWED_HTML_TAGS = [
    "a",
    "b",
    "blockquote",
    "br",
    "code",
    "div",
    "em",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "hr",
    "i",
    "img",
    "li",
    "ol",
    "p",
    "pre",
    "s",
    "span",
    "strike",
    "strong",
    "sub",
    "sup",
    "table",
    "tbody",
    "td",
    "tfoot",
    "th",
    "thead",
    "tr",
    "u",
    "ul",
]
ALLOWED_HTML_ATTRS = {
    "*": ["class", "style", "title"],
    "a": ["href", "target", "rel"],
    "img": ["src", "alt", "width", "height"],
    "td": ["colspan", "rowspan"],
    "th": ["colspan", "rowspan"],
}
ALLOWED_HTML_PROTOCOLS = ["http", "https", "mailto", "tel", "data"]
# Allow-list de propiedades CSS para el atributo style. bleach >=5 no sanea CSS
# salvo que se pase un CSSSanitizer; sin esto, el atributo style pasaria tal cual.
ALLOWED_CSS_PROPERTIES = [
    "background", "background-color", "border", "border-bottom", "border-collapse",
    "border-color", "border-left", "border-radius", "border-right", "border-spacing",
    "border-style", "border-top", "border-width", "color", "display", "float",
    "font", "font-family", "font-size", "font-style", "font-weight", "height",
    "letter-spacing", "line-height", "list-style", "list-style-type", "margin",
    "margin-bottom", "margin-left", "margin-right", "margin-top", "max-width",
    "min-width", "padding", "padding-bottom", "padding-left", "padding-right",
    "padding-top", "text-align", "text-decoration", "text-indent", "text-transform",
    "vertical-align", "white-space", "width", "word-break", "word-wrap",
]

_CSS_SANITIZER = (
    CSSSanitizer(allowed_css_properties=ALLOWED_CSS_PROPERTIES)
    if _HAS_CSS_SANITIZER
    else None
)


def sanitize_email_html(html):
    if not html:
        return ""

    import html as htmllib
    import re

    if "<" not in html and "&lt;" in html:
        html = htmllib.unescape(html)
    if not _HAS_BLEACH:
        return htmllib.escape(html)

    clean_kwargs = dict(
        tags=ALLOWED_HTML_TAGS,
        attributes=ALLOWED_HTML_ATTRS,
        protocols=ALLOWED_HTML_PROTOCOLS,
        strip=True,
    )
    if _CSS_SANITIZER is not None:
        clean_kwargs["css_sanitizer"] = _CSS_SANITIZER
    cleaned = bleach.clean(html, **clean_kwargs)
    cleaned = re.sub(
        r'<span\s+class="ql-[^"]*"\s*(?:data-[^\s]*="[^"]*"\s*)*(?:contenteditable="[^"]*"\s*)*>\s*</span>',
        "",
        cleaned,
    )
    cleaned = re.sub(r"<span\s*>\s*</span>", "", cleaned)
    return cleaned
