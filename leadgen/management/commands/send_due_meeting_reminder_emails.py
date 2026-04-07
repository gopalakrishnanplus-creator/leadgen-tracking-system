from django.core.management.base import BaseCommand

from leadgen.services import send_due_meeting_reminder_emails


class Command(BaseCommand):
    help = "Send automated 24-hour and same-day reminder emails for scheduled meetings."

    def handle(self, *args, **options):
        sent_count = send_due_meeting_reminder_emails()
        self.stdout.write(self.style.SUCCESS(f"Meeting reminder emails sent: {sent_count}"))
