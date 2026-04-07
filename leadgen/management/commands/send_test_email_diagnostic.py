import json

from django.core.management.base import BaseCommand, CommandError

from leadgen.services import send_test_email_diagnostic


class Command(BaseCommand):
    help = "Send a diagnostic test email and print structured delivery diagnostics."

    def add_arguments(self, parser):
        parser.add_argument(
            "--to",
            default="gopala.krishnan@inditech.co.in",
            help="Recipient email address for the diagnostic test email.",
        )

    def handle(self, *args, **options):
        diagnostics = send_test_email_diagnostic(options["to"])
        self.stdout.write(json.dumps(diagnostics, indent=2, sort_keys=True))
        if not diagnostics.get("success"):
            raise CommandError("Diagnostic email failed. See JSON output above for details.")
        self.stdout.write(self.style.SUCCESS("Diagnostic email sent successfully."))
