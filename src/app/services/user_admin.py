"""CRUD y reglas de negocio del panel de administración de usuarios."""

import re

from django.db.models import Q

from app.api import APIValidationError
from app.models import Comment, Group, Role, Ticket, User
from app.permissions import is_admin

_EMAIL_RE = re.compile(r'^[^@\s]+@[^@\s]+\.[^@\s]+$')

_PAGE_SIZES = (25, 50, 100, 200)


class UserAdminService:
    @staticmethod
    def serialize(u):
        return {
            'id': u.id,
            'name': u.name,
            'email': u.email,
            'role_id': u.role_id,
            'role_name': u.role.role_name if u.role else None,
            'group_id': u.group_id,
            'group_name': u.group.group_name if u.group else None,
            'is_active': bool(u.is_active),
            'created_at': u.created_at.strftime('%d/%m/%Y') if u.created_at else '',
        }

    @staticmethod
    def list_users(params):
        qs = User.objects.select_related('role', 'group').order_by('name')
        q = (params.get('q') or '').strip()
        if q:
            qs = qs.filter(Q(name__icontains=q) | Q(email__icontains=q))
        role_id = params.get('role_id')
        group_id = params.get('group_id')
        if role_id and role_id.isdigit():
            qs = qs.filter(role_id=int(role_id))
        if group_id and group_id.isdigit():
            qs = qs.filter(group_id=int(group_id))

        try:
            page = max(1, int(params.get('page') or 1))
        except (ValueError, TypeError):
            page = 1
        try:
            page_size = int(params.get('page_size') or 50)
            if page_size not in _PAGE_SIZES:
                page_size = 50
        except (ValueError, TypeError):
            page_size = 50

        total = qs.count()
        total_pages = max(1, (total + page_size - 1) // page_size)
        page = min(page, total_pages)
        offset = (page - 1) * page_size
        users = list(qs[offset:offset + page_size])
        return {
            'users': [UserAdminService.serialize(u) for u in users],
            'pagination': {
                'total': total, 'page': page,
                'total_pages': total_pages, 'page_size': page_size,
            },
        }

    @staticmethod
    def create_user(data):
        name = (data.get('name') or '').strip()
        email = (data.get('email') or '').strip().lower()
        if not name:
            raise APIValidationError('name_required', 'El nombre es obligatorio', status=400)
        if not _EMAIL_RE.match(email):
            raise APIValidationError('invalid_email', 'Email no válido', status=400)
        if User.objects.filter(email__iexact=email).exists():
            raise APIValidationError('email_taken', 'Ya existe un usuario con ese email', status=400)

        role = None
        if data.get('role_id'):
            role = Role.objects.filter(id=data['role_id']).first()
            if not role:
                raise APIValidationError('role_not_found', 'Rol no encontrado', status=400)
        group = None
        if data.get('group_id'):
            group = Group.objects.filter(id=data['group_id']).first()
            if not group:
                raise APIValidationError('group_not_found', 'Grupo no encontrado', status=400)

        user = User(email=email, name=name, role=role, group=group, is_active=True)
        password = (data.get('password') or '').strip()
        if password:
            user.set_password(password)
        else:
            user.set_unusable_password()
        user.save()
        return user

    @staticmethod
    def create_end_user(email, name=None):
        """Alta rápida de un solicitante desde el ticket: solo hace falta el email.

        Devuelve (usuario, creado). Si el email ya existe y está activo se
        devuelve ese usuario, que es lo que el agente quería seleccionar (el
        nombre recibido se ignora: no se renombra a nadie desde aquí). Sin
        nombre, sale de la parte local del email, igual que en la ingesta de
        correo; se puede cambiar después desde el panel de usuarios.
        """
        email = (email or '').strip().lower()
        if not _EMAIL_RE.match(email):
            raise APIValidationError('invalid_email', 'Email no válido', status=400)
        existing = User.objects.filter(email__iexact=email).first()
        if existing:
            if not existing.is_active:
                raise APIValidationError(
                    'user_inactive',
                    'Ya existe un usuario con ese email, pero está desactivado. Reactívalo desde el panel de usuarios.',
                    status=409,
                )
            return existing, False

        role, _ = Role.objects.get_or_create(role_name='End user')
        user = User(
            email=email,
            name=(name or '').strip()[:255] or email.split('@')[0],
            role=role,
            group=Group.objects.filter(group_name__iexact='End users').first(),
            is_active=True,
        )
        user.set_unusable_password()
        user.save()
        return user, True

    @staticmethod
    def update_user(actor, user_id, data):
        if not user_id:
            raise APIValidationError('id_required', 'id requerido', status=400)
        user = User.objects.filter(id=user_id).first()
        if not user:
            raise APIValidationError('user_not_found', 'Usuario no encontrado', status=404)

        # No permitir que un admin se desactive a sí mismo (riesgo lockout)
        if user.id == actor.id and data.get('is_active') is False:
            raise APIValidationError('self_deactivate', 'No puedes desactivar tu propia cuenta', status=400)

        if 'name' in data:
            name = (data.get('name') or '').strip()
            if not name:
                raise APIValidationError('name_required', 'El nombre no puede estar vacío', status=400)
            user.name = name

        if 'email' in data:
            new_email = (data.get('email') or '').strip().lower()
            if not _EMAIL_RE.match(new_email):
                raise APIValidationError('invalid_email', 'Email no válido', status=400)
            if User.objects.filter(email__iexact=new_email).exclude(id=user.id).exists():
                raise APIValidationError('email_taken', 'Ya existe otro usuario con ese email', status=400)
            user.email = new_email

        if 'role_id' in data:
            rid = data.get('role_id')
            if rid in (None, '', 0):
                user.role = None
            else:
                role = Role.objects.filter(id=rid).first()
                if not role:
                    raise APIValidationError('role_not_found', 'Rol no encontrado', status=400)
                # Si quitamos el rol admin a un admin, asegurarnos de no dejar el sistema sin admins
                if (user.role and is_admin(user) and role.role_name.lower()
                        not in ('admin', 'administrator', 'administrador')):
                    if not User.objects.filter(role__role_name__iexact='admin', is_active=True).exclude(id=user.id).exists():
                        raise APIValidationError('last_admin', 'Debe quedar al menos un administrador activo', status=400)
                user.role = role

        if 'group_id' in data:
            gid = data.get('group_id')
            if gid in (None, '', 0):
                user.group = None
            else:
                group = Group.objects.filter(id=gid).first()
                if not group:
                    raise APIValidationError('group_not_found', 'Grupo no encontrado', status=400)
                user.group = group

        if 'is_active' in data:
            user.is_active = bool(data['is_active'])

        user.save()
        return user

    @staticmethod
    def delete_user(actor, user_id):
        if not user_id:
            raise APIValidationError('id_required', 'id requerido', status=400)
        if user_id == actor.id:
            raise APIValidationError('self_delete', 'No puedes eliminar tu propia cuenta', status=400)
        user = User.objects.filter(id=user_id).first()
        if not user:
            raise APIValidationError('user_not_found', 'Usuario no encontrado', status=404)

        # Si tiene tickets relacionados, mejor desactivar que borrar para preservar FK
        has_tickets = (
            Ticket.objects.filter(Q(requester=user) | Q(assignee=user) | Q(created_by=user)).exists()
            or Comment.objects.filter(user=user).exists()
        )
        if has_tickets:
            user.is_active = False
            user.save(update_fields=['is_active'])
            return {'deactivated': True}
        user.delete()
        return {'deleted': True}
