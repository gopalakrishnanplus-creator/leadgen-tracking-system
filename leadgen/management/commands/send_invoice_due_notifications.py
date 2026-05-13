from django.core.management.base import BaseCommand
from leadgen.services import business_localtime, send_due_invoice_notifications


class Command(BaseCommand):
    help = "Send invoice due notifications for installments whose invoice date is today and whose 9 AM business-time reminder is due."

    def add_arguments(self, parser):
        parser.add_argument(
            "--force",
            action="store_true",
            help="Send today's due invoice notifications immediately, even before the 9 AM business-time reminder threshold.",
        )

    def handle(self, *args, **options):
        sent_count = send_due_invoice_notifications(force=options["force"])
        local_now = business_localtime()
        self.stdout.write(
            self.style.SUCCESS(
                f"Invoice due notifications sent: {sent_count} "
                f"(business time: {local_now:%Y-%m-%d %H:%M:%S %Z})"
            )
        )
