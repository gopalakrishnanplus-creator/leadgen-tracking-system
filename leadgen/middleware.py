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
        request.has_supervisor_workspace_access = False
        request.has_sales_workspace_access = False
        request.is_dual_workspace_user = False
        request.current_workspace = None
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
            request.has_supervisor_workspace_access = request.user.is_supervisor or supervisor_access is not None
            request.has_sales_workspace_access = request.user.is_sales_manager
            request.is_dual_workspace_user = (
                request.has_supervisor_workspace_access and request.has_sales_workspace_access
            )
            if request.is_dual_workspace_user:
                selected_workspace = request.session.get("workspace_mode")
                if selected_workspace in {"supervisor", "sales"}:
                    request.current_workspace = selected_workspace
                else:
                    request.current_workspace = "chooser"
            elif request.has_supervisor_workspace_access:
                request.current_workspace = "supervisor"
            elif request.has_sales_workspace_access:
                request.current_workspace = "sales"
            elif request.user.is_finance_manager:
                request.current_workspace = "finance"
            else:
                request.current_workspace = "staff"
            request.can_manage_users = request.current_workspace == "supervisor" and request.has_supervisor_workspace_access
        return self.get_response(request)
