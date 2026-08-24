"""Renderizado del informe PDF de tickets filtrados."""

from io import BytesIO
import os
from xml.sax.saxutils import escape

from django.utils import timezone
import reportlab
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT, TA_RIGHT
from reportlab.lib.fonts import addMapping
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    KeepTogether,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)


_INK = colors.HexColor('#1e293b')
_MUTED = colors.HexColor('#64748b')
_PRIMARY = colors.HexColor('#5b67ca')
_SURFACE = colors.HexColor('#f8fafc')
_LINE = colors.HexColor('#dbe3ef')
_STATUS_COLORS = {
    'open': colors.HexColor('#5b67ca'),
    'pending': colors.HexColor('#c47b24'),
    'resolved': colors.HexColor('#27845b'),
    'closed': colors.HexColor('#526070'),
}

_FONT = 'TicketFlowVera'
_FONT_BOLD = 'TicketFlowVeraBold'
_FONT_DIR = os.path.join(os.path.dirname(reportlab.__file__), 'fonts')
pdfmetrics.registerFont(TTFont(_FONT, os.path.join(_FONT_DIR, 'Vera.ttf')))
pdfmetrics.registerFont(TTFont(_FONT_BOLD, os.path.join(_FONT_DIR, 'VeraBd.ttf')))
addMapping(_FONT, 0, 0, _FONT)
addMapping(_FONT, 0, 1, _FONT_BOLD)


def _text(value):
    return escape(str(value or ''))


def _local_datetime(value):
    if not value:
        return '-'
    return timezone.localtime(value).strftime('%d/%m/%Y %H:%M')


def _page_chrome(canvas, doc):
    canvas.saveState()
    width, height = landscape(A4)
    canvas.setFillColor(_PRIMARY)
    canvas.rect(0, height - 8 * mm, width, 8 * mm, stroke=0, fill=1)
    canvas.setFillColor(_MUTED)
    canvas.setFont(_FONT, 8)
    canvas.drawString(14 * mm, 8 * mm, 'TicketFlow · Exportación de tickets')
    canvas.drawRightString(width - 14 * mm, 8 * mm, f'Página {doc.page}')
    canvas.restoreState()


def build_tickets_pdf(tickets, metadata):
    """Construye un PDF apaisado con una ficha legible por ticket."""
    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=landscape(A4),
        leftMargin=14 * mm,
        rightMargin=14 * mm,
        topMargin=17 * mm,
        bottomMargin=15 * mm,
        title=metadata['title'],
        author='TicketFlow',
        subject='Tickets resultantes de los filtros aplicados',
    )
    sample = getSampleStyleSheet()
    styles = {
        'title': ParagraphStyle(
            'TicketTitle', parent=sample['Title'], fontName=_FONT_BOLD,
            fontSize=20, leading=23, textColor=_INK, alignment=TA_LEFT,
            spaceAfter=4 * mm,
        ),
        'eyebrow': ParagraphStyle(
            'TicketEyebrow', parent=sample['Normal'], fontName=_FONT_BOLD,
            fontSize=7.5, leading=9, textColor=_PRIMARY, spaceAfter=1.5 * mm,
        ),
        'meta_label': ParagraphStyle(
            'MetaLabel', parent=sample['Normal'], fontName=_FONT_BOLD,
            fontSize=7.5, leading=9, textColor=_MUTED,
        ),
        'meta_value': ParagraphStyle(
            'MetaValue', parent=sample['Normal'], fontName=_FONT,
            fontSize=9, leading=11, textColor=_INK,
        ),
        'ticket_id': ParagraphStyle(
            'TicketId', parent=sample['Normal'], fontName=_FONT_BOLD,
            fontSize=10, leading=12, textColor=_INK,
        ),
        'subject': ParagraphStyle(
            'TicketSubject', parent=sample['Normal'], fontName=_FONT_BOLD,
            fontSize=10, leading=13, textColor=_INK, spaceAfter=2 * mm,
        ),
        'field_label': ParagraphStyle(
            'FieldLabel', parent=sample['Normal'], fontName=_FONT_BOLD,
            fontSize=6.8, leading=8, textColor=_MUTED,
        ),
        'field_value': ParagraphStyle(
            'FieldValue', parent=sample['Normal'], fontName=_FONT,
            fontSize=8.4, leading=10.5, textColor=_INK,
        ),
        'status': ParagraphStyle(
            'Status', parent=sample['Normal'], fontName=_FONT_BOLD,
            fontSize=8.4, leading=10.5, textColor=colors.white,
        ),
        'count': ParagraphStyle(
            'Count', parent=sample['Normal'], fontName=_FONT_BOLD,
            fontSize=9, leading=11, textColor=_PRIMARY, alignment=TA_RIGHT,
        ),
    }

    story = [
        Paragraph('EXPORTACIÓN · TICKETS', styles['eyebrow']),
        Paragraph(_text(metadata['title']), styles['title']),
    ]
    meta_cells = []
    for label, value in (
        ('Empresa', metadata['brand']),
        ('Vista', metadata['view']),
        ('Periodo', f"{metadata['date_from']} a {metadata['date_to']}"),
        ('Generado', metadata['generated_at']),
    ):
        meta_cells.append([
            Paragraph(_text(label.upper()), styles['meta_label']),
            Spacer(1, 1.2 * mm),
            Paragraph(_text(value), styles['meta_value']),
        ])
    ticket_word = 'ticket' if metadata['total'] == 1 else 'tickets'
    meta_table = Table(
        [[*meta_cells, Paragraph(f"{metadata['total']} {ticket_word}", styles['count'])]],
        colWidths=[47 * mm, 67 * mm, 57 * mm, 57 * mm, 36 * mm],
    )
    meta_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), _SURFACE),
        ('BOX', (0, 0), (-1, -1), 0.6, _LINE),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('LEFTPADDING', (0, 0), (-1, -1), 6),
        ('RIGHTPADDING', (0, 0), (-1, -1), 6),
        ('TOPPADDING', (0, 0), (-1, -1), 7),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 7),
    ]))
    story.extend([meta_table, Spacer(1, 5 * mm)])

    if not metadata['total']:
        empty = Table([[Paragraph('No hay tickets para los filtros seleccionados.', styles['meta_value'])]],
                      colWidths=[landscape(A4)[0] - 28 * mm])
        empty.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, -1), _SURFACE),
            ('BOX', (0, 0), (-1, -1), 0.8, _LINE),
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('TOPPADDING', (0, 0), (-1, -1), 18),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 18),
        ]))
        story.append(empty)

    for ticket in tickets:
        status = ticket.status or '-'
        status_color = _STATUS_COLORS.get(status, _MUTED)
        status_cell = Paragraph(
            _text(ticket.get_status_display() or status), styles['status'],
        )
        priority = ticket.get_priority_display() if ticket.priority else '-'
        header = Table([
            [Paragraph(f'#{ticket.id}', styles['ticket_id']), status_cell,
             Paragraph(f'Prioridad: {_text(priority)}', styles['field_value'])]
        ], colWidths=[26 * mm, 28 * mm, 187 * mm])
        header.setStyle(TableStyle([
            ('BACKGROUND', (1, 0), (1, 0), status_color),
            ('ALIGN', (1, 0), (1, 0), 'CENTER'),
            ('ALIGN', (2, 0), (2, 0), 'RIGHT'),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('LEFTPADDING', (0, 0), (-1, -1), 5),
            ('RIGHTPADDING', (0, 0), (-1, -1), 5),
            ('TOPPADDING', (0, 0), (-1, -1), 4),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
        ]))

        solved_at = ticket.resolved_at or ticket.closed_at
        fields = [
            ('Servicio', ticket.service or '-'),
            ('Tipo', ticket.get_type_display() if ticket.type else '-'),
            ('Canal', ticket.get_channel_display() if ticket.channel else '-'),
            ('Categoría', ticket.category or '-'),
            ('Solicitante', ticket.requester.name if ticket.requester else '-'),
            ('Solicitado', _local_datetime(ticket.created_at)),
            ('Resuelto', _local_datetime(solved_at)),
        ]
        detail_cells = []
        for label, value in fields:
            detail_cells.append([
                Paragraph(_text(label.upper()), styles['field_label']),
                Paragraph(_text(value), styles['field_value']),
            ])
        details = Table([
            [detail_cells[0], detail_cells[1], detail_cells[2], detail_cells[3]],
            [detail_cells[4], detail_cells[5], detail_cells[6], ''],
        ], colWidths=[61 * mm, 54 * mm, 54 * mm, 72 * mm])
        details.setStyle(TableStyle([
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ('LEFTPADDING', (0, 0), (-1, -1), 5),
            ('RIGHTPADDING', (0, 0), (-1, -1), 5),
            ('TOPPADDING', (0, 0), (-1, -1), 3),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
            ('SPAN', (2, 1), (3, 1)),
        ]))

        card = Table([[
            [header, Spacer(1, 1.5 * mm), Paragraph(_text(ticket.subject), styles['subject']), details]
        ]], colWidths=[landscape(A4)[0] - 28 * mm])
        card.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, -1), colors.white),
            ('BOX', (0, 0), (-1, -1), 0.7, _LINE),
            ('LEFTPADDING', (0, 0), (-1, -1), 7),
            ('RIGHTPADDING', (0, 0), (-1, -1), 7),
            ('TOPPADDING', (0, 0), (-1, -1), 6),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
        ]))
        story.extend([KeepTogether([card]), Spacer(1, 3 * mm)])

    doc.build(story, onFirstPage=_page_chrome, onLaterPages=_page_chrome)
    return buffer.getvalue()
