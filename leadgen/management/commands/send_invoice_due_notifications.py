from django.core.management.base import BaseCommand
from django.utils import timezone

from leadgen.services import send_due_invoice_notifications


class Command(BaseCommand):
    help = "Send invoice due notifications for installments whose invoice date is today."

    def handle(self, *args, **options):
        sent_count = send_due_invoice_notifications(timezone.localdate())
        self.stdout.write(self.style.SUCCESS(f"Invoice due notifications sent: {sent_count}"))
