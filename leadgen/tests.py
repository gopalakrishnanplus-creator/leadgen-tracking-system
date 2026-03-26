from datetime import datetime

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.utils import timezone
from openpyxl import Workbook

from .models import CallImportBatch, Meeting, Prospect, SystemSetting, User
from .services import apply_call_outcome, import_exotel_report, update_meeting_outcome


class LeadgenWorkflowTests(TestCase):
    def setUp(self):
        self.supervisor = User.objects.create(
            email="bhavesh.kataria@inditech.co.in",
            username="bhavesh.kataria@inditech.co.in",
            role=User.ROLE_SUPERVISOR,
            name="Bhavesh Kataria",
            is_staff=True,
            is_superuser=True,
        )
        self.supervisor.set_unusable_password()
        self.supervisor.save()
        self.staff = User.objects.create(
            email="staff@example.com",
            username="staff@example.com",
            role=User.ROLE_STAFF,
            name="Staff User",
            calling_number="+919900000001",
        )
        self.staff.set_unusable_password()
        self.staff.save()
        SystemSetting.load()
        self.prospect = Prospect.objects.create(
            company_name="Acme",
            contact_name="Jane Doe",
            linkedin_url="https://linkedin.com/in/jane",
            phone_number="+919812345678",
            assigned_to=self.staff,
            created_by=self.staff,
            approval_status=Prospect.APPROVAL_ACCEPTED,
            workflow_status=Prospect.WORKFLOW_READY_TO_CALL,
        )

    @override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
    def test_scheduled_outcome_creates_meeting(self):
        meeting = apply_call_outcome(
            self.prospect,
            self.staff,
            {
                "outcome": "scheduled",
                "scheduled_for": datetime(2026, 3, 30, 15, 0),
                "prospect_email": "prospect@example.com",
                "reason": "",
                "follow_up_date": None,
            },
        )
        self.prospect.refresh_from_db()
        self.assertEqual(self.prospect.workflow_status, Prospect.WORKFLOW_SCHEDULED)
        self.assertIsNotNone(meeting)
        self.assertEqual(Meeting.objects.count(), 1)

    @override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
    def test_meeting_did_not_happen_reverts_prospect(self):
        meeting = apply_call_outcome(
            self.prospect,
            self.staff,
            {
                "outcome": "scheduled",
                "scheduled_for": datetime(2026, 3, 30, 15, 0),
                "prospect_email": "prospect@example.com",
                "reason": "",
                "follow_up_date": None,
            },
        )
        update_meeting_outcome(meeting, Meeting.STATUS_DID_NOT_HAPPEN)
        self.prospect.refresh_from_db()
        self.assertEqual(self.prospect.workflow_status, Prospect.WORKFLOW_FOLLOW_UP)
        self.assertEqual(self.prospect.follow_up_reason, "Meeting did not happen")

    def test_import_updates_call_metrics(self):
        workbook = Workbook()
        sheet = workbook.active
        sheet.append(["Call SID", "Start Time", "End Time", "From", "To", "Direction", "Status"])
        sheet.append(
            ["CA1", "2026-03-20 10:00", "2026-03-20 10:05", "+919900000001", "+919812345678", "outbound", "completed"]
        )
        temp_path = self.settings.MEDIA_ROOT / "test_import.xlsx" if hasattr(self.settings, "MEDIA_ROOT") else None
        from io import BytesIO

        buffer = BytesIO()
        workbook.save(buffer)
        upload = SimpleUploadedFile(
            "test_import.xlsx",
            buffer.getvalue(),
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        batch = CallImportBatch.objects.create(
            import_date=timezone.localdate(),
            uploaded_file=upload,
            imported_by=self.supervisor,
        )
        import_exotel_report(batch)
        self.prospect.refresh_from_db()
        self.assertEqual(self.prospect.total_call_attempts, 1)
        self.assertEqual(self.prospect.total_connected_calls, 1)

# Create your tests here.
