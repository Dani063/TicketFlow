"""Sanitising and text extraction shared by help imports and the editor."""

import html
import re
from urllib.parse import urlparse

import bleach
from django.utils.html import strip_tags


ALLOWED_TAGS = set(bleach.sanitizer.ALLOWED_TAGS) | {
    "p", "br", "div", "span", "h1", "h2", "h3", "h4", "h5", "hr", "img", "figure", "figcaption",
    "table", "thead", "tbody", "tfoot", "tr", "th", "td", "pre", "code", "blockquote", "iframe", "video", "source",
    "details", "summary", "ol", "ul", "li", "strong", "em", "sup", "sub", "u", "s",
}
ALLOWED_ATTRIBUTES = {
    "a": ["href", "title", "target", "rel"],
    "img": ["src", "alt", "width", "height", "loading"],
    "iframe": ["src", "title", "width", "height", "allow", "allowfullscreen", "loading"],
    "video": ["src", "controls", "width", "height"],
    "source": ["src", "type"],
    "th": ["colspan", "rowspan", "scope"],
    "td": ["colspan", "rowspan"],
    "div": ["class"],
    "span": ["class"],
}
TRUSTED_VIDEO_HOSTS = {
    "www.youtube.com", "youtube.com", "www.youtube-nocookie.com", "player.vimeo.com",
}


def sanitize_help_html(value):
    body = bleach.clean(
        value or "",
        tags=ALLOWED_TAGS,
        attributes=ALLOWED_ATTRIBUTES,
        protocols={"http", "https", "mailto"},
        strip=True,
    )

    def safe_iframe(match):
        tag = match.group(0)
        src_match = re.search(r'src=["\']([^"\']+)', tag, re.I)
        parsed = urlparse(src_match.group(1)) if src_match else None
        host = (parsed.hostname or "").lower() if parsed else ""
        if host not in TRUSTED_VIDEO_HOSTS:
            return ""
        return tag.replace("<iframe", '<iframe loading="lazy"', 1) if "loading=" not in tag else tag

    body = re.sub(r"<iframe\b[^>]*>.*?</iframe>", safe_iframe, body, flags=re.I | re.S)
    return re.sub(r"<img(?![^>]*\bloading=)", '<img loading="lazy"', body, flags=re.I)


def help_plain_text(value):
    return html.unescape(strip_tags(value or "")).strip()
