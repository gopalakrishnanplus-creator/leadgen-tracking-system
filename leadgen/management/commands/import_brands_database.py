from django.core.management.base import BaseCommand

from leadgen.models import User
from leadgen.services import import_pharma_manager_brand_database


class Command(BaseCommand):
    help = "Import the full pharma brands database workbook into the marketing database."

    def add_arguments(self, parser):
        parser.add_argument("workbook_path", help="Path to the brands database .xlsx file.")
        parser.add_argument(
            "--uploaded-by-email",
            default="",
            help="Optional existing user email to record as the importer.",
        )

    def handle(self, *args, **options):
        uploaded_by = None
        uploaded_by_email = (options["uploaded_by_email"] or "").strip().lower()
        if uploaded_by_email:
            uploaded_by = User.objects.filter(email__iexact=uploaded_by_email, is_active=True).first()
            if uploaded_by is None:
                self.stderr.write(self.style.WARNING(f"No active user found for {uploaded_by_email}; importer left blank."))

        result = import_pharma_manager_brand_database(options["workbook_path"], uploaded_by=uploaded_by)
        self.stdout.write(
            self.style.SUCCESS(
                "Brands database import complete: "
                f"rows={result['total_rows']}, "
                f"created={result['created_count']}, "
                f"updated={result['updated_count']}, "
                f"skipped={result['skipped_count']}"
            )
        )
