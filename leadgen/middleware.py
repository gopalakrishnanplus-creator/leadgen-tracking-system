from django.contrib import messages
from django.contrib.auth import logout
from django.shortcuts import redirect

from .models import SupervisorAccessEmail, User


class AccessControlMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request.supervisor_access = None
        request.supervisor_access_level = None
        request.is_system_admin = False
        request.is_leadgen_supervisor = False
        request.can_manage_users = False
        if request.user.is_authenticated:
            if not request.user.is_active:
                logout(request)
                messages.error(request, "Your account is inactive.")
                return redirect("login")
            if request.user.role not in {
                User.ROLE_SUPERVISOR,
                User.ROLE_STAFF,
                User.ROLE_SALES_MANAGER,
                User.ROLE_FINANCE_MANAGER,
                }:
                logout(request)
                messages.error(request, "Your account role is invalid.")
                return redirect("login")
            if request.user.is_supervisor:
                access_email = (request.session.get("supervisor_access_email") or request.user.email or "").strip().lower()
                supervisor_access = SupervisorAccessEmail.objects.filter(
                    email=access_email,
                    is_active=True,
                ).first()
                if supervisor_access:
                    request.supervisor_access = supervisor_access
                    request.supervisor_access_level = supervisor_access.access_level
                    request.is_system_admin = supervisor_access.is_system_admin
                    request.is_leadgen_supervisor = supervisor_access.is_leadgen_supervisor
                    request.can_manage_users = True
        return self.get_response(request)
