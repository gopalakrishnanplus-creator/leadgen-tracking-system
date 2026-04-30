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
        request.has_staff_workspace_access = False
        request.has_sales_workspace_access = False
        request.has_finance_workspace_access = False
        request.has_business_workspace_access = False
        request.has_marketing_workspace_access = False
        request.is_dual_workspace_user = False
        request.available_workspaces = []
        request.staff_workspace_user = None
        request.sales_workspace_user = None
        request.finance_workspace_user = None
        request.business_workspace_user = None
        request.marketing_workspace_user = None
        request.login_email = None
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
                User.ROLE_BUSINESS_MANAGER,
                User.ROLE_MARKETING_MANAGER,
                }:
                logout(request)
                messages.error(request, "Your account role is invalid.")
                return redirect("login")
            access_email = (request.session.get("supervisor_access_email") or "").strip().lower()
            login_email = (access_email or request.user.email or "").strip().lower()
            request.login_email = login_email
            supervisor_access = SupervisorAccessEmail.objects.filter(
                email=access_email,
                is_active=True,
            ).first()
            if supervisor_access:
                request.supervisor_access = supervisor_access
                request.supervisor_access_level = supervisor_access.access_level
                request.is_system_admin = supervisor_access.is_system_admin
                request.is_leadgen_supervisor = supervisor_access.is_leadgen_supervisor
            workspace_users = User.objects.filter(email__iexact=login_email, is_active=True)
            request.staff_workspace_user = workspace_users.filter(role=User.ROLE_STAFF).order_by("pk").first()
            request.sales_workspace_user = workspace_users.filter(role=User.ROLE_SALES_MANAGER).order_by("pk").first()
            request.finance_workspace_user = workspace_users.filter(role=User.ROLE_FINANCE_MANAGER).order_by("pk").first()
            request.business_workspace_user = workspace_users.filter(role=User.ROLE_BUSINESS_MANAGER).order_by("pk").first()
            request.marketing_workspace_user = workspace_users.filter(role=User.ROLE_MARKETING_MANAGER).order_by("pk").first()

            request.has_supervisor_workspace_access = request.user.is_supervisor or supervisor_access is not None
            request.has_staff_workspace_access = request.staff_workspace_user is not None
            request.has_sales_workspace_access = request.sales_workspace_user is not None
            request.has_finance_workspace_access = request.finance_workspace_user is not None
            request.has_business_workspace_access = request.business_workspace_user is not None
            request.has_marketing_workspace_access = request.marketing_workspace_user is not None

            available_workspaces = []
            if request.has_supervisor_workspace_access:
                available_workspaces.append("supervisor")
            if request.has_staff_workspace_access:
                available_workspaces.append("staff")
            if request.has_sales_workspace_access:
                available_workspaces.append("sales")
            if request.has_finance_workspace_access:
                available_workspaces.append("finance")
            if request.has_business_workspace_access:
                available_workspaces.append("business")
            if request.has_marketing_workspace_access:
                available_workspaces.append("marketing")
            request.available_workspaces = available_workspaces
            request.is_dual_workspace_user = len(available_workspaces) > 1

            if request.is_dual_workspace_user:
                selected_workspace = request.session.get("workspace_mode")
                if selected_workspace in set(available_workspaces):
                    request.current_workspace = selected_workspace
                else:
                    request.current_workspace = "chooser"
            elif request.has_supervisor_workspace_access:
                request.current_workspace = "supervisor"
            elif request.has_staff_workspace_access:
                request.current_workspace = "staff"
            elif request.has_sales_workspace_access:
                request.current_workspace = "sales"
            elif request.has_finance_workspace_access:
                request.current_workspace = "finance"
            elif request.has_business_workspace_access:
                request.current_workspace = "business"
            elif request.has_marketing_workspace_access:
                request.current_workspace = "marketing"
            else:
                request.current_workspace = None
            request.can_manage_users = request.current_workspace == "supervisor" and request.has_supervisor_workspace_access
        return self.get_response(request)
