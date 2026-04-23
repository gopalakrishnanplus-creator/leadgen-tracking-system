from functools import wraps

from django.contrib.auth.decorators import login_required
from django.http import HttpResponseForbidden


def _supervisor_workspace_allowed(request):
    return _workspace_allowed(request, "supervisor")


def _workspace_allowed(request, workspace_name):
    access_flag = f"has_{workspace_name}_workspace_access"
    if not getattr(request, access_flag, False):
        return False
    return getattr(request, "current_workspace", None) == workspace_name


def role_required(role):
    def decorator(view_func):
        @login_required
        @wraps(view_func)
        def wrapped(request, *args, **kwargs):
            workspace_map = {
                "supervisor": "supervisor",
                "staff": "staff",
                "sales_manager": "sales",
                "finance_manager": "finance",
            }
            if role in workspace_map and _workspace_allowed(request, workspace_map[role]):
                return view_func(request, *args, **kwargs)
            if getattr(request, "is_dual_workspace_user", False):
                return HttpResponseForbidden("You do not have access to this page.")
            if getattr(request.user, "role", None) != role:
                return HttpResponseForbidden("You do not have access to this page.")
            return view_func(request, *args, **kwargs)

        return wrapped

    return decorator


def roles_required(*roles):
    def decorator(view_func):
        @login_required
        @wraps(view_func)
        def wrapped(request, *args, **kwargs):
            workspace_map = {
                "supervisor": "supervisor",
                "staff": "staff",
                "sales_manager": "sales",
                "finance_manager": "finance",
            }
            for role in set(roles):
                workspace_name = workspace_map.get(role)
                if workspace_name and _workspace_allowed(request, workspace_name):
                    return view_func(request, *args, **kwargs)
            if getattr(request, "is_dual_workspace_user", False):
                return HttpResponseForbidden("You do not have access to this page.")
            if getattr(request.user, "role", None) not in set(roles):
                return HttpResponseForbidden("You do not have access to this page.")
            return view_func(request, *args, **kwargs)

        return wrapped

    return decorator


def supervisor_access_required(*access_levels):
    def decorator(view_func):
        @login_required
        @wraps(view_func)
        def wrapped(request, *args, **kwargs):
            if not _supervisor_workspace_allowed(request):
                return HttpResponseForbidden("You do not have access to this page.")
            if getattr(request, "supervisor_access_level", None) not in set(access_levels):
                return HttpResponseForbidden("You do not have access to this page.")
            return view_func(request, *args, **kwargs)

        return wrapped

    return decorator
