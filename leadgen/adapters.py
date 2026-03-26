from allauth.exceptions import ImmediateHttpResponse
from allauth.socialaccount.adapter import DefaultSocialAccountAdapter
from django.conf import settings
from django.contrib import messages
from django.shortcuts import redirect

from .models import User


class LeadgenSocialAccountAdapter(DefaultSocialAccountAdapter):
    def _email_is_verified(self, sociallogin):
        value = sociallogin.account.extra_data.get("email_verified")
        return value is True or str(value).lower() == "true"

    def _authorized_user_for_email(self, email):
        normalized = (email or "").strip().lower()
        if not normalized:
            return None
        user = User.objects.filter(email__iexact=normalized).first()
        if user:
            return user
        if normalized == settings.SUPERVISOR_EMAIL.lower():
            user = User(
                email=normalized,
                username=normalized,
                name=settings.SUPERVISOR_NAME,
                role=User.ROLE_SUPERVISOR,
                is_staff=True,
                is_superuser=True,
            )
            user.set_unusable_password()
            user.save()
            return user
        return None

    def pre_social_login(self, request, sociallogin):
        if sociallogin.account.provider != "google":
            messages.error(request, "Only Google sign-in is supported.")
            raise ImmediateHttpResponse(redirect("login"))
        if not self._email_is_verified(sociallogin):
            messages.error(request, "Your Google email address must be verified.")
            raise ImmediateHttpResponse(redirect("login"))
        if sociallogin.is_existing:
            user = sociallogin.user
            if user.email.lower() != (sociallogin.user.email or "").lower():
                messages.error(request, "Account email mismatch detected.")
                raise ImmediateHttpResponse(redirect("login"))
            if not user.is_active:
                messages.error(request, "Your account is inactive.")
                raise ImmediateHttpResponse(redirect("login"))
            if self._authorized_user_for_email(user.email) is None:
                messages.error(request, "Your Google account is not authorized for this system.")
                raise ImmediateHttpResponse(redirect("login"))
            return

        user = self._authorized_user_for_email(sociallogin.user.email)
        if user is None:
            messages.error(request, "Your Google account is not authorized for this system.")
            raise ImmediateHttpResponse(redirect("login"))
        if not user.is_active:
            messages.error(request, "Your account is inactive.")
            raise ImmediateHttpResponse(redirect("login"))
        sociallogin.connect(request, user)

    def is_open_for_signup(self, request, sociallogin):
        return self._email_is_verified(sociallogin) and self._authorized_user_for_email(sociallogin.user.email) is not None
