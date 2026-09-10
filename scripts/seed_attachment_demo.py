"""Local-only demo data. Never runs against a deployed database or sends mail."""
import io
import json
import os
import zipfile
from unittest.mock import patch

from PIL import Image, ImageDraw
from reportlab.pdfgen import canvas
from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from app.models import Attachment, Brand, Comment, ProductLine, Role, Ticket, User
from app.services.attachments import AttachmentService
from app.services.tickets import TicketService

assert connection.vendor == 'sqlite' and settings.DEBUG
assert not settings.OUTBOUND_EMAIL_ENABLED
role, _ = Role.objects.get_or_create(role_name='agent')
user, _ = User.objects.get_or_create(email='attachments-qa@ticketflow.local', defaults={
    'name': 'Demo de adjuntos', 'role': role, 'is_active': True,
})
brand = Brand.objects.order_by('id').first()
if not brand:
    brand = Brand.objects.create(name='Demo local')
product = ProductLine.objects.filter(active=True).order_by('id').first()
if not product:
    product = ProductLine.objects.create(code='demo-local', name='Demo local', active=True)
subject = os.environ.get('ATTACHMENT_DEMO_SUBJECT', 'Prueba visual · Adjuntos de mensajería')
ticket = Ticket.objects.filter(subject=subject, created_by=user).first()
if not ticket:
    with patch('app.services.tickets.AutomationService.run_for_ticket'):
        ticket = TicketService.create_ticket(user, {
            'subject': subject, 'content': 'Aquí puedes probar los nuevos adjuntos. Son archivos de demostración locales.',
            'requester_id': user.id, 'brand': brand, 'product_line': product,
            'status': 'open', 'suppress_requester_email': True,
        })
comment = ticket.comment_set.order_by('id').first()

def add(name, content, mime, target=comment):
    if not Attachment.objects.filter(comment=target, original_name=name).exists():
        AttachmentService.save(SimpleUploadedFile(name, content, content_type=mime), ticket, user, comment=target)

pdf = io.BytesIO()
page = canvas.Canvas(pdf, pagesize=(420, 300))
page.setTitle('Guía de prueba de adjuntos')
page.setFont('Helvetica-Bold', 21)
page.drawString(32, 250, 'TicketFlow / Adjuntos')
page.setFont('Helvetica', 12)
page.drawString(32, 216, 'Documento de prueba local. Sin datos de clientes.')
page.drawString(32, 186, 'Vista previa privada y descarga del archivo original.')
page.showPage()
page.save()
add('Guía de comprobación.pdf', pdf.getvalue(), 'application/pdf')
add('test_numerosYO.csv', 'Registro,Estado,Tiempo\nPrueba A,Correcto,12\nPrueba B,Revisar,8\n'.encode(), 'text/csv')
add('Registro de diagnóstico con un nombre largo para comprobar que la extensión permanece visible.log',
    ('[INFO] Prueba local completada\n[INFO] Los adjuntos conservan su nombre\n' * 8).encode(), 'text/plain')
zipped = io.BytesIO()
with zipfile.ZipFile(zipped, 'w', zipfile.ZIP_DEFLATED) as archive:
    archive.writestr('LEEME.txt', 'Este ZIP contiene únicamente datos ficticios para QA local.')
add('Evidencias de soporte.zip', zipped.getvalue(), 'application/zip')

internal = ticket.comment_set.filter(content='Capturas de ejemplo · Nota interna').first()
if not internal:
    internal = Comment.objects.create(ticket=ticket, user=user, content='Capturas de ejemplo · Nota interna', is_public=False)
for index, accent in enumerate(('#7c3aed', '#0891b2'), start=1):
    picture = Image.new('RGB', (960, 540), '#f8fafc')
    draw = ImageDraw.Draw(picture)
    draw.rectangle((0, 0, 960, 64), fill=accent)
    draw.text((28, 24), f'TicketFlow - captura de ejemplo {index}', fill='white')
    for row, width in enumerate((580, 780, 460)):
        y = 108 + row * 105
        draw.rounded_rectangle((32, y, width, y + 70), radius=9, fill='white', outline='#cbd5e1', width=2)
        draw.text((52, y + 28), f'Archivo de prueba {row + 1} / datos ficticios', fill='#1e293b')
    buffer = io.BytesIO()
    picture.save(buffer, format='PNG')
    add(f'Captura de soporte {index}.png', buffer.getvalue(), 'image/png', internal)

print(json.dumps({'ticket_id': ticket.id, 'email': user.email, 'product_id': product.id, 'brand': brand.name}, ensure_ascii=False))
