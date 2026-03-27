from allauth.core.exceptions import ImmediateHttpResponse
from allauth.socialaccount.adapter import DefaultSocialAccountAdapter
from django.conf import settings
from django.contrib import messages
from django.shortcuts import redirect

from .models import User


class LeadgenSocialAccountAdapter(DefaultSocialAccountAdapter):
    def _normalized_email(self, sociallogin):
        return (
            sociallogin.account.extra_data.get("email")
            or sociallogin.user.email
            or ""
        ).strip().lower()

    def _email_is_verified(self, sociallogin):
        value = sociallogin.account.extra_data.get("email_verified")
        return value is True or str(value).lower() == "true"

    def _supervisor_user(self):
        user = User.objects.filter(role=User.ROLE_SUPERVISOR).first()
        if user:
            return user
        user = User(
            email=settings.SUPERVISOR_EMAIL,
            username=settings.SUPERVISOR_EMAIL,
            name=settings.SUPERVISOR_NAME,
            role=User.ROLE_SUPERVISOR,
            is_staff=True,
            is_superuser=True,
        )
        user.set_unusable_password()
        user.save()
        return user

    def _authorized_user_for_email(self, email):
        normalized = (email or "").strip().lower()
        if not normalized:
            return None
        if normalized in settings.SUPERVISOR_ALLOWED_EMAILS:
            return self._supervisor_user()
        user = User.objects.filter(email__iexact=normalized).first()
        if user:
            return user
        return None

    def pre_social_login(self, request, sociallogin):
        if sociallogin.account.provider != "google":
            messages.error(request, "Only Google sign-in is supported.")
            raise ImmediateHttpResponse(redirect("login"))
        if not self._email_is_verified(sociallogin):
            messages.error(request, "Your Google email address must be verified.")
            raise ImmediateHttpResponse(redirect("login"))
        external_email = self._normalized_email(sociallogin)
        if sociallogin.is_existing:
            user = sociallogin.user
            if not user.is_active:
                messages.error(request, "Your account is inactive.")
                raise ImmediateHttpResponse(redirect("login"))
            if user.is_supervisor:
                if external_email not in settings.SUPERVISOR_ALLOWED_EMAILS:
                    messages.error(request, "This Google account is not authorized for supervisor access.")
                    raise ImmediateHttpResponse(redirect("login"))
                return
            if self._authorized_user_for_email(external_email) is None or user.email.lower() != external_email:
                messages.error(request, "Your Google account is not authorized for this system.")
                raise ImmediateHttpResponse(redirect("login"))
            return

        user = self._authorized_user_for_email(external_email)
        if user is None:
            messages.error(request, "Your Google account is not authorized for this system.")
            raise ImmediateHttpResponse(redirect("login"))
        if not user.is_active:
            messages.error(request, "Your account is inactive.")
            raise ImmediateHttpResponse(redirect("login"))
        sociallogin.connect(request, user)

    def is_open_for_signup(self, request, sociallogin):
        return self._email_is_verified(sociallogin) and self._authorized_user_for_email(self._normalized_email(sociallogin)) is not None
