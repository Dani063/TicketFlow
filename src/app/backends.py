from django.contrib.auth.hashers import check_password
from django.contrib.auth.backends import BaseBackend
from app.models import User

class EmailBackend(BaseBackend):
    def authenticate(self, request, username=None, password=None, **kwargs):
        try:
           user = User.objects.get(email=username)  # Usa email como username
           if not user.is_active:
                return None
           if user.check_password(password):  # Comprueba la contraseña correctamente
                return user
        except User.DoesNotExist:
            return None
        return None

    def get_user(self, user_id):
        try:
            return User.objects.get(pk=user_id)
        except User.DoesNotExist:
            return None