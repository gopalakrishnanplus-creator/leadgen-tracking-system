from allauth.core.exceptions import ImmediateHttpResponse
from allauth.socialaccount.adapter import DefaultSocialAccountAdapter
from django.conf import settings
from django.contrib import messages
from django.shortcuts import redirect

from .models import SupervisorAccessEmail, User


class LeadgenSocialAccountAdapter(DefaultSocialAccountAdapter):
    def _supervisor_access_map(self):
        database_entries = SupervisorAccessEmail.objects.filter(is_active=True)
        if database_entries.exists():
            return {entry.email: entry.access_level for entry in database_entries}

        access_map = {}
        system_admin_emails = getattr(settings, "SYSTEM_ADMIN_EMAILS", ["gopala.krishnan@inditech.co.in"])
        for email in system_admin_emails:
            normalized = (email or "").strip().lower()
            if normalized:
                access_map[normalized] = SupervisorAccessEmail.ACCESS_SYSTEM_ADMIN

        configured_supervisor_emails = {
            settings.SUPERVISOR_EMAIL,
            *(email.strip().lower() for email in settings.SUPERVISOR_ALLOWED_EMAILS if email),
        }
        for email in configured_supervisor_emails:
            normalized = (email or "").strip().lower()
            if normalized:
                access_map.setdefault(normalized, SupervisorAccessEmail.ACCESS_LEADGEN_SUPERVISOR)
        return access_map

    def _supervisor_allowed_emails(self):
        return set(self._supervisor_access_map().keys())

    def _remember_supervisor_access(self, request, email):
        normalized = (email or "").strip().lower()
        access_level = self._supervisor_access_map().get(normalized)
        if access_level:
            request.session["supervisor_access_email"] = normalized
            request.session["supervisor_access_level"] = access_level
        else:
            request.session.pop("supervisor_access_email", None)
            request.session.pop("supervisor_access_level", None)

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
        if normalized in self._supervisor_allowed_emails():
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
                if external_email not in self._supervisor_allowed_emails():
                    messages.error(request, "This Google account is not authorized for supervisor access.")
                    raise ImmediateHttpResponse(redirect("login"))
                self._remember_supervisor_access(request, external_email)
                return
            if self._authorized_user_for_email(external_email) is None or user.email.lower() != external_email:
                messages.error(request, "Your Google account is not authorized for this system.")
                raise ImmediateHttpResponse(redirect("login"))
            self._remember_supervisor_access(request, None)
            return

        user = self._authorized_user_for_email(external_email)
        if user is None:
            messages.error(request, "Your Google account is not authorized for this system.")
            raise ImmediateHttpResponse(redirect("login"))
        if not user.is_active:
            messages.error(request, "Your account is inactive.")
            raise ImmediateHttpResponse(redirect("login"))
        if user.is_supervisor:
            self._remember_supervisor_access(request, external_email)
        else:
            self._remember_supervisor_access(request, None)
        sociallogin.connect(request, user)

    def is_open_for_signup(self, request, sociallogin):
        return self._email_is_verified(sociallogin) and self._authorized_user_for_email(self._normalized_email(sociallogin)) is not None
