from django.urls import path

from health import views

urlpatterns = [
    path("livez", views.livez, name="livez"),
    path("readyz", views.readyz, name="readyz"),
]
