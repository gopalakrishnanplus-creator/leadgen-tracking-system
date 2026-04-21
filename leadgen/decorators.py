from functools import wraps

from django.contrib.auth.decorators import login_required
from django.http import HttpResponseForbidden


def _supervisor_workspace_allowed(request):
    if not getattr(request, "has_supervisor_workspace_access", False):
        return False
    if getattr(request, "is_dual_workspace_user", False):
        return getattr(request, "current_workspace", None) == "supervisor"
    return True


def role_required(role):
    def decorator(view_func):
        @login_required
        @wraps(view_func)
        def wrapped(request, *args, **kwargs):
            if role == "supervisor" and _supervisor_workspace_allowed(request):
                return view_func(request, *args, **kwargs)
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
            if "supervisor" in set(roles) and _supervisor_workspace_allowed(request):
                return view_func(request, *args, **kwargs)
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
