from datetime import datetime, timezone
from html.parser import HTMLParser
from types import SimpleNamespace
from unittest.mock import patch

from django.template.loader import render_to_string
from django.test import SimpleTestCase, TestCase, override_settings

from app.services.conversation import Document, prepare_comments, present_comment


def comment(content='', html_body='', **kwargs):
    values = dict(content=content, html_body=html_body, via_channel='email', is_public=True,
                  created_at=datetime(2026, 9, 10, 9, tzinfo=timezone.utc),
                  user=SimpleNamespace(name='Cliente', email='client@example.test',
                                       is_superuser=False, role=None))
    values.update(kwargs)
    return SimpleNamespace(**values)


THREAD = '''Buenos días,

La extensión 54005 no aparece en el organigrama ni en los informes.

Un saludo
Equipo cliente
Soporte de voz
Email client@example.test

De: Agente (Helpdesk) <helpdesk@example.test>
Enviado: miércoles, 9 de septiembre de 2026 15:44
Cc: Cliente <client@example.test>
Asunto: Actualización de soporte

AVISO/WARNING: Correo externo.
##- Please type your reply above this line -##
You are on the list of people who receive copies of this support request.

Agente (Helpdesk)
Sep 9, 2026, 15:44 GMT+2
Procederemos al cierre del caso.
Un saludo,
Agente
Support Team
email: helpdesk@example.test

Cliente
Sep 9, 2026, 15:36 GMT+2
Ahora sí, gracias.

Agente (Helpdesk)
Sep 9, 2026, 15:28 GMT+2
¿Pueden volver a revisarlo?

Este mensaje y sus adjuntos se dirigen exclusivamente a su destinatario.
This email is a service from Helpdesk. Delivered by Zendesk
'''


class ConversationTests(SimpleTestCase):
    def test_zendesk_inside_outlook_is_separated_and_ordered(self):
        result = present_comment(comment(THREAD))
        self.assertEqual([h['author'] for h in result['history']], ['Agente (Helpdesk)', 'Cliente', 'Agente (Helpdesk)'])
        self.assertIn('54005', result['html'])
        self.assertNotIn('client@example.test', result['html'])
        self.assertIn('client@example.test', result['footer_html'])
        self.assertIn('AVISO/WARNING', result['extras_html'])
        self.assertIn('15:28', result['history'][0]['date'])
        self.assertIn('cierre', result['history'][-1]['html'])
        self.assertNotIn('cierre', result['html'])
        self.assertIn('exclusivamente', result['history'][0]['footer_html'])

    def test_html_keeps_tables_images_links_and_emphasis_without_mail_styles(self):
        source = '''<div style="font-family:Times;height:2000px"><p><strong>Estado</strong></p>
        <p>&nbsp;</p><div><br></div><table width="900"><tr><td colspan="2">54005</td><td>Activa</td></tr></table>
        <p><a href="https://example.test/report">Informe</a><img src="https://example.test/a.png" width="800"></p></div>'''
        result = present_comment(comment(html_body=source))
        self.assertIn('<table>', result['html'])
        self.assertIn('colspan="2"', result['html'])
        self.assertIn('<strong>Estado</strong>', result['html'])
        self.assertIn('<img', result['html'])
        self.assertIn('https://example.test/report', result['html'])
        self.assertNotIn('style=', result['html'])
        self.assertNotIn('width=', result['html'])
        self.assertNotIn('<p> </p>', result['html'])

    def test_html_headers_split_without_flattening_a_nested_table(self):
        source = '<div><p>Respuesta nueva</p><div><b>De:</b> Cliente</div><div>Enviado: ayer</div><div>Asunto: Prueba</div><table><tr><td><b>Dato anterior</b></td></tr></table></div>'
        result = present_comment(comment(html_body=source))
        self.assertIn('Respuesta nueva', result['html'])
        self.assertEqual(len(result['history']), 1)
        self.assertIn('<table>', result['history'][0]['html'])
        self.assertIn('<b>Dato anterior</b>', result['history'][0]['html'])

    def test_gmail_header_uses_explicit_sender_and_keeps_full_header(self):
        source = '<div>Respuesta</div><div class="gmail_quote"><div>On Wed, Sep 9, 2026 at 15:00 Someone &lt;a@example.test&gt; wrote:</div><blockquote>Consulta anterior</blockquote></div>'
        result = present_comment(comment(html_body=source))
        self.assertEqual(len(result['history']), 1)
        self.assertEqual(result['history'][0]['author'], 'a@example.test')
        self.assertIn('a@example.test', result['history'][0]['label'])
        self.assertIn('Consulta anterior', result['history'][0]['html'])

    def test_outlook_spanish_dates_are_sorted_chronologically(self):
        source = 'Actual\nDe: Agente\nEnviado: miércoles, 9 de septiembre de 2026 15:44\nAsunto: Prueba\nÚltima respuesta\nDe: Cliente\nEnviado: miércoles, 9 de septiembre de 2026 14:31\nAsunto: Prueba\nConsulta inicial'
        result = present_comment(comment(source))
        self.assertEqual([h['author'] for h in result['history']], ['Cliente', 'Agente'])

    def test_email_terminal_blockquote_is_collapsible(self):
        result = present_comment(comment(html_body='<p>Respuesta</p><blockquote>Consulta</blockquote>'))
        self.assertEqual(len(result['history']), 1)
        self.assertNotIn('Consulta', result['html'])

    def test_inline_answers_and_authored_web_quotes_stay_visible(self):
        source = '<p>Respuesta uno</p><blockquote>Pregunta dos</blockquote><p>Respuesta dos: cambiar el puerto.</p>'
        result = present_comment(comment(html_body=source))
        self.assertFalse(result['history'])
        self.assertIn('Respuesta dos', result['html'])
        self.assertIn('Pregunta dos', result['html'])
        web = present_comment(comment(html_body='<p>Texto</p><blockquote>Cita intencionada</blockquote>', via_channel='web'))
        self.assertFalse(web['history'])
        self.assertIn('Cita intencionada', web['html'])

    def test_header_looking_text_in_code_is_not_split(self):
        result = present_comment(comment(html_body='<pre>De: variable\nEnviado: variable\nAsunto: variable\nprint("test")</pre>'))
        self.assertFalse(result['history'])
        self.assertIn('print', result['html'])

    def test_single_date_in_message_and_reply_after_signature_stay_visible(self):
        result = present_comment(comment('Programado para\nSep 9, 2026, 15:44 GMT+2\nRevisar los registros'))
        self.assertFalse(result['history'])
        result = present_comment(comment('Respuesta\nUn saludo\nEquipo\nSupport Team\nEmail a@example.test\nPor cierto, necesitamos revisar también los informes.'))
        self.assertIn('necesitamos', result['html'])

    def test_signature_without_signoff_is_folded_after_the_message(self):
        result = present_comment(comment('Ahora sí,\n\nGRACIAS\n\nJR REAL\nSoporte Especialista Servicio Voz\nPhone +34 900 000 000\nEmail a@example.test'))
        self.assertIn('GRACIAS', result['html'])
        self.assertNotIn('JR REAL', result['html'])
        self.assertIn('JR REAL', result['footer_html'])

    def test_inline_reply_after_header_inside_blockquote_stays_visible(self):
        source = '<p>Respuesta uno</p><blockquote><p>On Wed someone wrote:</p><p>Pregunta</p></blockquote><p>Respuesta dos</p>'
        result = present_comment(comment(html_body=source))
        self.assertFalse(result['history'])
        self.assertIn('Respuesta dos', result['html'])

    def test_empty_table_cells_keep_column_alignment(self):
        result = present_comment(comment(html_body='<table><tr><td></td><td>54005</td><td></td></tr></table>'))
        self.assertIn('<td></td><td>54005</td><td></td>', result['html'])

    def test_identical_citation_text_with_different_links_is_not_deduped(self):
        source = THREAD.replace('Ahora sí, gracias.', '<a href="https://example.test/first">Informe</a>')
        first = comment(html_body=source.replace('\n', '<br>'))
        second = comment(html_body=source.replace('/first', '/second').replace('\n', '<br>'))
        prepare_comments([first, second])
        self.assertEqual(len(second.conversation['history']), 1)
        self.assertIn('/second', second.conversation['history'][0]['html'])

    def test_blank_lines_compacted_but_code_indentation_preserved(self):
        result = present_comment(comment('Hola\n' + '\n' * 40 + 'Segunda línea'))
        self.assertNotIn('<br><br><br>', result['html'])
        code = present_comment(comment(html_body='<pre>if ok:\n    run()\n\n    done()</pre>'))
        self.assertIn('\n    run()', code['html'])

    def test_plain_text_links_remain_clickable(self):
        result = present_comment(comment('Consulta https://example.test/report o escribe a a@example.test'))
        self.assertIn('href="https://example.test/report"', result['html'])
        self.assertIn('href="mailto:a@example.test"', result['html'])

    def test_web_links_open_safely_in_both_views_and_mail_and_phone_stay_native(self):
        class Links(HTMLParser):
            def __init__(self, source):
                super().__init__()
                self.links = []
                self.feed(source)

            def handle_starttag(self, tag, attrs):
                if tag == 'a':
                    self.links.append(dict(attrs))

        sources = [
            comment('Consulta https://example.test/report y http://example.test/status o a@example.test'),
            comment(html_body='<p style="color:red"><a href="https://example.test/report?a=1&amp;b=2" target="_self" rel="opener">Alarma</a> '
                    '<a href="mailto:a@example.test">Correo</a> <a href="tel:+34900000000">Teléfono</a></p>'),
        ]
        for source in sources:
            result = present_comment(source)
            for key in ('html', 'original_html'):
                links = Links(result[key]).links
                self.assertTrue(links)
                for link in links:
                    with self.subTest(source=source.html_body, view=key, href=link['href']):
                        if link['href'].startswith(('https://', 'http://')):
                            self.assertEqual(link.get('target'), '_blank')
                            self.assertEqual(set(link['rel'].split()), {'noopener', 'noreferrer'})
                        else:
                            self.assertNotIn('target', link)
            if source.html_body:
                self.assertIn('style="color:red;"', result['original_html'])

    def test_ambiguous_from_and_invalid_dates_do_not_hide_text(self):
        result = present_comment(comment('De: esta configuración depende el error\nAsunto: falta fecha\nSep 99, 2026, 25:70 GMT+2\nFinal importante'))
        self.assertFalse(result['history'])
        self.assertIn('Final importante', result['html'])

    def test_untrusted_email_is_sanitized_in_both_views(self):
        result = present_comment(comment(html_body='<script>alert(1)</script><p onclick="bad()">Texto<img src="x" onerror="bad()"><a href="javascript:bad()">link</a></p>'))
        for output in (result['html'], result['original_html']):
            self.assertNotIn('<script', output)
            self.assertNotIn('onclick=', output)
            self.assertNotIn('onerror=', output)
            self.assertNotIn('javascript:', output)
        self.assertNotIn('alert(1)', result['html'])

    def test_malformed_legacy_html_falls_back_without_breaking_ticket(self):
        result = present_comment(comment(html_body='<p>Respuesta</p><![broken]><p>Final</p>'))
        self.assertIn('Respuesta', result['html'])
        self.assertIn('Final', result['html'])
        self.assertFalse(result['history'])

    def test_render_escapes_quoted_author_and_uses_native_details(self):
        data = present_comment(comment('Nuevo\nDe: <img src=x onerror=bad()>\nEnviado: ayer\nAsunto: Prueba\nAnterior'))
        output = render_to_string('tickets/_conversation_body.html', {'conversation': data})
        self.assertIn('&lt;img src=x onerror=bad()&gt;', output)
        self.assertIn('<details', output)
        self.assertIn('Ver mensaje original', output)

    def test_duplicate_citations_are_deduped_by_author_timestamp_and_complete_body(self):
        a = comment(THREAD)
        b = comment(THREAD.replace('La extensión 54005 no aparece en el organigrama ni en los informes.', 'Nueva respuesta diferente.'))
        prepare_comments([a, b])
        self.assertEqual(len(a.conversation['history']), 3)
        self.assertEqual(len(b.conversation['history']), 0)
        self.assertEqual(b.conversation['duplicate_count'], 3)
        self.assertIn('cierre', b.conversation['original_html'])

    def test_same_text_from_other_author_or_private_scope_is_not_deduped(self):
        a = comment(THREAD)
        b = comment(THREAD.replace('Cliente\nSep', 'Otro cliente\nSep'))
        private = comment(THREAD, is_public=False)
        prepare_comments([private, a, b])
        self.assertEqual(len(a.conversation['history']), 3)
        self.assertEqual(len(b.conversation['history']), 1)
        self.assertEqual(b.conversation['history'][0]['author'], 'Otro cliente')

    def test_existing_real_message_replaces_only_exact_public_quote(self):
        real = comment('Ahora sí, gracias.', created_at=datetime(2026, 9, 9, 13, 36, tzinfo=timezone.utc))
        incoming = comment(THREAD)
        prepare_comments([real, incoming])
        self.assertEqual(incoming.conversation['duplicate_count'], 1)
        self.assertEqual(len(incoming.conversation['history']), 2)

    def test_preparation_does_not_mutate_stored_content(self):
        original = comment(THREAD)
        prepare_comments([original])
        self.assertEqual(original.content, THREAD)
        self.assertEqual(original.html_body, '')


@override_settings(ALLOWED_HOSTS=['testserver'], OUTBOUND_EMAIL_ENABLED=False,
                   AI_CLASSIFICATION_ENABLED=False, AI_REPLY_ASSIST_ENABLED=False)
class ConversationIntegrationTests(TestCase):
    def setUp(self):
        from app.models import Role, Ticket, User, Comment
        self.user = User.objects.create_user(email='conversation@example.test', name='Cliente', password='test')
        self.user.role = Role.objects.create(role_name='end user')
        self.user.save()
        self.ticket = Ticket.objects.create(subject='Conversation regression', description='test', requester=self.user, created_by=self.user)
        self.old = Comment.objects.create(ticket=self.ticket, user=self.user, content=THREAD, via_channel='email')
        self.client.force_login(self.user)

    def test_historical_ticket_and_spa_fragment_use_same_presentation_without_writes(self):
        from app.models import Comment, TicketEvent
        counts = Comment.objects.count(), TicketEvent.objects.count()
        for suffix in ('', '&fragment=1'):
            response = self.client.get(f'/tickets/create/?id={self.ticket.pk}{suffix}')
            self.assertEqual(response.status_code, 200)
            self.assertContains(response, 'data-conversation')
            self.assertContains(response, 'Historial citado · 3 mensajes')
            self.assertContains(response, 'speaker-customer')
        self.old.refresh_from_db()
        self.assertEqual(self.old.content, THREAD)
        self.assertEqual(counts, (Comment.objects.count(), TicketEvent.objects.count()))

    def test_publishing_returns_shared_renderer_and_keeps_private_comments_private(self):
        from app.models import Comment
        Comment.objects.create(ticket=self.ticket, user=self.user, content='Private sentinel', is_public=False)
        response = self.client.post(f'/tickets/{self.ticket.pk}/add_comment/',
            data={'content': 'Respuesta actual\n\n\nDe: Otro\nEnviado: ayer\nAsunto: Prueba\nConsulta anterior', 'is_public': True}, content_type='application/json')
        self.assertEqual(response.status_code, 200)
        self.assertIn('Historial citado', response.json()['conversation_html'])
        self.assertEqual(response.json()['speaker_kind'], 'customer')
        self.assertNotIn('Private sentinel', response.json()['conversation_html'])
        self.assertNotIn('Private sentinel', self.client.get(f'/tickets/create/?id={self.ticket.pk}').content.decode())

    @patch('app.services.email_ingestion.EmailIngestionService._store_attachments')
    @patch('app.services.email_ingestion.EmailIngestionService.find_ticket_for_message', return_value=None)
    @patch('app.services.email_ingestion.TicketService.create_ticket')
    def test_inbound_new_ticket_preserves_html(self, create, find, store):
        from app.services.email_ingestion import EmailIngestionService
        from app.models import Brand
        brand = Brand.objects.create(name='Conversation mail', support_email='help@example.test')
        create.return_value = self.ticket
        source = '<p>Respuesta</p><table><tr><td>54005</td></tr></table>'
        EmailIngestionService._process_message({'id': 'conversation-unique-message',
            'from': {'emailAddress': {'address': self.user.email}}, 'subject': 'Prueba',
            'body': {'contentType': 'html', 'content': source}}, brand)
        self.assertIn('<table>', create.call_args.args[1]['html_body'])
