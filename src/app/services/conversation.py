"""Presentación reversible del correo; nunca crea ni modifica comentarios.

Los rangos de texto apuntan al árbol HTML para separar cabeceras sin perder
tablas, enlaces o imágenes. Las citas ambiguas conservan su contenido visible.
"""
import html
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser

from django.utils.html import urlize

from app.permissions import is_agent
from app.sanitizers import sanitize_email_html


BLOCKS = set('p div section article header footer blockquote table tr li ul ol pre h1 h2 h3 h4 h5 h6'.split())
VOID = {'br', 'img', 'hr', 'meta', 'link', 'input', 'wbr'}
SKIP = {'script', 'style', 'head', 'iframe', 'object', 'template'}
KEEP = BLOCKS | set('a b strong em i u s strike span code sub sup tbody thead tfoot td th br img hr'.split())
MONTHS = dict(zip('jan feb mar apr may jun jul aug sep oct nov dec'.split(), range(1, 13)))
MONTHS.update(dict(zip('ene feb mar abr may jun jul ago sep oct nov dic'.split(), range(1, 13))))
DATE = re.compile(r'^(\w{3,10})\.?\s+(\d{1,2}),?\s+(\d{4}),?\s+(\d{1,2}):(\d{2})(?::\d{2})?\s*(?:(?:GMT|UTC)([+-]\d{1,2})(?::?(\d{2}))?)?$', re.I)
HEADER = re.compile(r'^(De|From|Enviado|Sent|Fecha|Date|Para|To|Cc|Asunto|Subject):\s*(.*)$', re.I)
REPLY = re.compile(r'^(?:On\s+(.+?)\s+wrote:|El\s+(.+?)\s+escribi[oó]:)$', re.I)
NOTICE = re.compile(r'^(?:Este mensaje y sus adjuntos|The information contained in this transmission|Esta mensagem e seus anexos|This email is a service from|AVISO/WARNING:|##-\s*Please type your reply above this line)', re.I)
SIGNOFF = re.compile(r'^(?:Un saludo[,.]?|Saludos[,.]?|Atentamente[,.]?|Best regards[,.]?|Kind regards[,.]?|Regards[,.]?|--)$', re.I)
CONTACT = re.compile(r'(?:\b(?:email|phone|tel|tlf|soporte|support|avda|avenida|calle)\b|[\w.+-]+@[\w.-]+\.[a-z]{2,}|https?://)', re.I)


def compact(text):
    return re.sub(r'\n[ \t]*\n(?:[ \t]*\n)+', '\n\n', text.replace('\xa0', ' ')).strip()


@dataclass
class Node:
    tag: str = ''
    attrs: list = field(default_factory=list)
    children: list = field(default_factory=list)
    text: str = ''
    start: int = 0
    end: int = 0


class Document(HTMLParser):
    def __init__(self, source):
        super().__init__(convert_charrefs=True)
        self.root = Node('root')
        self.stack = [self.root]
        self.skipping = []
        self.nodes = []
        self.feed(source)
        self.close()
        self.parts = []
        self.position = 0
        self._index(self.root)
        self.text = ''.join(self.parts)

    def handle_starttag(self, tag, attrs):
        if self.skipping:
            if tag in SKIP:
                self.skipping.append(tag)
            return
        if tag in SKIP:
            self.skipping.append(tag)
            return
        if len(self.stack) > 100 or len(self.nodes) > 30000:
            raise ValueError('HTML demasiado complejo')
        # HTML de correo con párrafos sin cierre explícito.
        if tag in {'p', 'li', 'tr', 'td', 'th'} and self.stack[-1].tag == tag:
            self.stack.pop()
        node = Node(tag, attrs)
        self.stack[-1].children.append(node)
        self.nodes.append(node)
        if tag not in VOID:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in VOID:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        if self.skipping:
            if tag == self.skipping[-1]:
                self.skipping.pop()
            return
        for i in range(len(self.stack) - 1, 0, -1):
            if self.stack[i].tag == tag:
                del self.stack[i:]
                break

    def handle_data(self, data):
        if not self.skipping:
            self.stack[-1].children.append(Node(text=data))

    def _add(self, value):
        self.parts.append(value)
        self.position += len(value)

    def _index(self, node, pre=False):
        if node.tag in BLOCKS:
            self._add('\n')
        node.start = self.position
        if not node.tag:
            node.text = node.text.replace('\xa0', ' ')
            if not pre:
                node.text = re.sub(r'\s+', ' ', node.text)
            self._add(node.text)
        elif node.tag in {'br', 'hr'}:
            self._add('\n')
        elif node.tag == 'img':
            self._add('\ufffc')
        else:
            for child in node.children:
                self._index(child, pre or node.tag == 'pre')
        if node.tag in {'td', 'th'}:
            self._add('\t')
        node.end = self.position
        if node.tag in BLOCKS:
            self._add('\n')

    def render(self, start=0, end=None):
        end = len(self.text) if end is None else end

        def walk(node):
            if node.end <= start or node.start >= end:
                return ''
            if not node.tag:
                return html.escape(node.text[max(0, start-node.start):end-node.start])
            body = ''.join(walk(child) for child in node.children)
            tag = node.tag
            if tag not in KEEP:
                return body
            if tag not in VOID | {'td', 'th'} and not re.sub(r'<[^>]+>|\s|&nbsp;', '', body) and '<img' not in body:
                return ''
            # Solo semántica: las dimensiones y fuentes del correo no gobiernan la UI.
            attrs = ''
            allowed = {'a': {'href', 'title'}, 'img': {'src', 'alt', 'title'},
                       'td': {'colspan', 'rowspan'}, 'th': {'colspan', 'rowspan'}}.get(tag, set())
            for key, value in node.attrs:
                if key in allowed and value:
                    attrs += f' {key}="{html.escape(value, quote=True)}"'
            if tag == 'a':
                attrs += ' rel="noopener noreferrer"'
            return f'<{tag}{attrs}>' + ('' if tag in VOID else body + f'</{tag}>')

        result = walk(self.root)
        result = re.sub(r'(?:<br>\s*){3,}', '<br><br>', result)
        result = re.sub(r'^(?:\s|<br>)+|(?:\s|<br>)+$', '', result)
        return sanitize_email_html(result)


def parse_date(value):
    value = value.strip()
    try:
        rfc_date = parsedate_to_datetime(value)
        if rfc_date.tzinfo:
            return rfc_date
    except (ValueError, TypeError, IndexError):
        pass
    value = re.sub(r'^(?:lunes|martes|mi[eé]rcoles|jueves|viernes|s[aá]bado|domingo|monday|tuesday|wednesday|thursday|friday|saturday|sunday),?\s+', '', value, flags=re.I)
    day_first = re.match(r'^(\d{1,2})\s+(?:de\s+)?([\wáé]+)\s+(?:de\s+)?(\d{4})\s+(?:a las\s+)?(.+)$', value, re.I)
    if day_first:
        day, month, year, time = day_first.groups()
        value = f'{month} {day}, {year}, {time}'
    match = DATE.match(value)
    if not match:
        return None
    month, day, year, hour, minute, offset, offset_min = match.groups()
    try:
        tz = timezone(timedelta(minutes=(int(offset) * 60 + (-1 if offset.startswith('-') else 1) * int(offset_min or 0)))) if offset else None
        return datetime(int(year), MONTHS[month[:3].lower()], int(day), int(hour), int(minute), tzinfo=tz)
    except (KeyError, ValueError):
        return None


def _lines(document, start=0, end=None):
    end = len(document.text) if end is None else end
    return [(start + m.start(), start + m.end(), m.group().strip().lstrip('> ').strip())
            for m in re.finditer(r'[^\n]+', document.text[start:end]) if m.group().strip()]


def _headers(document):
    lines = _lines(document)
    headers = []
    protected = [(n.start, n.end) for n in document.nodes if n.tag in {'pre', 'code'}]
    for i, (start, end, line) in enumerate(lines):
        if any(a <= start < b for a, b in protected):
            continue
        date = parse_date(line)
        if date and i:
            author_start, _, author = lines[i - 1]
            if len(author) <= 160 and not HEADER.match(author) and not any(c in author for c in '?<>'):
                headers.append(dict(start=author_start, body_start=end, author=author,
                                    date=line, parsed_date=date, kind='zendesk'))
                continue
        match = HEADER.match(line)
        if match and match[1].lower() in {'de', 'from'}:
            following = []
            for next_line in lines[i+1:i+13]:
                header = HEADER.match(next_line[2])
                if not header:
                    break
                following.append((next_line, header))
            dates = [h[2] for _, h in following if h[1].lower() in {'enviado', 'sent', 'fecha', 'date'}]
            if dates and any(h[1].lower() in {'para', 'to', 'cc', 'asunto', 'subject'} for _, h in following):
                headers.append(dict(start=start, body_start=following[-1][0][1], author=match[2],
                                    date=dates[0], parsed_date=parse_date(dates[0]), kind='outlook'))
        reply = REPLY.match(line)
        if reply:
            # Cabecera completa visible: no inventamos autor ni fecha si son ambiguos.
            sender = re.search(r'[\w.+-]+@[\w.-]+\.[\w-]+', line)
            headers.append(dict(start=start, body_start=end, author=sender.group() if sender else '', date='', parsed_date=None,
                                label=line, kind='reply'))
    # Una fecha aislada también puede formar parte de una explicación técnica.
    if sum(h['kind'] == 'zendesk' for h in headers) < 2 and '##- Please type your reply above this line -##' not in document.text and 'Delivered by Zendesk' not in document.text:
        headers = [h for h in headers if h['kind'] != 'zendesk']
    # Respuestas debajo de citas HTML: conservarlas visibles sin atribuirlas al citado.
    for node in document.nodes:
        if node.tag == 'blockquote' and document.text[node.end:].strip() and any(node.start <= h['start'] < node.end for h in headers):
            return []
    # Cada cabecera solo delimita una vez, incluso con marcado anidado.
    return sorted({h['start']: h for h in headers}.values(), key=lambda h: h['start'])


def _segment(document, start, end):
    lines = _lines(document, start, end)
    cut = end
    for pos, _, line in lines:
        if NOTICE.match(line) and pos > start:
            cut = pos
            break
    for i, (pos, line_end, line) in enumerate(lines):
        tail = document.text[line_end:cut]
        substantive_tail = any(re.search(r'[?¿!¡]|\b(?:además|por cierto|necesitamos|revisad|revisar|please|however|P\.D\.)\b', t, re.I)
                               for t in tail.splitlines() if not CONTACT.search(t))
        if SIGNOFF.match(line) and pos > start and len(tail) < 2200 and len(CONTACT.findall(tail)) >= 2 and not substantive_tail:
            cut = line_end
            break
        # Firma de equipo sin despedida (p. ej. nombre + cargo + teléfono + email).
        role_line = re.search(r'\b(?:Support Team|Technical Support|Customer Support|Soporte Especialista|Soporte Técnico|Servicio (?:de )?Voz)\b', line, re.I)
        if role_line and i > 0 and pos < cut and len(tail) < 1800 and len(CONTACT.findall(tail)) >= 2 and not substantive_tail:
            previous_start, _, previous = lines[i-1]
            if previous_start > start and len(previous) <= 70 and re.fullmatch(r'[\w .\-]+', previous):
                cut = previous_start
                break
    return dict(html=document.render(start, cut), text=compact(document.text[start:cut]),
                footer_html=document.render(cut, end) if cut < end else '')


def present_comment(comment):
    raw_html = getattr(comment, 'html_body', '') or ''
    content = getattr(comment, 'content', '') or ''
    # Hay importaciones antiguas que guardaron HTML en content.
    if not raw_html and re.search(r'<(?:p|div|br|table|blockquote)\b', content, re.I):
        raw_html = content
    source = raw_html or urlize(content, nofollow=True, autoescape=True).replace('\n', '<br>')
    result = dict(history=[], extras_html='', duplicate_count=0,
                  original_html=sanitize_email_html(source), html='', footer_html='', text='')
    try:
        if len(source) > 1000000:
            raise ValueError('Correo demasiado grande para separar')
        document = Document(source)
        headers = _headers(document)
        first = headers[0]['start'] if headers else len(document.text)
        # Gmail/Apple: solo una cita final. Si hay respuestas intercaladas, se ven completas.
        if not headers and getattr(comment, 'via_channel', '') == 'email':
            quotes = [n for n in document.nodes if n.tag == 'blockquote' or
                      any(k == 'class' and 'gmail_quote' in (v or '').split() for k, v in n.attrs)]
            if quotes:
                quote = quotes[0]
                if quote.start > 0 and not document.text[quote.end:].strip():
                    first = quote.start
                    result['history'].append(dict(author='', date='', label='Mensaje anterior',
                        parsed_date=None, **_segment(document, quote.start, quote.end)))
        result.update(_segment(document, 0, first))
        if not result['html'] and not result['history'] and not headers:
            result['html'] = document.render()
        for i, header in enumerate(headers):
            end = headers[i+1]['start'] if i+1 < len(headers) else len(document.text)
            # La notificación de Zendesk envuelve su propia lista de comentarios.
            # Sus avisos se conservan desplegables, no como una intervención ficticia.
            body_text = document.text[header['body_start']:end]
            if header['kind'] == 'outlook' and i+1 < len(headers) and headers[i+1]['kind'] == 'zendesk' and (
                    '##- Please type your reply above this line -##' in body_text):
                result['extras_html'] += document.render(header['start'], end)
                continue
            segment = _segment(document, header['body_start'], end)
            if segment['html'] or segment['footer_html']:
                result['history'].append(dict(**header, **segment))
        if result['history'] and all(h.get('parsed_date') for h in result['history']):
            result['history'].sort(key=lambda h: (h['parsed_date'].astimezone(timezone.utc) if h['parsed_date'].tzinfo else h['parsed_date']).replace(tzinfo=None))
    except (ValueError, RecursionError):
        # Nunca truncar ni ocultar el cuerpo al no poder reconocerlo.
        result.update(html=result['original_html'], text=content, history=[])
    return result


def _identity(author):
    return re.sub(r'\s+', ' ', author).strip().casefold()


def _key(author, date, text, body_html=''):
    if not date or not date.tzinfo or not author or not text:
        return None
    media = tuple(re.findall(r'(?:src|href)="([^"]+)"', body_html))
    return (_identity(author), date.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M'),
            re.sub(r'\s+', ' ', text).strip(), media)


def prepare_comments(comments):
    """Solo recibe comentarios que el usuario tiene permiso para ver."""
    known = set()
    for comment in comments:
        comment.conversation = present_comment(comment)
        comment.speaker_kind = 'internal' if not comment.is_public else ('agent' if is_agent(comment.user) else 'customer')
        if comment.is_public:
            for author in (comment.user.name, comment.user.email):
                key = _key(author or '', comment.created_at, comment.conversation['text'], comment.conversation['html'])
                if key:
                    known.add(key)
    seen = set()
    for comment in comments:
        visible = []
        for message in comment.conversation['history']:
            key = _key(message.get('author', ''), message.get('parsed_date'), message['text'], message['html'])
            # Una cita privada nunca elimina una pública ni viceversa.
            scoped_key = (comment.is_public, key)
            if key and ((comment.is_public and key in known) or scoped_key in seen):
                comment.conversation['duplicate_count'] += 1
                continue
            if key:
                seen.add(scoped_key)
            visible.append(message)
        comment.conversation['history'] = visible
    return comments
