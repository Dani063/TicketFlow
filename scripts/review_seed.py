"""Synthetic examples for the isolated SQLite review; no real customers/mail."""
import io
import json
from datetime import datetime, timedelta, timezone as utc

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from PIL import Image

from app.models import Brand, Comment, Group, ProductLine, Role, SatisfactionRating, SLAPolicy, Ticket, User
from app.services.email_ingestion import EmailIngestionService

PREFIX = '[QA 21377] '
ACCOUNTS = (
    ('admin', 'qa.admin@example.test', 'QA Administrador'),
    ('agent', 'qa.agente.a@example.test', 'QA Agente A'),
    ('agent', 'qa.agente.b@example.test', 'QA Agente B'),
    ('End user', 'qa.cliente@example.test', 'QA Cliente'),
    ('End user', 'qa.cc@example.test', 'QA Copia'),
)


@transaction.atomic
def seed_review(root, *, reset=False, port=8010):
    assert settings.DATABASES['default']['ENGINE'] == 'django.db.backends.sqlite3'
    assert not settings.OUTBOUND_EMAIL_ENABLED and not settings.AI_CLASSIFICATION_ENABLED
    assert settings.ATTACHMENT_STORAGE_BACKEND == 'local'
    if reset:
        examples = Ticket.objects.filter(subject__startswith=PREFIX)
        SatisfactionRating.objects.filter(ticket__in=examples).delete()
        examples.delete()
    group, _ = Group.objects.get_or_create(group_name='QA Soporte')
    people = {}
    for role_name, email, name in ACCOUNTS:
        role, _ = Role.objects.get_or_create(role_name=role_name)
        user, created = User.objects.get_or_create(email=email, defaults={'name': name})
        user.role, user.group = role, group
        user.is_active = True
        user.is_staff = user.is_superuser = role_name == 'admin'
        if created:
            user.set_unusable_password()
        user.save()
        people[email] = user
    admin, agent, other, customer, copied = [people[item[1]] for item in ACCOUNTS]
    brand, _ = Brand.objects.get_or_create(name='QA Revisión local', defaults={
        'support_email':'helpdesk@example.test', 'from_name':'Soporte QA', 'mailbox_type':'m365',
    })
    product = ProductLine.objects.get(code='recordia')
    SLAPolicy.objects.get_or_create(name='QA Revisión: primera respuesta 90 min, resolución 180 min', defaults={
        'brand':brand,'assigned_group':group,'service':'recordia',
        'first_response_minutes':90,'resolution_minutes':180,'active':True,
    })
    now = timezone.now()
    scenarios = []

    def ticket(code, label, **changes):
        age = changes.pop('age_days', 0)
        values = dict(description='Datos sintéticos para la revisión conjunta.', status='open', type='incident',
            priority='normal', requester=customer, created_by=agent, assignee=agent,
            assigned_group=group, brand=brand, product_line=product, service='recordia', language='es', channel='web')
        values.update(changes)
        item, created = Ticket.objects.get_or_create(subject=PREFIX+code+' '+label, defaults=values)
        if created:
            Ticket.objects.filter(pk=item.pk).update(created_at=now-timedelta(days=age), updated_at=now)
            item.refresh_from_db()
        scenarios.append({'code':code,'id':item.id,'subject':item.subject,'url':f'http://127.0.0.1:{port}/tickets/create/?id={item.id}'})
        return item

    ticket('01','Abierto asignado al agente A')
    ticket('02','Pendiente antiguo (120 días)', status='pending', age_days=120)
    ticket('03','Abierto sin asignar', assignee=None)
    ticket('04','Abierto asignado al agente B', assignee=other)
    ticket('05','Tarea: también en las colas de no resueltos', type='task')
    solved = ticket('06','Resuelto: fuera de la cola no resuelta', status='resolved', resolved_at=now)
    ticket('07','On-hold: notas y SLA en pausa', status='on_hold', age_days=2/24,
        on_hold_at=now-timedelta(hours=1), resolved_at=now-timedelta(hours=1),
        first_response_due_at=now-timedelta(minutes=30), resolution_due_at=now+timedelta(hours=1),
        due_at=now+timedelta(hours=1))
    ticket('08','Cerrado: solo lectura', status='closed', resolved_at=now-timedelta(days=1), closed_at=now)
    ticket('09','Eliminado: restauración desde papelera', is_deleted=True, deleted_at=now, deleted_by=agent)

    inline_subject = PREFIX+'10 Correo simulado: párrafos, enlaces e imagen inline'
    inline = Ticket.objects.filter(subject=inline_subject).first()
    if inline is None:
        image = io.BytesIO()
        Image.new('RGB',(240,60),color='#6654d9').save(image,format='PNG')
        body = ('<p>Primer párrafo del mensaje.</p><p><br></p>'
            '<p>Segundo párrafo, separado del anterior.</p>'
            '<p><a href="https://example.com">Enlace web de prueba</a></p>'
            '<p><a href="mailto:qa.cliente@example.test">Correo</a> · <a href="tel:+34910000000">Teléfono</a></p>'
            '<p>Imagen inline: debe verse un rectángulo violeta.</p><img src="cid:qa-logo-21377">')
        message={'id':'qa-inline-21377-'+now.strftime('%Y%m%d%H%M%S%f'),'conversationId':'qa-local-inline-21377',
            'subject':inline_subject,'receivedDateTime':now.isoformat(),
            'from':{'emailAddress':{'address':customer.email,'name':customer.name}},
            'toRecipients':[{'emailAddress':{'address':brand.support_email}}],
            'ccRecipients':[{'emailAddress':{'address':copied.email,'name':copied.name}}],
            'hasAttachments':False,'body':{'contentType':'html','content':body}}
        attachments=[{'name':'imagen-qa.png','content':image.getvalue(),'content_type':'image/png',
            'is_inline':True,'content_id':'qa-logo-21377'},
            {'name':'informe-qa.txt','content':b'Adjunto local de prueba.','content_type':'text/plain'}]
        _, inline=EmailIngestionService._process_message(message,brand,attachments=attachments,suppress_confirmation=True)
        Ticket.objects.filter(pk=inline.pk).update(assignee=agent,assigned_group=group,product_line=product,service='recordia')
    scenarios.append({'code':'10','id':inline.id,'subject':inline.subject,'url':f'http://127.0.0.1:{port}/tickets/create/?id={inline.id}'})

    ticket('11','Cambiar estado sin escribir comentario')
    clock=ticket('12','Hora española: creado a las 08:00 UTC')
    if not Comment.objects.filter(ticket=clock,content='Mensaje con hora de referencia').exists():
        sample=datetime(2026,10,27,8,0,tzinfo=utc.utc)
        Ticket.objects.filter(pk=clock.pk).update(created_at=sample,updated_at=sample)
        c=Comment.objects.create(ticket=clock,user=customer,content='Mensaje con hora de referencia',is_public=True)
        Comment.objects.filter(pk=c.pk).update(created_at=sample)
    ticket('13','On-hold vencido: 73 horas',status='on_hold',age_days=4,
        on_hold_at=now-timedelta(hours=73),resolved_at=now-timedelta(hours=73))
    ticket('14','On-hold aún vigente: 71 horas',status='on_hold',age_days=4,
        on_hold_at=now-timedelta(hours=71),resolved_at=now-timedelta(hours=71))
    for number in range(15,28):
        ticket(f'{number:02d}','Fila para paginación')
    other_solved=ticket('28','Resuelto antiguo y valoración del agente B',status='resolved',assignee=other,
        age_days=12,resolved_at=now-timedelta(days=10))
    SatisfactionRating.objects.get_or_create(ticket=solved,score='good',defaults={'requester':customer,'assignee':agent,'created_at':now})
    SatisfactionRating.objects.get_or_create(ticket=other_solved,score='bad',defaults={'requester':customer,'assignee':other,'created_at':now})
    counts={
        'personal_agente_a':Ticket.objects.filter(assignee=agent,is_deleted=False,merged_into__isnull=True)
            .exclude(status__in=('resolved','on_hold','closed')).count(),
        'todos_no_resueltos':Ticket.objects.filter(is_deleted=False,merged_into__isnull=True)
            .exclude(status__in=('resolved','on_hold','closed')).count(),
    }
    index={'users':[{'email':item[1],'name':item[2],'role':item[0]} for item in ACCOUNTS],
        'brand':brand.name,'scenarios':scenarios,'initial_counts':counts}
    (root/'.review-local'/'escenarios.json').write_text(json.dumps(index,ensure_ascii=False,indent=2),encoding='utf-8')
    lines=['# Datos para la revisión local','',f'Entrada: http://127.0.0.1:{port}/dev-login/','',
        '## Cuentas','',*[f'- {item[2]}: `{item[1]}`' for item in ACCOUNTS],'',
        '## Tickets','', '| Código | Ticket | Escenario |','|---|---|---|',
        *[f"| {s['code']} | [#{s['id']}]({s['url']}) | {s['subject']} |" for s in scenarios], '',
        '## Contadores al preparar los datos','',*[f'- {key}: **{value}**' for key,value in counts.items()], '',
        'Los contadores cambian al hacer las pruebas. `reset-data` restablece solo los tickets cuyo asunto empieza por `[QA 21377]`.']
    (root/'.review-local'/'escenarios.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    print(f'Datos listos: {len(scenarios)} tickets y {len(ACCOUNTS)} cuentas.')
    print('Índice: '+str(root/'.review-local'/'escenarios.md'))
