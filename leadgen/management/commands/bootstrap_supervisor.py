from django.conf import settings
from django.core.management.base import BaseCommand

from leadgen.models import User


class Command(BaseCommand):
    help = "Create or update the single supervisor account."

    def handle(self, *args, **options):
        email = settings.SUPERVISOR_EMAIL.lower()
        user, created = User.objects.get_or_create(
            email=email,
            defaults={
                "username": email,
                "name": settings.SUPERVISOR_NAME,
                "role": User.ROLE_SUPERVISOR,
                "is_staff": True,
                "is_superuser": True,
            },
        )
        user.name = settings.SUPERVISOR_NAME
        user.role = User.ROLE_SUPERVISOR
        user.is_staff = True
        user.is_superuser = True
        user.must_change_password = False
        user.set_unusable_password()
        user.save()
        action = "Created" if created else "Updated"
        self.stdout.write(self.style.SUCCESS(f"{action} supervisor account: {email}"))
