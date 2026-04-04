from django.contrib import messages
from django.contrib.auth import logout
from django.shortcuts import redirect

from .models import User


class AccessControlMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
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
        return self.get_response(request)
