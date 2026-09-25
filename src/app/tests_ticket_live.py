"""Refresco automatico del ticket abierto (endpoint /api/tickets/<id>/live/).

Lo que se protege aqui:

- La visibilidad es la MISMA que al publicar: un usuario final no puede ver por
  sondeo un comentario interno que no veria en la conversacion.
- El payload de un comentario tiene la misma forma que el de add_comment. El
  frontend pinta ambos con la misma funcion, asi que en cuanto las dos rutas
  divergen el refresco automatico renderiza distinto que la publicacion propia.
- El caso normal (nada nuevo) no devuelve comentarios: es una llamada cada pocos
  segundos y por ahi se va el coste si se descuida.
"""

import json

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from app.models import Comment, Role, Ticket, User


class TicketLiveTests(TestCase):
    def setUp(self):
        self.end_role = Role.objects.create(role_name="End user")
        self.agent_role = Role.objects.create(role_name="agent")

        self.customer = User.objects.create_user(
            email="customer@example.com", name="Customer", password="secret",
        )
        self.customer.role = self.end_role
        self.customer.save(update_fields=["role"])

        self.agent = User.objects.create_user(
            email="agent@example.com", name="Agent", password="secret",
        )
        self.agent.role = self.agent_role
        self.agent.save(update_fields=["role"])

        self.outsider = User.objects.create_user(
            email="outsider@example.com", name="Outsider", password="secret",
        )
        self.outsider.role = self.end_role
        self.outsider.save(update_fields=["role"])

        self.ticket = Ticket.objects.create(
            subject="Ticket", description="desc", status="open", priority="normal",
            requester=self.customer, assignee=self.agent, created_by=self.customer,
        )

    def comment(self, user, content, is_public=True):
        return Comment.objects.create(
            ticket=self.ticket, user=user, content=content,
            is_public=is_public, created_at=timezone.now(),
        )

    def live(self, after=0):
        response = self.client.get(
            reverse("ticket_live", args=[self.ticket.id]), {"after": after},
        )
        self.assertEqual(response.status_code, 200)
        return response.json()

    def test_solo_devuelve_los_comentarios_posteriores_a_after(self):
        primero = self.comment(self.customer, "uno")
        segundo = self.comment(self.agent, "dos")
        self.client.force_login(self.agent)

        data = self.live(after=primero.id)

        self.assertEqual([c["id"] for c in data["comments"]], [segundo.id])
        self.assertEqual(data["last_comment_id"], segundo.id)

    def test_sin_novedades_no_devuelve_comentarios_pero_si_la_cabecera(self):
        ultimo = self.comment(self.agent, "uno")
        self.client.force_login(self.agent)

        data = self.live(after=ultimo.id)

        self.assertEqual(data["comments"], [])
        self.assertEqual(data["last_comment_id"], ultimo.id)
        self.assertEqual(data["ticket"]["status"], "open")

    def test_la_cabecera_refleja_el_estado_ya_cambiado(self):
        self.ticket.status = "pending"
        self.ticket.save(update_fields=["status"])
        self.client.force_login(self.agent)

        data = self.live()

        self.assertEqual(data["ticket"]["status"], "pending")
        self.assertEqual(data["ticket"]["status_display"], "Pending")
        self.assertEqual(data["ticket"]["assignee_name"], "Agent")

    def test_el_usuario_final_no_ve_los_comentarios_internos(self):
        publico = self.comment(self.agent, "publico", is_public=True)
        self.comment(self.agent, "nota interna", is_public=False)

        self.client.force_login(self.customer)
        data = self.live()
        self.assertEqual([c["id"] for c in data["comments"]], [publico.id])

        # El agente si los ve: la diferencia es de permisos, no de endpoint.
        self.client.force_login(self.agent)
        data = self.live()
        self.assertEqual(len(data["comments"]), 2)

    def test_last_comment_id_no_retrocede_con_comentarios_internos_ocultos(self):
        publico = self.comment(self.agent, "publico", is_public=True)
        interno = self.comment(self.agent, "interna", is_public=False)
        self.client.force_login(self.customer)

        data = self.live(after=publico.id)

        # El usuario final no ve el interno, asi que su puntero se queda donde
        # estaba; si avanzara hasta `interno.id` se saltaria el siguiente publico.
        self.assertEqual(data["comments"], [])
        self.assertEqual(data["last_comment_id"], publico.id)
        self.assertLess(data["last_comment_id"], interno.id)

    def test_sin_acceso_al_ticket_responde_403(self):
        self.client.force_login(self.outsider)
        response = self.client.get(reverse("ticket_live", args=[self.ticket.id]))
        self.assertEqual(response.status_code, 403)

    def test_el_payload_del_comentario_tiene_la_misma_forma_que_el_de_add_comment(self):
        self.client.force_login(self.agent)
        publicado = self.client.post(
            reverse("add_comment", args=[self.ticket.id]),
            data=json.dumps({"content": "respuesta", "is_public": True}),
            content_type="application/json",
        )
        self.assertEqual(publicado.status_code, 200)
        del_post = publicado.json()

        del_live = self.live()["comments"][0]

        # add_comment anade new_status y la cabecera del ticket; por lo demas las
        # dos rutas alimentan la misma funcion de render del frontend y no pueden
        # divergir.
        self.assertEqual(set(del_post) - {"new_status", "ticket"}, set(del_live))
        self.assertEqual(del_post["id"], del_live["id"])
        self.assertEqual(del_post["conversation_html"], del_live["conversation_html"])
        self.assertEqual(del_post["speaker_kind"], del_live["speaker_kind"])
        self.assertEqual(del_post["username"], del_live["username"])

    def test_publicar_devuelve_la_cabecera_ya_con_el_estado_nuevo(self):
        """Publicar dejaba la chapa de estado con el valor con que cargo la pagina."""
        self.client.force_login(self.agent)
        respuesta = self.client.post(
            reverse("add_comment", args=[self.ticket.id]),
            data=json.dumps({"content": "cerramos", "is_public": True, "new_status": "resolved"}),
            content_type="application/json",
        )
        self.assertEqual(respuesta.status_code, 200)
        cabecera = respuesta.json()["ticket"]
        self.assertEqual(cabecera["status"], "resolved")
        self.assertEqual(cabecera["status_display"], "Resolved")
        # Misma forma que la del sondeo: el frontend las pinta con la misma funcion.
        self.assertEqual(set(cabecera), set(self.live()["ticket"]))

    def test_la_cabecera_lleva_reloj_del_servidor_para_ordenar_respuestas(self):
        """Sin el, el sondeo repinta con lo que leyo antes de publicar."""
        self.client.force_login(self.agent)
        antes = self.live()["ticket"]["updated_ts"]
        respuesta = self.client.post(
            reverse("add_comment", args=[self.ticket.id]),
            data=json.dumps({"content": "cerramos", "is_public": True, "new_status": "resolved"}),
            content_type="application/json",
        )
        self.assertGreater(respuesta.json()["ticket"]["updated_ts"], antes)
