from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from .models import User, Ticket, Comment

class TicketFlowTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email="test@example.com",
            name="Test User",
            password="secret"
        )
        self.client.force_login(self.user)

    def test_create_ticket(self):
        response = self.client.post(reverse("create_ticket"), {
            "subject": "Test Ticket",
            "message": "Contenido inicial",
            "status": "open",
        })
        self.assertEqual(response.status_code, 302)  # redirige al ticket creado
        self.assertTrue(Ticket.objects.filter(subject="Test Ticket").exists())

    def test_add_comment(self):
        ticket = Ticket.objects.create(
            subject="Ticket Comentario",
            description="desc",
            status="open",
            priority="normal",
            requester=self.user,
            assignee=self.user,
            created_by=self.user,
            created_at=timezone.now(),
        )
        url = reverse("add_comment", args=[ticket.id])
        response = self.client.post(url, data={
            "content": "Primer comentario",
            "is_public": True
        }, content_type="application/json")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(Comment.objects.filter(ticket=ticket, content="Primer comentario").exists())
