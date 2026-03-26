from django.shortcuts import redirect
from django.urls import reverse


class PasswordChangeRequiredMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.user.is_authenticated and getattr(request.user, "must_change_password", False):
            allowed_names = {
                "password_change",
                "logout",
            }
            if request.resolver_match is None or request.resolver_match.url_name not in allowed_names:
                return redirect(reverse("password_change"))
        return self.get_response(request)
