from datetime import datetime
from unittest.mock import patch

from django.db import IntegrityError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.utils import timezone
from openpyxl import Workbook

from .adapters import LeadgenSocialAccountAdapter
from .forms import ProspectCreateForm, SalesConversationForm
from .models import CallImportBatch, Meeting, Prospect, SalesConversation, SystemSetting, User
from .services import apply_call_outcome, build_supervisor_report, import_exotel_report, update_meeting_outcome


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
        self.sales_manager, _ = User.objects.get_or_create(
            email="amit@inditech.co.in",
            defaults={
                "username": "amit@inditech.co.in",
                "role": User.ROLE_SALES_MANAGER,
                "name": "Amit",
                "whatsapp_number": "+919900000002",
            },
        )
        self.sales_manager.role = User.ROLE_SALES_MANAGER
        self.sales_manager.name = self.sales_manager.name or "Amit"
        self.sales_manager.whatsapp_number = self.sales_manager.whatsapp_number or "+919900000002"
        self.sales_manager.set_unusable_password()
        self.sales_manager.save()
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

    @override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
    def test_meeting_happened_creates_sales_conversation(self):
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
        update_meeting_outcome(meeting, Meeting.STATUS_HAPPENED, updated_by=self.supervisor)
        sales_conversation = SalesConversation.objects.get(source_meeting=meeting)
        self.assertEqual(sales_conversation.company_name, self.prospect.company_name)
        self.assertEqual(sales_conversation.assigned_sales_manager, self.sales_manager)
        self.assertEqual(sales_conversation.contacts.count(), 1)
        self.assertEqual(sales_conversation.contacts.first().name, self.prospect.contact_name)

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

    def test_only_one_supervisor_allowed(self):
        with self.assertRaises(IntegrityError):
            User.objects.create(
                email="second-supervisor@example.com",
                username="second-supervisor@example.com",
                name="Second Supervisor",
                role=User.ROLE_SUPERVISOR,
                is_staff=True,
                is_superuser=True,
            )

    @override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
    def test_report_builder_returns_staff_metrics(self):
        apply_call_outcome(
            self.prospect,
            self.staff,
            {
                "outcome": "follow_up_to_schedule",
                "scheduled_for": None,
                "prospect_email": "",
                "reason": "Asked to call back next week",
                "follow_up_date": timezone.localdate(),
            },
        )
        self.prospect.status_updates.update(created_at=timezone.make_aware(datetime(2026, 3, 20, 9, 0)))
        workbook = Workbook()
        sheet = workbook.active
        sheet.append(["Call SID", "Start Time", "End Time", "From", "To", "Direction", "Status"])
        sheet.append(
            ["CA2", "2026-03-20 10:00", "2026-03-20 10:04", "+919900000001", "+919812345678", "outbound", "completed"]
        )
        from io import BytesIO

        buffer = BytesIO()
        workbook.save(buffer)
        batch = CallImportBatch.objects.create(
            import_date=timezone.localdate(),
            uploaded_file=SimpleUploadedFile(
                "test_import_2.xlsx",
                buffer.getvalue(),
                content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            ),
            imported_by=self.supervisor,
        )
        import_exotel_report(batch)
        report = build_supervisor_report(datetime(2026, 3, 20).date(), datetime(2026, 3, 20).date(), "Asia/Kolkata")
        self.assertEqual(report["summary"]["attempts"], 1)
        self.assertEqual(report["summary"]["follow_ups"], 1)
        self.assertEqual(report["staff_metrics"][0]["staff"], self.staff)

    @override_settings(
        SUPERVISOR_EMAIL="bhavesh.kataria@inditech.co.in",
        SUPERVISOR_ALLOWED_EMAILS=[
            "gopala.krishnan@inditech.co.in",
            "gkinchina@gmail.com",
            "bhavesh.kataria@inditech.co.in",
        ],
    )
    def test_supervisor_alias_maps_to_single_supervisor_user(self):
        adapter = LeadgenSocialAccountAdapter()
        user = adapter._authorized_user_for_email("gkinchina@gmail.com")
        self.assertEqual(user.pk, self.supervisor.pk)
        user = adapter._authorized_user_for_email("gopala.krishnan@inditech.co.in")
        self.assertEqual(user.pk, self.supervisor.pk)

    def test_login_page_posts_to_google_provider(self):
        response = self.client.get("/login/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'method="post"')
        self.assertContains(response, "Continue with Google")

    def test_home_redirects_sales_manager_to_sales_pipeline(self):
        self.client.force_login(self.sales_manager)
        response = self.client.get("/")
        self.assertRedirects(response, "/sales/")

    def test_prospect_form_accepts_linkedin_without_scheme(self):
        form = ProspectCreateForm(
            data={
                "company_name": "KOKO Coffee Roasters",
                "contact_name": "Bhavesh Kataria",
                "linkedin_url": "www.linkedin.com/in/bhavesh-kataria-456b27148",
                "phone_number": "7770035122",
            },
            instance=Prospect(assigned_to=self.staff, created_by=self.staff),
        )
        self.assertTrue(form.is_valid(), form.errors.as_json())
        self.assertEqual(
            form.cleaned_data["linkedin_url"],
            "https://www.linkedin.com/in/bhavesh-kataria-456b27148",
        )

    @override_settings(ALLOWED_HOSTS=["testserver", "localhost", "127.0.0.1"])
    def test_staff_can_submit_prospect_without_server_error(self):
        self.client.force_login(self.staff)
        response = self.client.post(
            "/staff/prospects/add/",
            {
                "company_name": "KOKO Coffee Roasters",
                "contact_name": "Bhavesh Kataria",
                "linkedin_url": "www.linkedin.com/in/bhavesh-kataria-456b27148",
                "phone_number": "7770035122",
            },
        )
        self.assertEqual(response.status_code, 302)
        created = Prospect.objects.get(phone_number="7770035122")
        self.assertEqual(created.assigned_to, self.staff)
        self.assertEqual(
            created.linkedin_url,
            "https://www.linkedin.com/in/bhavesh-kataria-456b27148",
        )

    def test_scheduled_outcome_still_saves_when_invite_send_fails(self):
        with patch("leadgen.services.send_meeting_invitation", side_effect=RuntimeError("send failed")):
            with self.captureOnCommitCallbacks(execute=True):
                meeting = apply_call_outcome(
                    self.prospect,
                    self.staff,
                    {
                        "outcome": "scheduled",
                        "scheduled_for": datetime(2026, 3, 31, 14, 30),
                        "prospect_email": "prospect@example.com",
                        "reason": "Meeting scheduled",
                        "follow_up_date": None,
                    },
                )
        self.prospect.refresh_from_db()
        meeting.refresh_from_db()
        self.assertEqual(self.prospect.workflow_status, Prospect.WORKFLOW_SCHEDULED)
        self.assertEqual(meeting.status, Meeting.STATUS_SCHEDULED)
        self.assertIsNone(meeting.invite_sent_at)

    @override_settings(ALLOWED_HOSTS=["testserver", "localhost", "127.0.0.1"])
    def test_staff_scheduled_update_does_not_500_when_invite_send_fails(self):
        self.client.force_login(self.staff)
        with patch("leadgen.services.send_meeting_invitation", side_effect=RuntimeError("send failed")):
            with self.captureOnCommitCallbacks(execute=True):
                response = self.client.post(
                    f"/staff/prospects/{self.prospect.pk}/update-call/",
                    {
                        "outcome": "scheduled",
                        "scheduled_for": "2026-03-31T14:30",
                        "prospect_email": "swapna@macleodspharma.in",
                        "reason": "Meeting scheduled",
                        "follow_up_date": "",
                    },
                )
        self.assertEqual(response.status_code, 302)
        self.prospect.refresh_from_db()
        self.assertEqual(self.prospect.workflow_status, Prospect.WORKFLOW_SCHEDULED)
        self.assertEqual(Meeting.objects.count(), 1)

    def test_sales_conversation_form_handles_contacts_and_brands(self):
        form = SalesConversationForm(
            data={
                "company_name": "Acme",
                "assigned_sales_manager": self.sales_manager.pk,
                "conversation_status": SalesConversation.STATUS_ENGAGED,
                "proposal_status": SalesConversation.PROPOSAL_PROPOSAL_NEEDED,
                "comments": "Warm opportunity",
                "brands_input": "Brand A\nBrand B",
                "contact_1_name": "Jane Doe",
                "contact_1_email": "jane@example.com",
                "contact_1_whatsapp": "9999999999",
                "contact_2_name": "",
                "contact_2_email": "",
                "contact_2_whatsapp": "",
                "contact_3_name": "",
                "contact_3_email": "",
                "contact_3_whatsapp": "",
            }
        )
        self.assertTrue(form.is_valid(), form.errors.as_json())
        self.assertEqual(form.cleaned_data["brands_input"], ["Brand A", "Brand B"])
        self.assertEqual(form.cleaned_data["contact_rows"][0]["name"], "Jane Doe")

    def test_sales_manager_pipeline_is_limited_to_assigned_records(self):
        SalesConversation.objects.create(
            company_name="Visible Co",
            assigned_sales_manager=self.sales_manager,
            created_by=self.supervisor,
        )
        other_manager = User.objects.create(
            email="other-sales@example.com",
            username="other-sales@example.com",
            role=User.ROLE_SALES_MANAGER,
            name="Other Sales",
        )
        other_manager.set_unusable_password()
        other_manager.save()
        SalesConversation.objects.create(
            company_name="Hidden Co",
            assigned_sales_manager=other_manager,
            created_by=self.supervisor,
        )
        self.client.force_login(self.sales_manager)
        response = self.client.get("/sales/")
        self.assertContains(response, "Visible Co")
        self.assertNotContains(response, "Hidden Co")

# Create your tests here.
