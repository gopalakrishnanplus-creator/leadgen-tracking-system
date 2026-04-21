from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch
from zoneinfo import ZoneInfo

from allauth.socialaccount.models import SocialAccount
from django.db import IntegrityError
from django.core import mail
from django.core.files.uploadedfile import SimpleUploadedFile
from django.contrib.sessions.middleware import SessionMiddleware
from django.test import RequestFactory
from django.test import TestCase, override_settings
from django.utils import timezone
from django.utils.datastructures import MultiValueDict
from openpyxl import Workbook

from .adapters import LeadgenSocialAccountAdapter, SHARED_SUPERVISOR_USER_EMAIL
from .forms import (
    ContractCollectionForm,
    FinanceManagerCreateForm,
    MeetingStatusUpdateForm,
    ProspectCreateForm,
    SalesConversationForm,
    SalesManagerCreateForm,
)
from .models import (
    CallImportBatch,
    CallLog,
    ContractCollection,
    ContractCollectionInstallment,
    Meeting,
    MeetingReminder,
    Prospect,
    ProspectStatusUpdate,
    SalesConversation,
    SupervisorAccessEmail,
    SystemSetting,
    User,
)
from .services import (
    apply_call_outcome,
    backfill_sales_conversations_from_happened_meetings,
    business_localdate,
    build_calendar_invite,
    build_daily_target_report,
    build_pending_collections,
    build_reminder_dashboard,
    build_supervisor_report,
    get_or_create_contract_collection_from_sales_conversation,
    import_exotel_report,
    log_whatsapp_reminder,
    send_email,
    send_due_meeting_reminder_emails,
    send_meeting_invitation,
    send_test_email_diagnostic,
    send_due_invoice_notifications,
    reschedule_meeting,
    sync_contract_collection_data,
    update_meeting_outcome,
)


class LeadgenWorkflowTests(TestCase):
    def setUp(self):
        self.supervisor = User.objects.create(
            email=SHARED_SUPERVISOR_USER_EMAIL,
            username=SHARED_SUPERVISOR_USER_EMAIL,
            role=User.ROLE_SUPERVISOR,
            name="Bhavesh Kataria",
            is_staff=True,
            is_superuser=True,
        )
        self.supervisor.set_unusable_password()
        self.supervisor.save()
        for email in [
            "bhavesh.kataria@inditech.co.in",
            "gopala.krishnan@inditech.co.in",
            "gkinchina@gmail.com",
            "leesaamit@gmail.com",
            "vamshi.alle@inditech.co.in",
        ]:
            SupervisorAccessEmail.objects.update_or_create(
                email=email,
                defaults={"is_active": True},
            )
        self.staff = User.objects.create(
            email="staff@example.com",
            username="staff@example.com",
            role=User.ROLE_STAFF,
            name="Staff User",
            calling_number="+919900000001",
        )
        self.staff.set_unusable_password()
        self.staff.save()
        self.other_staff = User.objects.create(
            email="otherstaff@example.com",
            username="otherstaff@example.com",
            role=User.ROLE_STAFF,
            name="Other Staff",
            calling_number="+919900000003",
        )
        self.other_staff.set_unusable_password()
        self.other_staff.save()
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
        self.finance_manager = User.objects.create(
            email="finance@example.com",
            username="finance@example.com",
            role=User.ROLE_FINANCE_MANAGER,
            name="Finance User",
        )
        self.finance_manager.set_unusable_password()
        self.finance_manager.save()
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

    def _force_login_as_supervisor_access(self, email):
        self.client.force_login(self.supervisor)
        session = self.client.session
        access_email = SupervisorAccessEmail.objects.get(email=email)
        session["supervisor_access_email"] = access_email.email
        session["supervisor_access_level"] = access_email.access_level
        session.save()

    def _request_with_session(self):
        request = RequestFactory().get("/")
        middleware = SessionMiddleware(lambda r: None)
        middleware.process_request(request)
        request.session.save()
        return request

    def _force_login_with_workspace_access(self, user, email, workspace=None):
        self.client.force_login(user)
        session = self.client.session
        access_email = SupervisorAccessEmail.objects.get(email=email)
        session["supervisor_access_email"] = access_email.email
        session["supervisor_access_level"] = access_email.access_level
        if workspace:
            session["workspace_mode"] = workspace
        else:
            session.pop("workspace_mode", None)
        session.save()

    def _import_rows(self, rows):
        workbook = Workbook()
        sheet = workbook.active
        sheet.append(["Call SID", "Start Time", "End Time", "From", "To", "Direction", "Status"])
        for row in rows:
            sheet.append(row)
        from io import BytesIO

        buffer = BytesIO()
        workbook.save(buffer)
        batch = CallImportBatch.objects.create(
            import_date=timezone.localdate(),
            uploaded_file=SimpleUploadedFile(
                "test_import.xlsx",
                buffer.getvalue(),
                content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            ),
            imported_by=self.supervisor,
        )
        import_exotel_report(batch)
        return batch

    @override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
    def test_scheduled_outcome_creates_meeting(self):
        meeting = apply_call_outcome(
            self.prospect,
            self.staff,
            {
                "outcome": "scheduled",
                "scheduled_for": datetime(2026, 3, 30, 15, 0),
                "prospect_email": "prospect@example.com",
                "meeting_platform": Meeting.PLATFORM_ZOOM,
                "reason": "",
                "follow_up_date": None,
            },
        )
        self.prospect.refresh_from_db()
        self.assertEqual(self.prospect.workflow_status, Prospect.WORKFLOW_SCHEDULED)
        self.assertIsNotNone(meeting)
        self.assertEqual(Meeting.objects.count(), 1)
        self.assertEqual(meeting.meeting_platform, Meeting.PLATFORM_ZOOM)
        self.assertEqual(meeting.meeting_link, "https://us02web.zoom.us/j/81585703258?pwd=BlJ5Tbhbqo9P2HNPjsQDLtJZDjB7H9.1")

    @override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend", SENDGRID_API_KEY="")
    def test_short_notice_meeting_sends_day_before_reminder_immediately(self):
        fixed_now = timezone.make_aware(datetime(2026, 3, 30, 9, 0), ZoneInfo("Asia/Kolkata"))
        with patch("django.utils.timezone.now", return_value=fixed_now):
            with self.captureOnCommitCallbacks(execute=True):
                meeting = apply_call_outcome(
                    self.prospect,
                    self.staff,
                    {
                        "outcome": "scheduled",
                        "scheduled_for": datetime(2026, 3, 30, 15, 0),
                        "prospect_email": "prospect@example.com",
                        "meeting_platform": Meeting.PLATFORM_TEAMS,
                        "reason": "",
                        "follow_up_date": None,
                    },
                )

        self.assertEqual(
            MeetingReminder.objects.filter(
                meeting=meeting,
                reminder_type=MeetingReminder.TYPE_EMAIL_DAY_BEFORE,
            ).count(),
            1,
        )
        self.assertEqual(len(mail.outbox), 2)
        self.assertEqual(mail.outbox[0].subject, "20-min: Outcome-Linked Campaign – Discussion with Acme")
        self.assertEqual(mail.outbox[1].subject, "20-min: Outcome-Linked Campaign - Discussion with Acme")

    @override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend", SENDGRID_API_KEY="")
    def test_meeting_within_one_hour_does_not_send_day_before_reminder_immediately(self):
        fixed_now = timezone.make_aware(datetime(2026, 3, 30, 14, 30), ZoneInfo("Asia/Kolkata"))
        with patch("django.utils.timezone.now", return_value=fixed_now):
            with self.captureOnCommitCallbacks(execute=True):
                meeting = apply_call_outcome(
                    self.prospect,
                    self.staff,
                    {
                        "outcome": "scheduled",
                        "scheduled_for": datetime(2026, 3, 30, 15, 0),
                        "prospect_email": "prospect@example.com",
                        "meeting_platform": Meeting.PLATFORM_TEAMS,
                        "reason": "",
                        "follow_up_date": None,
                    },
                )

        self.assertEqual(
            MeetingReminder.objects.filter(
                meeting=meeting,
                reminder_type=MeetingReminder.TYPE_EMAIL_DAY_BEFORE,
            ).count(),
            0,
        )
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].subject, "20-min: Outcome-Linked Campaign – Discussion with Acme")

    def test_scheduled_outcome_recipients_include_sales_manager_supervisor_staff_and_prospect(self):
        meeting = apply_call_outcome(
            self.prospect,
            self.staff,
            {
                "outcome": "scheduled",
                "scheduled_for": datetime(2026, 3, 30, 15, 0),
                "prospect_email": "prospect@example.com",
                "meeting_platform": Meeting.PLATFORM_TEAMS,
                "reason": "",
                "follow_up_date": None,
            },
        )
        self.assertCountEqual(
            meeting.recipient_emails,
            [
                "prospect@example.com",
                "bhavesh.kataria@inditech.co.in",
                "staff@example.com",
                "amit@inditech.co.in",
            ],
        )

    @override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
    def test_meeting_did_not_happen_reverts_prospect(self):
        meeting = apply_call_outcome(
            self.prospect,
            self.staff,
            {
                "outcome": "scheduled",
                "scheduled_for": datetime(2026, 3, 30, 15, 0),
                "prospect_email": "prospect@example.com",
                "meeting_platform": Meeting.PLATFORM_TEAMS,
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
                "meeting_platform": Meeting.PLATFORM_TEAMS,
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

    def test_backfill_sales_conversations_from_happened_meetings_creates_missing_records(self):
        meeting = Meeting.objects.create(
            prospect=self.prospect,
            scheduled_by=self.staff,
            scheduled_for=timezone.now(),
            prospect_email="prospect@example.com",
            status=Meeting.STATUS_HAPPENED,
        )
        self.prospect.workflow_status = Prospect.WORKFLOW_MEETING_HAPPENED
        self.prospect.save(update_fields=["workflow_status", "updated_at"])

        created_count = backfill_sales_conversations_from_happened_meetings()

        self.assertEqual(created_count, 1)
        sales_conversation = SalesConversation.objects.get(source_meeting=meeting)
        self.assertEqual(sales_conversation.company_name, self.prospect.company_name)
        self.assertEqual(sales_conversation.contacts.first().email, "prospect@example.com")

    def test_backfill_sales_conversations_from_happened_meetings_is_idempotent(self):
        meeting = apply_call_outcome(
            self.prospect,
            self.staff,
            {
                "outcome": "scheduled",
                "scheduled_for": datetime(2026, 3, 30, 15, 0),
                "prospect_email": "prospect@example.com",
                "meeting_platform": Meeting.PLATFORM_TEAMS,
                "reason": "",
                "follow_up_date": None,
            },
        )
        update_meeting_outcome(meeting, Meeting.STATUS_HAPPENED, updated_by=self.supervisor)

        created_count = backfill_sales_conversations_from_happened_meetings()

        self.assertEqual(created_count, 0)
        self.assertEqual(SalesConversation.objects.filter(source_meeting=meeting).count(), 1)

    def test_sales_manager_can_add_sales_conversation_directly(self):
        self.client.force_login(self.sales_manager)
        response = self.client.get("/sales/add/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Add sales conversation")

    def test_dual_workspace_user_gets_workspace_choice_on_home(self):
        SupervisorAccessEmail.objects.update_or_create(
            email="amit@inditech.co.in",
            defaults={
                "is_active": True,
                "access_level": SupervisorAccessEmail.ACCESS_LEADGEN_SUPERVISOR,
            },
        )
        self._force_login_with_workspace_access(self.sales_manager, "amit@inditech.co.in")
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Choose which dashboard to use")
        self.assertContains(response, "Use as lead gen supervisor")
        self.assertContains(response, "Use as sales manager")

    def test_dual_workspace_user_can_open_supervisor_dashboard_and_see_all_sales_pipeline_records(self):
        SupervisorAccessEmail.objects.update_or_create(
            email="amit@inditech.co.in",
            defaults={
                "is_active": True,
                "access_level": SupervisorAccessEmail.ACCESS_LEADGEN_SUPERVISOR,
            },
        )
        other_sales_manager = User.objects.create(
            email="other.sales@example.com",
            username="other.sales@example.com",
            role=User.ROLE_SALES_MANAGER,
            name="Other Sales",
        )
        other_sales_manager.set_unusable_password()
        other_sales_manager.save()
        own_conversation = SalesConversation.objects.create(
            company_name="Own Assigned Co",
            assigned_sales_manager=self.sales_manager,
            created_by=self.supervisor,
        )
        other_conversation = SalesConversation.objects.create(
            company_name="Other Assigned Co",
            assigned_sales_manager=other_sales_manager,
            created_by=self.supervisor,
        )
        self._force_login_with_workspace_access(self.sales_manager, "amit@inditech.co.in", workspace="supervisor")
        response = self.client.get("/supervisor/")
        self.assertEqual(response.status_code, 200)
        sales_response = self.client.get("/sales/")
        self.assertEqual(sales_response.status_code, 200)
        self.assertContains(sales_response, own_conversation.company_name)
        self.assertContains(sales_response, other_conversation.company_name)
        self.assertContains(sales_response, "Edit details")

    def test_dual_workspace_user_can_switch_to_sales_dashboard(self):
        SupervisorAccessEmail.objects.update_or_create(
            email="amit@inditech.co.in",
            defaults={
                "is_active": True,
                "access_level": SupervisorAccessEmail.ACCESS_LEADGEN_SUPERVISOR,
            },
        )
        self._force_login_with_workspace_access(self.sales_manager, "amit@inditech.co.in")
        response = self.client.get("/workspace/sales/")
        self.assertRedirects(response, "/sales/")
        dashboard_response = self.client.get("/sales/")
        self.assertEqual(dashboard_response.status_code, 200)
        self.assertContains(dashboard_response, "Sales pipeline")

    def test_sales_conversation_update_page_has_explicit_save_changes_button(self):
        conversation = SalesConversation.objects.create(
            company_name="Editable Pipeline Co",
            assigned_sales_manager=self.sales_manager,
            created_by=self.supervisor,
        )
        self.client.force_login(self.supervisor)
        response = self.client.get(f"/sales/{conversation.pk}/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Use this page to edit the saved conversation details")
        self.assertContains(response, "Save changes")

    def test_meeting_status_form_requires_new_datetime_for_reschedule(self):
        meeting = apply_call_outcome(
            self.prospect,
            self.staff,
            {
                "outcome": "scheduled",
                "scheduled_for": datetime(2026, 4, 23, 17, 30),
                "prospect_email": "prospect@example.com",
                "meeting_platform": Meeting.PLATFORM_TEAMS,
                "reason": "",
                "follow_up_date": None,
            },
        )
        form = MeetingStatusUpdateForm(data={"status": Meeting.STATUS_RESCHEDULED}, instance=meeting)
        self.assertFalse(form.is_valid())
        self.assertIn("rescheduled_for", form.errors)

    @override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend", SENDGRID_API_KEY="")
    def test_reschedule_meeting_creates_new_scheduled_meeting_and_new_invite_cycle(self):
        meeting = apply_call_outcome(
            self.prospect,
            self.staff,
            {
                "outcome": "scheduled",
                "scheduled_for": datetime(2026, 4, 23, 17, 30),
                "prospect_email": "prospect@example.com",
                "meeting_platform": Meeting.PLATFORM_TEAMS,
                "reason": "",
                "follow_up_date": None,
            },
        )
        MeetingReminder.objects.create(
            meeting=meeting,
            reminder_type=MeetingReminder.TYPE_EMAIL_DAY_BEFORE,
            recipient_number="",
            sent_at=timezone.now(),
        )

        with self.captureOnCommitCallbacks(execute=True):
            new_meeting = reschedule_meeting(
                meeting,
                timezone.make_aware(datetime(2026, 4, 24, 12, 0)),
                updated_by=self.supervisor,
            )

        meeting.refresh_from_db()
        new_meeting.refresh_from_db()
        self.prospect.refresh_from_db()
        self.assertEqual(meeting.status, Meeting.STATUS_RESCHEDULED)
        self.assertEqual(new_meeting.status, Meeting.STATUS_SCHEDULED)
        self.assertEqual(new_meeting.prospect, self.prospect)
        self.assertEqual(new_meeting.scheduled_by, self.staff)
        self.assertEqual(new_meeting.prospect_email, "prospect@example.com")
        self.assertEqual(new_meeting.meeting_platform, Meeting.PLATFORM_TEAMS)
        self.assertEqual(new_meeting.reminders.count(), 0)
        self.assertEqual(meeting.reminders.count(), 1)
        self.assertEqual(self.prospect.workflow_status, Prospect.WORKFLOW_SCHEDULED)
        self.assertIsNotNone(new_meeting.invite_sent_at)
        self.assertEqual(Meeting.objects.count(), 2)
        self.assertEqual(
            ProspectStatusUpdate.objects.filter(
                prospect=self.prospect,
                outcome=ProspectStatusUpdate.OUTCOME_SCHEDULED,
            ).count(),
            2,
        )
        self.assertEqual(len(mail.outbox), 1)

    @override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend", SENDGRID_API_KEY="")
    def test_reschedule_meeting_within_24_hours_sends_day_before_reminder_immediately(self):
        initial_now = timezone.make_aware(datetime(2026, 4, 22, 9, 0), ZoneInfo("Asia/Kolkata"))
        with patch("django.utils.timezone.now", return_value=initial_now):
            with self.captureOnCommitCallbacks(execute=True):
                meeting = apply_call_outcome(
                    self.prospect,
                    self.staff,
                    {
                        "outcome": "scheduled",
                        "scheduled_for": datetime(2026, 4, 24, 17, 30),
                        "prospect_email": "prospect@example.com",
                        "meeting_platform": Meeting.PLATFORM_TEAMS,
                        "reason": "",
                        "follow_up_date": None,
                    },
                )

        mail.outbox = []
        reschedule_now = timezone.make_aware(datetime(2026, 4, 23, 13, 0), ZoneInfo("Asia/Kolkata"))
        with patch("django.utils.timezone.now", return_value=reschedule_now):
            with self.captureOnCommitCallbacks(execute=True):
                new_meeting = reschedule_meeting(
                    meeting,
                    timezone.make_aware(datetime(2026, 4, 23, 17, 30)),
                    updated_by=self.supervisor,
                )

        self.assertEqual(
            MeetingReminder.objects.filter(
                meeting=new_meeting,
                reminder_type=MeetingReminder.TYPE_EMAIL_DAY_BEFORE,
            ).count(),
            1,
        )
        self.assertEqual(len(mail.outbox), 2)
        self.assertEqual(mail.outbox[0].subject, "20-min: Outcome-Linked Campaign – Discussion with Acme")
        self.assertEqual(mail.outbox[1].subject, "20-min: Outcome-Linked Campaign - Discussion with Acme")

    @override_settings(ALLOWED_HOSTS=["testserver", "localhost", "127.0.0.1"])
    @override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend", SENDGRID_API_KEY="")
    def test_supervisor_can_reschedule_meeting_from_status_screen(self):
        meeting = apply_call_outcome(
            self.prospect,
            self.staff,
            {
                "outcome": "scheduled",
                "scheduled_for": datetime(2026, 4, 23, 17, 30),
                "prospect_email": "prospect@example.com",
                "meeting_platform": Meeting.PLATFORM_ZOOM,
                "reason": "",
                "follow_up_date": None,
            },
        )
        self.client.force_login(self.supervisor)
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(
                f"/supervisor/meetings/{meeting.pk}/status/",
                {
                    "status": Meeting.STATUS_RESCHEDULED,
                    "rescheduled_for": "2026-04-24T12:00",
                },
            )
        self.assertRedirects(response, "/supervisor/meetings/")
        meeting.refresh_from_db()
        self.assertEqual(meeting.status, Meeting.STATUS_RESCHEDULED)
        new_meeting = Meeting.objects.exclude(pk=meeting.pk).get()
        self.assertEqual(new_meeting.status, Meeting.STATUS_SCHEDULED)
        self.assertEqual(new_meeting.meeting_platform, Meeting.PLATFORM_ZOOM)
        self.assertEqual(new_meeting.reminders.count(), 0)

    def test_import_updates_call_metrics(self):
        self._import_rows(
            [
                [
                    "CA1",
                    "2026-03-20 10:00",
                    "2026-03-20 10:05",
                    "+919900000001",
                    "+919812345678",
                    "outbound",
                    "completed",
                ]
            ]
        )
        self.prospect.refresh_from_db()
        self.assertEqual(self.prospect.total_call_attempts, 1)
        self.assertEqual(self.prospect.total_connected_calls, 1)

    def test_import_matches_numeric_excel_phone_cells_to_plain_digit_staff_and_prospect_numbers(self):
        numeric_staff = User.objects.create(
            email="numericstaff@example.com",
            username="numericstaff@example.com",
            role=User.ROLE_STAFF,
            name="Numeric Staff",
            calling_number="2263083600",
        )
        numeric_staff.set_unusable_password()
        numeric_staff.save()
        numeric_prospect = Prospect.objects.create(
            company_name="Numeric Import Co",
            contact_name="Numeric Prospect",
            linkedin_url="https://linkedin.com/in/numeric-prospect",
            phone_number="9899377578",
            assigned_to=numeric_staff,
            created_by=self.supervisor,
            approval_status=Prospect.APPROVAL_ACCEPTED,
            workflow_status=Prospect.WORKFLOW_READY_TO_CALL,
        )

        self._import_rows(
            [
                [
                    "CA-NUMERIC-1",
                    datetime(2026, 3, 20, 13, 14),
                    datetime(2026, 3, 20, 13, 15),
                    2263083600.0,
                    9899377578.0,
                    "outbound",
                    "no-answer",
                ]
            ]
        )

        log = CallLog.objects.get(call_sid="CA-NUMERIC-1")
        self.assertEqual(log.staff, numeric_staff)
        self.assertEqual(log.prospect, numeric_prospect)
        report = build_daily_target_report(target_date=datetime(2026, 3, 20).date(), tz_name="Asia/Kolkata")
        staff_row = next(item for item in report["staff_rows"] if item["staff"] == numeric_staff)
        self.assertEqual(staff_row["actual_attempts"], 1)

    def test_import_matches_india_country_code_variants_for_prospect_numbers(self):
        local_number_staff = User.objects.create(
            email="localnumberstaff@example.com",
            username="localnumberstaff@example.com",
            role=User.ROLE_STAFF,
            name="Local Number Staff",
            calling_number="9240940704",
        )
        local_number_staff.set_unusable_password()
        local_number_staff.save()
        local_number_prospect = Prospect.objects.create(
            company_name="Country Code Import Co",
            contact_name="Country Code Prospect",
            linkedin_url="https://linkedin.com/in/country-code-prospect",
            phone_number="9004584090",
            assigned_to=local_number_staff,
            created_by=self.supervisor,
            approval_status=Prospect.APPROVAL_ACCEPTED,
            workflow_status=Prospect.WORKFLOW_READY_TO_CALL,
        )

        self._import_rows(
            [
                [
                    "CA-COUNTRYCODE-1",
                    datetime(2026, 3, 20, 12, 14),
                    datetime(2026, 3, 20, 12, 16),
                    9240940704.0,
                    919004584090.0,
                    "outbound",
                    "completed",
                ]
            ]
        )

        log = CallLog.objects.get(call_sid="CA-COUNTRYCODE-1")
        self.assertEqual(log.staff, local_number_staff)
        self.assertEqual(log.prospect, local_number_prospect)

    def test_calendar_invite_includes_selected_meeting_link(self):
        meeting = apply_call_outcome(
            self.prospect,
            self.staff,
            {
                "outcome": "scheduled",
                "scheduled_for": datetime(2026, 3, 30, 15, 0),
                "prospect_email": "prospect@example.com",
                "meeting_platform": Meeting.PLATFORM_TEAMS,
                "reason": "",
                "follow_up_date": None,
            },
        )
        invite = build_calendar_invite(meeting, SystemSetting.load()).decode("utf-8")
        self.assertIn("https://teams.microsoft.com/meet/46370354924443?p=I1IIFzfeGnIxTIGznU", invite)
        self.assertIn("Meeting Platform: Teams meeting", invite)

    def test_calendar_invite_does_not_duplicate_organizer_as_attendee(self):
        meeting = apply_call_outcome(
            self.prospect,
            self.staff,
            {
                "outcome": "scheduled",
                "scheduled_for": datetime(2026, 3, 30, 15, 0),
                "prospect_email": "prospect@example.com",
                "meeting_platform": Meeting.PLATFORM_TEAMS,
                "reason": "",
                "follow_up_date": None,
            },
        )
        invite = build_calendar_invite(meeting, SystemSetting.load()).decode("utf-8")
        normalized_invite = invite.replace("\r\n ", "")

        self.assertIn("ORGANIZER;CN=\"Bhavesh Kataria\";ROLE=CHAIR", normalized_invite)
        self.assertIn("MAILTO:products@inditech.co.in", normalized_invite)
        self.assertEqual(normalized_invite.count("MAILTO:bhavesh.kataria@inditech.co.in"), 1)
        self.assertIn(
            "ATTENDEE;CN=bhavesh.kataria@inditech.co.in;ROLE=REQ-PARTICIPANT:MAILTO:bhavesh.kataria@inditech.co.in",
            normalized_invite,
        )
        self.assertIn(
            "ATTENDEE;CN=amit@inditech.co.in;ROLE=REQ-PARTICIPANT:MAILTO:amit@inditech.co.in",
            normalized_invite,
        )

    @override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend", SENDGRID_API_KEY="")
    def test_meeting_invitation_email_uses_new_subject_and_only_meeting_link_point(self):
        meeting = apply_call_outcome(
            self.prospect,
            self.staff,
            {
                "outcome": "scheduled",
                "scheduled_for": datetime(2026, 3, 30, 15, 0),
                "prospect_email": "prospect@example.com",
                "meeting_platform": Meeting.PLATFORM_TEAMS,
                "reason": "",
                "follow_up_date": None,
            },
        )
        mail.outbox = []

        send_meeting_invitation(meeting)

        self.assertEqual(len(mail.outbox), 1)
        message = mail.outbox[0]
        self.assertEqual(
            message.subject,
            "20-min: Outcome-Linked Campaign – Discussion with Acme",
        )
        self.assertIn("Meeting link:", message.body)
        self.assertNotIn("Prospect:", message.body)
        self.assertNotIn("Company:", message.body)
        self.assertNotIn("LinkedIn:", message.body)
        self.assertNotIn("Scheduled for:", message.body)
        self.assertNotIn("Lead gen staff:", message.body)
        self.assertNotIn("Meeting platform:", message.body)
        html_alternative = message.alternatives[0]
        html_body = getattr(html_alternative, "content", html_alternative[0])
        self.assertIn("Meeting link:", html_body)
        self.assertNotIn("Prospect:", html_body)
        self.assertNotIn("Company:", html_body)
        self.assertNotIn("LinkedIn:", html_body)
        self.assertNotIn("Scheduled for:", html_body)
        self.assertNotIn("Lead gen staff:", html_body)
        self.assertNotIn("Meeting platform:", html_body)

    def test_log_whatsapp_reminder_creates_proof_record(self):
        meeting = apply_call_outcome(
            self.prospect,
            self.staff,
            {
                "outcome": "scheduled",
                "scheduled_for": datetime(2026, 3, 30, 15, 0),
                "prospect_email": "prospect@example.com",
                "meeting_platform": Meeting.PLATFORM_TEAMS,
                "reason": "",
                "follow_up_date": None,
            },
        )
        reminder = log_whatsapp_reminder(
            meeting=meeting,
            reminder_type=MeetingReminder.TYPE_WHATSAPP_INITIAL,
            recipient_number="9876543210",
            screenshot=SimpleUploadedFile("whatsapp-proof.png", b"image-bytes", content_type="image/png"),
            sent_by=self.staff,
        )
        self.assertEqual(reminder.meeting, meeting)
        self.assertEqual(reminder.reminder_type, MeetingReminder.TYPE_WHATSAPP_INITIAL)
        self.assertEqual(reminder.recipient_number, "+9876543210")
        self.assertEqual(reminder.sent_by, self.staff)
        self.assertIn("whatsapp-proof", reminder.screenshot.name)
        self.assertTrue(reminder.screenshot.name.endswith(".png"))

    def test_finance_manager_create_form_does_not_require_calling_number(self):
        form = FinanceManagerCreateForm(
            data={
                "name": "Finance Person",
                "email": "finance.person@example.com",
                "whatsapp_number": "+919811111111",
            }
        )
        self.assertTrue(form.is_valid(), form.errors)
        user = form.save()
        self.assertEqual(user.role, User.ROLE_FINANCE_MANAGER)
        self.assertIsNone(user.calling_number)

    def test_sales_manager_create_form_does_not_require_calling_number(self):
        form = SalesManagerCreateForm(
            data={
                "name": "Sales Person",
                "email": "sales.person@example.com",
                "whatsapp_number": "+919822222222",
            }
        )
        self.assertTrue(form.is_valid(), form.errors)
        user = form.save()
        self.assertEqual(user.role, User.ROLE_SALES_MANAGER)
        self.assertIsNone(user.calling_number)

    @override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend", SENDGRID_API_KEY="")
    def test_send_due_meeting_reminder_emails_creates_automated_logs(self):
        meeting = apply_call_outcome(
            self.prospect,
            self.staff,
            {
                "outcome": "scheduled",
                "scheduled_for": datetime(2026, 3, 31, 10, 0),
                "prospect_email": "prospect@example.com",
                "meeting_platform": Meeting.PLATFORM_TEAMS,
                "reason": "",
                "follow_up_date": None,
            },
        )
        now = timezone.make_aware(datetime(2026, 3, 30, 10, 30))
        sent_count = send_due_meeting_reminder_emails(now=now)
        self.assertEqual(sent_count, 1)
        self.assertEqual(
            MeetingReminder.objects.filter(
                meeting=meeting,
                reminder_type=MeetingReminder.TYPE_EMAIL_DAY_BEFORE,
            ).count(),
            1,
        )
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("Outcome-Linked Campaign", mail.outbox[0].subject)
        self.assertCountEqual(
            mail.outbox[0].cc,
            list(
                SupervisorAccessEmail.objects.filter(
                    access_level=SupervisorAccessEmail.ACCESS_LEADGEN_SUPERVISOR,
                    is_active=True,
                ).values_list("email", flat=True)
            ),
        )
        self.assertNotIn("gopala.krishnan@inditech.co.in", mail.outbox[0].cc)

    def test_reminder_dashboard_marks_missed_whatsapp_steps(self):
        meeting = apply_call_outcome(
            self.prospect,
            self.staff,
            {
                "outcome": "scheduled",
                "scheduled_for": datetime(2026, 3, 31, 12, 0),
                "prospect_email": "prospect@example.com",
                "meeting_platform": Meeting.PLATFORM_TEAMS,
                "reason": "",
                "follow_up_date": None,
            },
        )
        meeting.created_at = timezone.make_aware(datetime(2026, 3, 30, 10, 0))
        meeting.save(update_fields=["created_at"])
        now = timezone.make_aware(datetime(2026, 3, 31, 11, 30))
        dashboard = build_reminder_dashboard("Asia/Kolkata", now=now)
        row = next(item for item in dashboard["rows"] if item["meeting"] == meeting)
        self.assertTrue(row["first_whatsapp"]["is_missed"])
        self.assertTrue(row["final_whatsapp"]["is_missed"])
        self.assertIn("First WhatsApp", row["missed"])

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
        self._import_rows(
            [
                [
                    "CA2",
                    "2026-03-20 10:00",
                    "2026-03-20 10:04",
                    "+919900000001",
                    "+919812345678",
                    "outbound",
                    "completed",
                ]
            ]
        )
        report = build_supervisor_report(datetime(2026, 3, 20).date(), datetime(2026, 3, 20).date(), "Asia/Kolkata")
        self.assertEqual(report["summary"]["attempts"], 1)
        self.assertEqual(report["summary"]["follow_ups"], 1)
        staff_metric = next(item for item in report["staff_metrics"] if item["staff"] == self.staff)
        self.assertEqual(staff_metric["attempts"], 1)
        self.assertEqual(staff_metric["follow_ups"], 1)

    def test_report_builder_separates_new_and_rescheduled_meetings(self):
        report_date = datetime(2026, 3, 20).date()
        new_meeting = apply_call_outcome(
            self.prospect,
            self.staff,
            {
                "outcome": "scheduled",
                "scheduled_for": datetime(2026, 3, 25, 15, 0),
                "prospect_email": "prospect@example.com",
                "meeting_platform": Meeting.PLATFORM_TEAMS,
                "reason": "",
                "follow_up_date": None,
            },
        )
        ProspectStatusUpdate.objects.filter(
            prospect=self.prospect,
            staff=self.staff,
            outcome=ProspectStatusUpdate.OUTCOME_SCHEDULED,
            reason="",
        ).update(created_at=timezone.make_aware(datetime(2026, 3, 20, 9, 0)))
        Meeting.objects.filter(pk=new_meeting.pk).update(created_at=timezone.make_aware(datetime(2026, 3, 20, 9, 0)))

        reschedule_prospect = Prospect.objects.create(
            company_name="Reschedule Co",
            contact_name="Reschedule Person",
            linkedin_url="https://linkedin.com/in/reschedule-person",
            phone_number="+919812349998",
            assigned_to=self.other_staff,
            created_by=self.other_staff,
            approval_status=Prospect.APPROVAL_ACCEPTED,
            workflow_status=Prospect.WORKFLOW_READY_TO_CALL,
        )
        original_meeting = apply_call_outcome(
            reschedule_prospect,
            self.other_staff,
            {
                "outcome": "scheduled",
                "scheduled_for": datetime(2026, 3, 24, 11, 0),
                "prospect_email": "reschedule@example.com",
                "meeting_platform": Meeting.PLATFORM_ZOOM,
                "reason": "",
                "follow_up_date": None,
            },
        )
        ProspectStatusUpdate.objects.filter(
            prospect=reschedule_prospect,
            staff=self.other_staff,
            outcome=ProspectStatusUpdate.OUTCOME_SCHEDULED,
            reason="",
        ).update(created_at=timezone.make_aware(datetime(2026, 3, 18, 9, 0)))
        Meeting.objects.filter(pk=original_meeting.pk).update(created_at=timezone.make_aware(datetime(2026, 3, 18, 9, 0)))

        rescheduled_meeting = reschedule_meeting(
            original_meeting,
            timezone.make_aware(datetime(2026, 3, 26, 14, 0)),
            updated_by=self.supervisor,
        )
        ProspectStatusUpdate.objects.filter(
            prospect=reschedule_prospect,
            staff=self.other_staff,
            outcome=ProspectStatusUpdate.OUTCOME_SCHEDULED,
            reason="Meeting rescheduled",
        ).update(created_at=timezone.make_aware(datetime(2026, 3, 20, 10, 0)))
        Meeting.objects.filter(pk=rescheduled_meeting.pk).update(created_at=timezone.make_aware(datetime(2026, 3, 20, 10, 0)))

        report = build_supervisor_report(report_date, report_date, "Asia/Kolkata")
        self.assertEqual(report["summary"]["meetings_scheduled"], 1)
        self.assertEqual(report["summary"]["meetings_rescheduled"], 1)
        staff_metric = next(item for item in report["staff_metrics"] if item["staff"] == self.staff)
        self.assertEqual(staff_metric["meetings_scheduled"], 1)
        self.assertEqual(staff_metric["meetings_rescheduled"], 0)
        other_metric = next(item for item in report["staff_metrics"] if item["staff"] == self.other_staff)
        self.assertEqual(other_metric["meetings_scheduled"], 0)
        self.assertEqual(other_metric["meetings_rescheduled"], 1)

    def test_daily_target_report_returns_yesterday_target_and_attempts_per_staff(self):
        target_date = timezone.localdate()
        second_target = Prospect.objects.create(
            company_name="Follow Up Co",
            contact_name="Follow Up Person",
            linkedin_url="https://linkedin.com/in/follow-up-person",
            phone_number="+919812300001",
            assigned_to=self.other_staff,
            created_by=self.supervisor,
            approval_status=Prospect.APPROVAL_ACCEPTED,
            workflow_status=Prospect.WORKFLOW_FOLLOW_UP,
        )
        completed_prospect = Prospect.objects.create(
            company_name="Worked Co",
            contact_name="Worked Person",
            linkedin_url="https://linkedin.com/in/worked-person",
            phone_number="+919812300002",
            assigned_to=self.staff,
            created_by=self.supervisor,
            approval_status=Prospect.APPROVAL_ACCEPTED,
            workflow_status=Prospect.WORKFLOW_SCHEDULED,
        )
        batch = CallImportBatch.objects.create(
            import_date=target_date,
            uploaded_file=SimpleUploadedFile("target-report.xlsx", b"placeholder"),
            imported_by=self.supervisor,
        )
        started_at = timezone.make_aware(datetime.combine(target_date, datetime.min.time()) + timedelta(hours=10))
        ended_at = started_at + timedelta(minutes=2)
        CallLog.objects.create(
            call_sid="TARGET-REPORT-1",
            batch=batch,
            staff=self.staff,
            prospect=completed_prospect,
            started_at=started_at,
            ended_at=ended_at,
            duration_seconds=120,
            from_number=self.staff.calling_number,
            to_number=completed_prospect.phone_number,
            direction="outbound",
            crm_status=CallLog.STATUS_COMPLETED,
            was_connected=True,
            matched=True,
            raw_data={},
        )
        CallLog.objects.create(
            call_sid="TARGET-REPORT-2",
            batch=batch,
            staff=self.other_staff,
            prospect=second_target,
            started_at=started_at,
            ended_at=ended_at,
            duration_seconds=120,
            from_number=self.other_staff.calling_number,
            to_number=second_target.phone_number,
            direction="outbound",
            crm_status=CallLog.STATUS_NO_ANSWER,
            was_connected=False,
            matched=True,
            raw_data={},
        )
        report = build_daily_target_report(target_date=target_date, tz_name="Asia/Kolkata")
        staff_row = next(item for item in report["staff_rows"] if item["staff"] == self.staff)
        other_staff_row = next(item for item in report["staff_rows"] if item["staff"] == self.other_staff)
        self.assertEqual(staff_row["target_count"], 2)
        self.assertEqual(staff_row["actual_attempts"], 1)
        self.assertEqual(other_staff_row["target_count"], 1)
        self.assertEqual(other_staff_row["actual_attempts"], 1)

    def test_import_failed_marks_prospect_invalid_and_hides_from_staff_views(self):
        self._import_rows(
            [
                [
                    "CA-FAILED-1",
                    "2026-03-20 10:00",
                    "2026-03-20 10:01",
                    "+919900000001",
                    "+919812345678",
                    "outbound",
                    "failed",
                ]
            ]
        )
        self.prospect.refresh_from_db()
        self.assertEqual(self.prospect.workflow_status, Prospect.WORKFLOW_INVALID_NUMBER)
        self.assertIn("invalid number", self.prospect.system_action_note.lower())
        self.client.force_login(self.staff)
        response = self.client.get("/staff/dashboard/")
        self.assertNotContains(response, self.prospect.company_name)
        invalid_response = self.client.get("/staff/prospects/")
        self.assertNotContains(invalid_response, self.prospect.company_name)
        self.client.force_login(self.supervisor)
        supervisor_response = self.client.get("/supervisor/prospects/invalid/")
        self.assertContains(supervisor_response, self.prospect.company_name)

    def test_import_five_no_answers_moves_prospect_to_supervisor_action_queue(self):
        self._import_rows(
            [
                [
                    f"CA-NOANSWER-{index}",
                    f"2026-03-2{index} 10:00",
                    f"2026-03-2{index} 10:01",
                    "+919900000001",
                    "+919812345678",
                    "outbound",
                    "no-answer",
                ]
                for index in range(1, 6)
            ]
        )
        self.prospect.refresh_from_db()
        self.assertEqual(self.prospect.workflow_status, Prospect.WORKFLOW_SUPERVISOR_ACTION)
        self.assertEqual(self.prospect.system_action_note, "Attempted five times with no answer.")
        self.client.force_login(self.staff)
        response = self.client.get("/staff/dashboard/")
        self.assertNotContains(response, self.prospect.company_name)
        hidden_response = self.client.get("/staff/prospects/")
        self.assertNotContains(hidden_response, self.prospect.company_name)
        self.client.force_login(self.supervisor)
        supervisor_response = self.client.get("/supervisor/prospects/supervisor-action/")
        self.assertContains(supervisor_response, self.prospect.company_name)
        self.assertContains(supervisor_response, "Attempted five times with no answer.")

    def test_supervisor_alias_maps_to_single_supervisor_user(self):
        adapter = LeadgenSocialAccountAdapter()
        user = adapter._authorized_user_for_email("gkinchina@gmail.com")
        self.assertEqual(user.pk, self.supervisor.pk)
        user = adapter._authorized_user_for_email("gopala.krishnan@inditech.co.in")
        self.assertEqual(user.pk, self.supervisor.pk)
        user = adapter._authorized_user_for_email("leesaamit@gmail.com")
        self.assertEqual(user.pk, self.supervisor.pk)
        user = adapter._authorized_user_for_email("vamshi.alle@inditech.co.in")
        self.assertEqual(user.pk, self.supervisor.pk)

    def test_sales_manager_create_form_allows_supervisor_email_when_shared_supervisor_is_internal(self):
        form = SalesManagerCreateForm(
            data={
                "name": "Bhavesh Kataria",
                "email": "bhavesh.kataria@inditech.co.in",
                "whatsapp_number": "+919876543210",
            }
        )
        self.assertTrue(form.is_valid(), form.errors)

    def test_existing_supervisor_social_account_rebinds_to_real_user_with_same_email(self):
        sales_user = User.objects.create(
            email="bhavesh.kataria@inditech.co.in",
            username="bhavesh.kataria@inditech.co.in",
            role=User.ROLE_SALES_MANAGER,
            name="Bhavesh Kataria",
        )
        sales_user.set_unusable_password()
        sales_user.save()
        account = SocialAccount.objects.create(
            user=self.supervisor,
            provider="google",
            uid="google-bhavesh-uid",
            extra_data={
                "email": "bhavesh.kataria@inditech.co.in",
                "email_verified": True,
            },
        )
        sociallogin = SimpleNamespace(
            account=account,
            user=self.supervisor,
            is_existing=True,
        )
        request = self._request_with_session()

        LeadgenSocialAccountAdapter().pre_social_login(request, sociallogin)

        account.refresh_from_db()
        self.assertEqual(account.user_id, sales_user.pk)
        self.assertEqual(sociallogin.user.pk, sales_user.pk)
        self.assertEqual(request.session["supervisor_access_email"], "bhavesh.kataria@inditech.co.in")

    def test_login_page_posts_to_google_provider(self):
        response = self.client.get("/login/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'method="post"')
        self.assertContains(response, "Continue with Google")

    def test_home_redirects_sales_manager_to_sales_pipeline(self):
        self.client.force_login(self.sales_manager)
        response = self.client.get("/")
        self.assertRedirects(response, "/sales/")

    def test_home_redirects_finance_manager_to_contracts(self):
        self.client.force_login(self.finance_manager)
        response = self.client.get("/")
        self.assertRedirects(response, "/contracts/")

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

    def test_supervisor_can_reassign_pending_prospect_to_another_staff_member(self):
        pending = Prospect.objects.create(
            company_name="Pending Reassign Co",
            contact_name="Pending Person",
            linkedin_url="https://linkedin.com/in/pending-person",
            phone_number="+919811111111",
            assigned_to=self.staff,
            created_by=self.staff,
            approval_status=Prospect.APPROVAL_PENDING,
            workflow_status=Prospect.WORKFLOW_PENDING_REVIEW,
        )
        self.client.force_login(self.supervisor)
        response = self.client.post(
            f"/supervisor/prospects/{pending.pk}/review/",
            {
                "assigned_to": self.other_staff.pk,
                "decision": "accept",
                "supervisor_notes": "Move this to the other caller.",
            },
        )
        self.assertRedirects(response, "/supervisor/prospects/review/")
        pending.refresh_from_db()
        self.assertEqual(pending.assigned_to, self.other_staff)
        self.assertEqual(pending.approval_status, Prospect.APPROVAL_ACCEPTED)
        self.assertEqual(pending.workflow_status, Prospect.WORKFLOW_READY_TO_CALL)
        staff_response = self.client.get(f"/supervisor/staff/{self.staff.pk}/dashboard/")
        self.assertNotContains(staff_response, "Pending Reassign Co")
        other_staff_response = self.client.get(f"/supervisor/staff/{self.other_staff.pk}/dashboard/")
        self.assertContains(other_staff_response, "Pending Reassign Co")

    def test_supervisor_can_add_prospect_and_assign_to_staff(self):
        self.client.force_login(self.supervisor)
        response = self.client.post(
            "/supervisor/prospects/add/",
            {
                "company_name": "Supervisor Added Co",
                "contact_name": "Direct Prospect",
                "linkedin_url": "www.linkedin.com/in/direct-prospect",
                "phone_number": "8880011223",
                "assigned_to": self.other_staff.pk,
            },
        )
        self.assertRedirects(response, f"/supervisor/staff/{self.other_staff.pk}/dashboard/")
        created = Prospect.objects.get(phone_number="8880011223")
        self.assertEqual(created.assigned_to, self.other_staff)
        self.assertEqual(created.created_by, self.supervisor)
        self.assertEqual(created.approval_status, Prospect.APPROVAL_ACCEPTED)
        self.assertEqual(created.workflow_status, Prospect.WORKFLOW_READY_TO_CALL)
        self.assertEqual(
            created.linkedin_url,
            "https://www.linkedin.com/in/direct-prospect",
        )

    def test_supervisor_can_reassign_five_no_answer_prospect_to_other_staff(self):
        self.prospect.workflow_status = Prospect.WORKFLOW_SUPERVISOR_ACTION
        self.prospect.system_action_note = "Attempted five times with no answer."
        self.prospect.save(update_fields=["workflow_status", "system_action_note", "updated_at"])
        for index in range(5):
            self.prospect.call_logs.create(
                call_sid=f"REASSIGN-NOANSWER-{index}",
                batch=CallImportBatch.objects.create(
                    import_date=timezone.localdate(),
                    uploaded_file=SimpleUploadedFile(f"batch-{index}.xlsx", b"placeholder"),
                    imported_by=self.supervisor,
                ),
                staff=self.staff,
                prospect=self.prospect,
                started_at=timezone.now(),
                ended_at=timezone.now(),
                from_number=self.staff.calling_number,
                to_number=self.prospect.phone_number,
                direction="outbound",
                crm_status="no-answer",
                was_connected=False,
                matched=True,
                raw_data={},
            )
        self.client.force_login(self.supervisor)
        response = self.client.post(
            f"/supervisor/prospects/{self.prospect.pk}/manage/",
            {
                "assigned_to": self.other_staff.pk,
                "supervisor_notes": "Try a different caller.",
                "action": "reassign",
            },
        )
        self.assertRedirects(response, f"/supervisor/staff/{self.other_staff.pk}/dashboard/")
        self.prospect.refresh_from_db()
        self.assertEqual(self.prospect.assigned_to, self.other_staff)
        self.assertEqual(self.prospect.workflow_status, Prospect.WORKFLOW_READY_TO_CALL)
        self.assertEqual(self.prospect.system_action_note, "")
        self.assertEqual(self.prospect.no_answer_reset_count, 5)
        new_staff_response = self.client.get(f"/supervisor/staff/{self.other_staff.pk}/dashboard/")
        self.assertContains(new_staff_response, self.prospect.company_name)

    def test_supervisor_can_mark_five_no_answer_prospect_invalid(self):
        self.prospect.workflow_status = Prospect.WORKFLOW_SUPERVISOR_ACTION
        self.prospect.system_action_note = "Attempted five times with no answer."
        self.prospect.save(update_fields=["workflow_status", "system_action_note", "updated_at"])
        self.client.force_login(self.supervisor)
        response = self.client.post(
            f"/supervisor/prospects/{self.prospect.pk}/manage/",
            {
                "assigned_to": self.staff.pk,
                "supervisor_notes": "Invalid after repeated unanswered attempts.",
                "action": "mark_invalid",
            },
        )
        self.assertRedirects(response, "/supervisor/prospects/invalid/")
        self.prospect.refresh_from_db()
        self.assertEqual(self.prospect.workflow_status, Prospect.WORKFLOW_INVALID_NUMBER)
        self.assertIn("marked invalid", self.prospect.system_action_note.lower())
        invalid_response = self.client.get("/supervisor/prospects/invalid/")
        self.assertContains(invalid_response, self.prospect.company_name)

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
                        "meeting_platform": Meeting.PLATFORM_ZOOM,
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
                        "meeting_platform": Meeting.PLATFORM_TEAMS,
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

    def test_contract_collection_is_created_from_signed_sales_conversation(self):
        sales_conversation = SalesConversation.objects.create(
            company_name="Signed Co",
            assigned_sales_manager=self.sales_manager,
            contract_signed=True,
            created_by=self.supervisor,
        )
        sales_conversation.contacts.create(position=1, name="Jane Doe", email="jane@example.com")
        contract_collection = get_or_create_contract_collection_from_sales_conversation(
            sales_conversation,
            created_by=self.supervisor,
        )
        self.assertEqual(contract_collection.company_name, "Signed Co")
        self.assertEqual(contract_collection.sales_manager, self.sales_manager)
        self.assertEqual(contract_collection.contacts.count(), 1)

    def test_contract_form_enforces_one_contact_and_collects_installments(self):
        form = ContractCollectionForm(
            data={
                "company_name": "Acme Contracts",
                "sales_manager": self.sales_manager.pk,
                "contract_value": "150000.00",
                "contact_1_name": "Jane Doe",
                "contact_1_email": "jane@example.com",
                "contact_1_whatsapp": "9999999999",
                "contact_2_name": "",
                "contact_2_email": "",
                "contact_2_whatsapp": "",
                "contact_3_name": "",
                "contact_3_email": "",
                "contact_3_whatsapp": "",
                "installment_1_amount": "50000.00",
                "installment_1_invoice_date": "2026-04-10",
                "installment_1_expected_collection_date": "2026-04-20",
                "installment_1_revised_collection_date": "",
                "installment_1_contract_summary": "Master Services Agreement between Inditech and Acme dated April 1, 2026",
                "installment_1_service_description": "Outcome-linked campaign strategy and activation services for Q2",
                "installment_1_legal_due_reason": "Invoice is due under milestone 1 acceptance confirmed by the client.",
            }
        )
        self.assertTrue(form.is_valid(), form.errors.as_json())
        self.assertEqual(form.cleaned_data["contact_rows"][0]["name"], "Jane Doe")
        self.assertEqual(form.cleaned_data["installment_rows"][0]["position"], 1)
        self.assertEqual(
            form.cleaned_data["installment_rows"][0]["contract_summary"],
            "Master Services Agreement between Inditech and Acme dated April 1, 2026",
        )

    def test_contract_form_requires_invoice_reference_fields_for_each_installment(self):
        form = ContractCollectionForm(
            data={
                "company_name": "Acme Contracts",
                "sales_manager": self.sales_manager.pk,
                "contract_value": "150000.00",
                "contact_1_name": "Jane Doe",
                "contact_1_email": "jane@example.com",
                "contact_1_whatsapp": "9999999999",
                "installment_1_amount": "50000.00",
                "installment_1_invoice_date": "2026-04-10",
                "installment_1_expected_collection_date": "2026-04-20",
                "installment_1_revised_collection_date": "",
                "installment_1_contract_summary": "",
                "installment_1_service_description": "",
                "installment_1_legal_due_reason": "",
            }
        )
        self.assertFalse(form.is_valid())
        self.assertIn("installment_1_contract_summary", form.errors)
        self.assertIn("installment_1_service_description", form.errors)
        self.assertIn("installment_1_legal_due_reason", form.errors)

    @override_settings(MAX_UPLOAD_FILE_SIZE=10)
    def test_sales_conversation_form_rejects_files_over_10mb_limit(self):
        form = SalesConversationForm(
            data={
                "company_name": "Acme Sales",
                "assigned_sales_manager": self.sales_manager.pk,
                "conversation_status": SalesConversation.STATUS_ENGAGED,
                "proposal_status": SalesConversation.PROPOSAL_SOLUTION_NEEDED,
                "contract_signed": "",
                "comments": "",
                "brands_input": "Brand A",
                "contact_1_name": "Jane Doe",
                "contact_1_email": "jane@example.com",
                "contact_1_whatsapp": "9999999999",
            },
            files=MultiValueDict(
                {
                    "solution_files": [SimpleUploadedFile("large-solution.pdf", b"12345678901")],
                    "proposal_files": [SimpleUploadedFile("large-proposal.pdf", b"12345678901")],
                }
            ),
        )
        self.assertFalse(form.is_valid())
        self.assertIn("solution_files", form.errors)
        self.assertIn("proposal_files", form.errors)

    def test_sales_conversation_form_accepts_pdf_uploads(self):
        form = SalesConversationForm(
            data={
                "company_name": "Acme Sales",
                "assigned_sales_manager": self.sales_manager.pk,
                "conversation_status": SalesConversation.STATUS_ENGAGED,
                "proposal_status": SalesConversation.PROPOSAL_SOLUTION_NEEDED,
                "contract_signed": "",
                "comments": "",
                "brands_input": "Brand A",
                "contact_1_name": "Jane Doe",
                "contact_1_email": "jane@example.com",
                "contact_1_whatsapp": "9999999999",
            },
            files=MultiValueDict(
                {
                    "proposal_files": [
                        SimpleUploadedFile("proposal.pdf", b"%PDF-1.4 proposal", content_type="application/pdf")
                    ],
                    "solution_files": [
                        SimpleUploadedFile("solution.pdf", b"%PDF-1.4 solution", content_type="application/pdf")
                    ],
                }
            ),
        )
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(len(form.cleaned_data["proposal_files"]), 1)
        self.assertEqual(len(form.cleaned_data["solution_files"]), 1)

    @override_settings(MAX_UPLOAD_FILE_SIZE=10)
    def test_contract_form_rejects_files_over_10mb_limit(self):
        form = ContractCollectionForm(
            data={
                "company_name": "Acme Contracts",
                "sales_manager": self.sales_manager.pk,
                "contract_value": "150000.00",
                "contact_1_name": "Jane Doe",
                "contact_1_email": "jane@example.com",
                "contact_1_whatsapp": "9999999999",
            },
            files=MultiValueDict(
                {
                    "contract_files": [SimpleUploadedFile("large-contract.pdf", b"12345678901")],
                }
            ),
        )
        self.assertFalse(form.is_valid())
        self.assertIn("contract_files", form.errors)

    def test_contract_form_accepts_pdf_uploads(self):
        form = ContractCollectionForm(
            data={
                "company_name": "Acme Contracts",
                "sales_manager": self.sales_manager.pk,
                "contract_value": "150000.00",
                "contact_1_name": "Jane Doe",
                "contact_1_email": "jane@example.com",
                "contact_1_whatsapp": "9999999999",
            },
            files=MultiValueDict(
                {
                    "contract_files": [
                        SimpleUploadedFile("contract.pdf", b"%PDF-1.4 contract", content_type="application/pdf")
                    ],
                }
            ),
        )
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(len(form.cleaned_data["contract_files"]), 1)

    def test_supervisor_can_edit_locked_contract_fields_on_existing_contract(self):
        contract_collection = ContractCollection.objects.create(
            company_name="Editable Contract Co",
            sales_manager=self.sales_manager,
            contract_value="100000.00",
            created_by=self.supervisor,
        )
        contract_collection.contacts.create(position=1, name="Jane Doe", email="jane@example.com", whatsapp_number="9999999999")
        installment = ContractCollectionInstallment.objects.create(
            contract_collection=contract_collection,
            position=1,
            installment_amount="25000.00",
            invoice_date=datetime(2026, 4, 10).date(),
            expected_collection_date=datetime(2026, 4, 20).date(),
            contract_summary="Initial contract summary",
            invoiced_service_description="Initial service description",
            legal_due_reason="Initial legal due reason",
        )

        self.client.force_login(self.supervisor)
        response = self.client.post(
            f"/contracts/{contract_collection.pk}/",
            {
                "form_type": "terms",
                "company_name": contract_collection.company_name,
                "sales_manager": self.sales_manager.pk,
                "contract_value": "125000.00",
                "contact_1_name": "Jane Doe",
                "contact_1_email": "jane@example.com",
                "contact_1_whatsapp": "9999999999",
                "contact_2_name": "",
                "contact_2_email": "",
                "contact_2_whatsapp": "",
                "contact_3_name": "",
                "contact_3_email": "",
                "contact_3_whatsapp": "",
                "installment_1_amount": "30000.00",
                "installment_1_invoice_date": "2026-04-12",
                "installment_1_expected_collection_date": "2026-04-25",
                "installment_1_revised_collection_date": "",
                "installment_1_contract_summary": "Updated contract summary",
                "installment_1_service_description": "Updated service description",
                "installment_1_legal_due_reason": "Updated legal due reason",
                "installment_2_amount": "",
                "installment_2_invoice_date": "",
                "installment_2_expected_collection_date": "",
                "installment_2_revised_collection_date": "",
                "installment_2_contract_summary": "",
                "installment_2_service_description": "",
                "installment_2_legal_due_reason": "",
                "installment_3_amount": "",
                "installment_3_invoice_date": "",
                "installment_3_expected_collection_date": "",
                "installment_3_revised_collection_date": "",
                "installment_3_contract_summary": "",
                "installment_3_service_description": "",
                "installment_3_legal_due_reason": "",
                "installment_4_amount": "",
                "installment_4_invoice_date": "",
                "installment_4_expected_collection_date": "",
                "installment_4_revised_collection_date": "",
                "installment_4_contract_summary": "",
                "installment_4_service_description": "",
                "installment_4_legal_due_reason": "",
                "installment_5_amount": "",
                "installment_5_invoice_date": "",
                "installment_5_expected_collection_date": "",
                "installment_5_revised_collection_date": "",
                "installment_5_contract_summary": "",
                "installment_5_service_description": "",
                "installment_5_legal_due_reason": "",
                "installment_6_amount": "",
                "installment_6_invoice_date": "",
                "installment_6_expected_collection_date": "",
                "installment_6_revised_collection_date": "",
                "installment_6_contract_summary": "",
                "installment_6_service_description": "",
                "installment_6_legal_due_reason": "",
            },
        )
        self.assertRedirects(response, f"/contracts/{contract_collection.pk}/")
        contract_collection.refresh_from_db()
        installment.refresh_from_db()
        self.assertEqual(str(contract_collection.contract_value), "125000.00")
        self.assertEqual(str(installment.installment_amount), "30000.00")
        self.assertEqual(str(installment.invoice_date), "2026-04-12")
        self.assertEqual(str(installment.expected_collection_date), "2026-04-25")
        self.assertEqual(installment.contract_summary, "Updated contract summary")

    def test_existing_contract_form_keeps_locked_fields_disabled_without_supervisor_override(self):
        contract_collection = ContractCollection.objects.create(
            company_name="Locked Contract Co",
            sales_manager=self.sales_manager,
            contract_value="100000.00",
            created_by=self.supervisor,
        )
        ContractCollectionInstallment.objects.create(
            contract_collection=contract_collection,
            position=1,
            installment_amount="25000.00",
            invoice_date=datetime(2026, 4, 10).date(),
            expected_collection_date=datetime(2026, 4, 20).date(),
            contract_summary="Initial contract summary",
            invoiced_service_description="Initial service description",
            legal_due_reason="Initial legal due reason",
        )
        form = ContractCollectionForm(instance=contract_collection)
        self.assertTrue(form.fields["contract_value"].disabled)
        self.assertTrue(form.fields["installment_1_amount"].disabled)
        self.assertTrue(form.fields["installment_1_invoice_date"].disabled)
        self.assertTrue(form.fields["installment_1_expected_collection_date"].disabled)

    @override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
    def test_send_due_invoice_notifications_sends_email_for_today(self):
        contract_collection = ContractCollection.objects.create(
            company_name="Invoice Co",
            sales_manager=self.sales_manager,
            contract_value="100000.00",
            created_by=self.supervisor,
        )
        contract_collection.contacts.create(position=1, name="Jane Doe", email="jane@example.com")
        installment = ContractCollectionInstallment.objects.create(
            contract_collection=contract_collection,
            position=1,
            installment_amount="25000.00",
            invoice_date=timezone.localdate(),
            expected_collection_date=timezone.localdate(),
            contract_summary="Agreement between Inditech and Invoice Co dated April 1, 2026",
            invoiced_service_description="Campaign setup and launch services",
            legal_due_reason="Milestone one deliverables were accepted by the client.",
        )
        sent_count = send_due_invoice_notifications(timezone.localdate())
        installment.refresh_from_db()
        self.assertEqual(sent_count, 1)
        self.assertIsNotNone(installment.invoice_notification_sent_at)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["finance@example.com"])
        self.assertCountEqual(
            mail.outbox[0].cc,
            [
                "bhavesh.kataria@inditech.co.in",
                "gkinchina@gmail.com",
                "leesaamit@gmail.com",
                "vamshi.alle@inditech.co.in",
                "amit@inditech.co.in",
            ],
        )
        self.assertIn("Invoice to be raised today", mail.outbox[0].subject)
        self.assertIn("Agreement between Inditech and Invoice Co dated April 1, 2026", mail.outbox[0].body)
        self.assertIn("Campaign setup and launch services", mail.outbox[0].body)
        self.assertIn("Milestone one deliverables were accepted by the client.", mail.outbox[0].body)

    @override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
    def test_send_due_invoice_notifications_uses_business_timezone_date(self):
        contract_collection = ContractCollection.objects.create(
            company_name="India Immunologicals",
            sales_manager=self.sales_manager,
            contract_value="250000.00",
            created_by=self.supervisor,
        )
        contract_collection.contacts.create(position=1, name="Jane Doe", email="jane@example.com")
        installment = ContractCollectionInstallment.objects.create(
            contract_collection=contract_collection,
            position=2,
            installment_amount="100000.00",
            invoice_date=datetime(2026, 4, 16).date(),
            expected_collection_date=datetime(2026, 4, 20).date(),
            contract_summary="Agreement between Inditech and India Immunologicals dated April 1, 2026",
            invoiced_service_description="Installment 2 campaign services",
            legal_due_reason="Installment 2 is due under the signed contract milestones.",
        )
        utc_now = timezone.make_aware(datetime(2026, 4, 15, 19, 0), ZoneInfo("UTC"))

        self.assertEqual(business_localdate("Asia/Kolkata", now=utc_now), datetime(2026, 4, 16).date())
        sent_count = send_due_invoice_notifications(now=utc_now)

        installment.refresh_from_db()
        self.assertEqual(sent_count, 1)
        self.assertIsNotNone(installment.invoice_notification_sent_at)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("India Immunologicals", mail.outbox[0].subject)

    @override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
    def test_updating_invoice_date_to_today_sends_invoice_email_immediately(self):
        contract_collection = ContractCollection.objects.create(
            company_name="Immediate Invoice Co",
            sales_manager=self.sales_manager,
            contract_value="250000.00",
            created_by=self.supervisor,
        )
        contract_collection.contacts.create(
            position=1,
            name="Jane Doe",
            email="jane@example.com",
            whatsapp_number="9999999999",
        )
        installment = ContractCollectionInstallment.objects.create(
            contract_collection=contract_collection,
            position=1,
            installment_amount="50000.00",
            invoice_date=timezone.localdate() + timedelta(days=2),
            expected_collection_date=timezone.localdate() + timedelta(days=10),
            contract_summary="Agreement between Inditech and Immediate Invoice Co dated April 1, 2026",
            invoiced_service_description="Initial campaign delivery",
            legal_due_reason="The first milestone has been completed and accepted.",
        )

        cleaned_data = {
            "sales_manager": self.sales_manager,
            "contract_value": contract_collection.contract_value,
            "contact_rows": [
                {
                    "position": 1,
                    "name": "Jane Doe",
                    "email": "jane@example.com",
                    "whatsapp_number": "9999999999",
                }
            ],
            "installment_rows": [
                {
                    "position": 1,
                    "installment_amount": installment.installment_amount,
                    "invoice_date": timezone.localdate(),
                    "expected_collection_date": installment.expected_collection_date,
                    "revised_collection_date": None,
                    "contract_summary": installment.contract_summary,
                    "invoiced_service_description": installment.invoiced_service_description,
                    "legal_due_reason": installment.legal_due_reason,
                }
            ],
        }

        with self.captureOnCommitCallbacks(execute=True):
            sync_contract_collection_data(
                contract_collection,
                cleaned_data,
                allow_locked_field_edits=True,
            )

        installment.refresh_from_db()
        self.assertEqual(installment.invoice_date, timezone.localdate())
        self.assertIsNotNone(installment.invoice_notification_sent_at)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["finance@example.com"])
        self.assertCountEqual(
            mail.outbox[0].cc,
            [
                "bhavesh.kataria@inditech.co.in",
                "gkinchina@gmail.com",
                "leesaamit@gmail.com",
                "vamshi.alle@inditech.co.in",
                "amit@inditech.co.in",
            ],
        )

    @override_settings(
        SENDGRID_API_KEY="test-sendgrid-key",
        DEFAULT_FROM_EMAIL="products@inditech.co.in",
        REPLY_TO_EMAIL="bhavesh.kataria@inditech.co.in",
    )
    def test_send_email_uses_sendgrid_payload_with_cc_and_attachments(self):
        response = SimpleNamespace(status_code=202, body=b"accepted")
        with patch("leadgen.services.SendGridAPIClient") as client_cls:
            client_cls.return_value.client.mail.send.post.return_value = response
            send_email(
                subject="Meeting invitation",
                html_body="<p>HTML body</p>",
                text_body="Text body",
                to_emails=["prospect@example.com"],
                cc_emails=["staff@example.com", "amit@inditech.co.in"],
                attachments=[
                    {
                        "filename": "meeting-invite.ics",
                        "content": b"BEGIN:VCALENDAR",
                        "type": "text/calendar",
                    }
                ],
            )

        payload = client_cls.return_value.client.mail.send.post.call_args.kwargs["request_body"]
        self.assertEqual(payload["from"]["email"], "products@inditech.co.in")
        self.assertEqual(payload["reply_to"]["email"], "bhavesh.kataria@inditech.co.in")
        self.assertEqual(payload["personalizations"][0]["to"], [{"email": "prospect@example.com"}])
        self.assertEqual(
            payload["personalizations"][0]["cc"],
            [{"email": "staff@example.com"}, {"email": "amit@inditech.co.in"}],
        )
        self.assertEqual(payload["content"][0]["type"], "text/plain")
        self.assertEqual(payload["content"][1]["type"], "text/html")
        self.assertEqual(payload["attachments"][0]["filename"], "meeting-invite.ics")
        self.assertEqual(payload["attachments"][0]["type"], "text/calendar")

    @override_settings(
        EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
        DEFAULT_FROM_EMAIL="products@inditech.co.in",
        REPLY_TO_EMAIL="bhavesh.kataria@inditech.co.in",
        SENDGRID_API_KEY="",
    )
    def test_send_email_sets_reply_to_for_django_backend(self):
        send_email(
            subject="Meeting invitation",
            html_body="<p>HTML body</p>",
            text_body="Text body",
            to_emails=["prospect@example.com"],
            cc_emails=["staff@example.com"],
        )
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].from_email, "products@inditech.co.in")
        self.assertEqual(mail.outbox[0].reply_to, ["bhavesh.kataria@inditech.co.in"])

    @override_settings(
        SENDGRID_API_KEY="test-sendgrid-key",
        DEFAULT_FROM_EMAIL="products@inditech.co.in",
        REPLY_TO_EMAIL="bhavesh.kataria@inditech.co.in",
    )
    def test_send_test_email_diagnostic_reports_sendgrid_success(self):
        response = SimpleNamespace(status_code=202, body=b"accepted")
        with patch("leadgen.services.probe_sendgrid_connectivity", return_value={"dns_ok": True, "resolved_hosts": ["1.2.3.4"]}):
            with patch("leadgen.services.SendGridAPIClient") as client_cls:
                client_cls.return_value.client.mail.send.post.return_value = response
                diagnostics = send_test_email_diagnostic("gopala.krishnan@inditech.co.in")

        self.assertTrue(diagnostics["success"])
        self.assertEqual(diagnostics["delivery"]["transport"], "sendgrid")
        self.assertEqual(diagnostics["delivery"]["status_code"], 202)
        self.assertEqual(diagnostics["to_email"], "gopala.krishnan@inditech.co.in")
        self.assertEqual(diagnostics["sendgrid_connectivity"]["dns_ok"], True)

    @override_settings(
        EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
        DEFAULT_FROM_EMAIL="products@inditech.co.in",
        REPLY_TO_EMAIL="bhavesh.kataria@inditech.co.in",
        SENDGRID_API_KEY="",
    )
    def test_send_test_email_diagnostic_reports_backend_success(self):
        with patch("leadgen.services.probe_sendgrid_connectivity", return_value={"dns_ok": True, "resolved_hosts": ["1.2.3.4"]}):
            diagnostics = send_test_email_diagnostic("gopala.krishnan@inditech.co.in")

        self.assertTrue(diagnostics["success"])
        self.assertEqual(diagnostics["delivery"]["transport"], "django-email-backend")
        self.assertEqual(diagnostics["to_email"], "gopala.krishnan@inditech.co.in")
        self.assertEqual(len(mail.outbox), 1)

    def test_pending_collections_groups_invoiced_and_future_installments(self):
        contract_collection = ContractCollection.objects.create(
            company_name="Collections Co",
            sales_manager=self.sales_manager,
            contract_value="90000.00",
            created_by=self.supervisor,
        )
        ContractCollectionInstallment.objects.create(
            contract_collection=contract_collection,
            position=1,
            installment_amount="30000.00",
            invoice_date=timezone.localdate() - timedelta(days=2),
            expected_collection_date=timezone.localdate() + timedelta(days=5),
        )
        ContractCollectionInstallment.objects.create(
            contract_collection=contract_collection,
            position=2,
            installment_amount="30000.00",
            invoice_date=timezone.localdate() + timedelta(days=4),
            expected_collection_date=timezone.localdate() + timedelta(days=10),
        )
        summary = build_pending_collections(ContractCollection.objects.all(), timezone.localdate())
        self.assertEqual(summary["invoiced_pending"].count(), 1)
        self.assertEqual(summary["yet_to_invoice"].count(), 1)

    def test_finance_manager_can_open_contracts_dashboard(self):
        ContractCollection.objects.create(
            company_name="Finance Visible Co",
            sales_manager=self.sales_manager,
            contract_value="120000.00",
            created_by=self.supervisor,
        )
        self.client.force_login(self.finance_manager)
        response = self.client.get("/contracts/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Finance Visible Co")

    def test_supervisor_can_open_contract_detail_page(self):
        contract_collection = ContractCollection.objects.create(
            company_name="Detail Co",
            sales_manager=self.sales_manager,
            contract_value="120000.00",
            created_by=self.supervisor,
        )
        self.client.force_login(self.supervisor)
        response = self.client.get(f"/contracts/{contract_collection.pk}/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Detail Co")

    def test_supervisor_can_open_staff_dashboard_from_staff_list(self):
        self.client.force_login(self.supervisor)
        response = self.client.get("/supervisor/staff/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, f"/supervisor/staff/{self.staff.pk}/dashboard/")

    def test_system_admin_can_open_manage_users_page(self):
        self._force_login_as_supervisor_access("gopala.krishnan@inditech.co.in")
        response = self.client.get("/supervisor/users/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Manage users")
        self.assertContains(response, "System admin")
        self.assertContains(response, "gopala.krishnan@inditech.co.in")
        self.assertContains(response, "vamshi.alle@inditech.co.in")
        self.assertContains(response, self.staff.name)
        self.assertContains(response, self.sales_manager.email)
        self.assertContains(response, self.finance_manager.email)

    def test_supervisor_can_add_supervisor_access_email(self):
        self._force_login_as_supervisor_access("gopala.krishnan@inditech.co.in")
        response = self.client.post("/supervisor/users/", {"email": "new.supervisor@example.com"})
        self.assertRedirects(response, "/supervisor/users/")
        self.assertTrue(
            SupervisorAccessEmail.objects.filter(email="new.supervisor@example.com", is_active=True).exists()
        )

    def test_supervisor_can_deactivate_supervisor_access_email(self):
        access_email = SupervisorAccessEmail.objects.create(email="temporary.supervisor@example.com", is_active=True)
        self._force_login_as_supervisor_access("gopala.krishnan@inditech.co.in")
        response = self.client.post(f"/supervisor/users/supervisor-access/{access_email.pk}/delete/")
        self.assertRedirects(response, "/supervisor/users/")
        access_email.refresh_from_db()
        self.assertFalse(access_email.is_active)

    def test_leadgen_supervisor_manage_users_page_only_shows_staff_management(self):
        self._force_login_as_supervisor_access("bhavesh.kataria@inditech.co.in")
        response = self.client.get("/supervisor/users/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Lead gen supervisor view")
        self.assertContains(response, self.staff.name)
        self.assertNotContains(response, "Google accounts with full system-wide administration rights")
        self.assertNotContains(response, self.sales_manager.email)
        self.assertNotContains(response, self.finance_manager.email)

    def test_leadgen_supervisor_cannot_access_sales_manager_create(self):
        self._force_login_as_supervisor_access("bhavesh.kataria@inditech.co.in")
        response = self.client.get("/supervisor/sales-managers/add/")
        self.assertEqual(response.status_code, 403)

    def test_leadgen_supervisor_dashboard_removes_crossed_out_sections(self):
        self._force_login_as_supervisor_access("bhavesh.kataria@inditech.co.in")
        response = self.client.get("/supervisor/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Manage users")
        self.assertNotContains(response, "Active sales managers")
        self.assertNotContains(response, "Active finance managers")
        self.assertNotContains(response, "Accepted prospects")
        self.assertNotContains(response, "All prospects")
        self.assertNotContains(response, "Add prospect")
        self.assertNotContains(response, "Five unanswered attempts")
        self.assertNotContains(response, "Compare yesterday's target with imported call attempts for each lead gen staff member")
        self.assertNotContains(response, "Track WhatsApp proofs, automated reminder emails, and missed reminder steps")
        self.assertNotContains(response, "Open any lead gen staff dashboard from one place")
        self.assertNotContains(response, "Move conducted meetings straight into sales follow-through")
        self.assertNotContains(response, "Recent imported calls")

    def test_system_admin_dashboard_keeps_system_sections(self):
        self._force_login_as_supervisor_access("gopala.krishnan@inditech.co.in")
        response = self.client.get("/supervisor/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Active sales managers")
        self.assertContains(response, "Active finance managers")
        self.assertContains(response, "Accepted prospects")
        self.assertContains(response, "All prospects")
        self.assertContains(response, "Add prospect")
        self.assertContains(response, "Five unanswered attempts")
        self.assertContains(response, "Compare yesterday's target with imported call attempts for each lead gen staff member")
        self.assertContains(response, "Track WhatsApp proofs, automated reminder emails, and missed reminder steps")
        self.assertContains(response, "Open any lead gen staff dashboard from one place")
        self.assertContains(response, "Move conducted meetings straight into sales follow-through")
        self.assertContains(response, "Recent imported calls")

    def test_supervisor_staff_dashboard_shows_staff_name(self):
        self.client.force_login(self.supervisor)
        response = self.client.get(f"/supervisor/staff/{self.staff.pk}/dashboard/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.staff.name)
        self.assertContains(response, "Supervisor view of this staff member")

    def test_staff_prospect_list_can_filter_to_accepted(self):
        Prospect.objects.create(
            company_name="Awaiting Review Co",
            contact_name="Pending Person",
            linkedin_url="https://linkedin.com/in/pending",
            phone_number="+919812340000",
            assigned_to=self.staff,
            created_by=self.staff,
            approval_status=Prospect.APPROVAL_PENDING,
            workflow_status=Prospect.WORKFLOW_PENDING_REVIEW,
        )
        self.client.force_login(self.staff)
        response = self.client.get("/staff/prospects/?view=accepted")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.prospect.company_name)
        self.assertNotContains(response, "Awaiting Review Co")

    def test_supervisor_can_open_filtered_staff_prospect_list(self):
        Prospect.objects.create(
            company_name="Awaiting Review Co",
            contact_name="Pending Person",
            linkedin_url="https://linkedin.com/in/pending",
            phone_number="+919812340001",
            assigned_to=self.staff,
            created_by=self.staff,
            approval_status=Prospect.APPROVAL_PENDING,
            workflow_status=Prospect.WORKFLOW_PENDING_REVIEW,
        )
        self.client.force_login(self.supervisor)
        response = self.client.get(f"/supervisor/staff/{self.staff.pk}/prospects/?view=accepted")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.prospect.company_name)
        self.assertNotContains(response, "Awaiting Review Co")

    def test_supervisor_staff_prospect_list_shows_move_and_delete_actions(self):
        self.client.force_login(self.supervisor)
        response = self.client.get(f"/supervisor/staff/{self.staff.pk}/prospects/?view=all")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, f"/supervisor/prospects/{self.prospect.pk}/move/")
        self.assertContains(response, f"/supervisor/prospects/{self.prospect.pk}/delete/")

    def test_supervisor_can_move_prospect_from_staff_assigned_list(self):
        self.client.force_login(self.supervisor)
        next_url = f"/supervisor/staff/{self.staff.pk}/prospects/?view=all"
        response = self.client.post(
            f"/supervisor/prospects/{self.prospect.pk}/move/?next={next_url}",
            {
                "assigned_to": self.other_staff.pk,
                "supervisor_notes": "Move to another caller.",
                "next": next_url,
            },
        )
        self.assertRedirects(response, next_url)
        self.prospect.refresh_from_db()
        self.assertEqual(self.prospect.assigned_to, self.other_staff)
        self.assertEqual(self.prospect.supervisor_notes, "Move to another caller.")

    def test_supervisor_can_filter_staff_prospects_to_yesterday_calls(self):
        target_date = datetime(2026, 4, 14).date()
        started_at = timezone.make_aware(datetime(2026, 4, 14, 11, 0))
        batch = CallImportBatch.objects.create(
            import_date=target_date,
            uploaded_file=SimpleUploadedFile("calls.xlsx", b"placeholder"),
            imported_by=self.supervisor,
        )
        CallLog.objects.create(
            call_sid="YESTERDAY-CALL-1",
            batch=batch,
            staff=self.staff,
            prospect=self.prospect,
            started_at=started_at,
            ended_at=started_at + timedelta(minutes=2),
            from_number=self.staff.calling_number,
            to_number=self.prospect.phone_number,
            direction="outbound",
            crm_status=CallLog.STATUS_COMPLETED,
            was_connected=True,
            matched=True,
            raw_data={},
        )
        ProspectStatusUpdate.objects.create(
            prospect=self.prospect,
            staff=self.staff,
            outcome=ProspectStatusUpdate.OUTCOME_FOLLOW_UP,
            reason="Call connected yesterday.",
        )
        ProspectStatusUpdate.objects.filter(prospect=self.prospect, staff=self.staff).update(created_at=started_at)
        meeting = Meeting.objects.create(
            prospect=self.prospect,
            scheduled_by=self.staff,
            scheduled_for=timezone.make_aware(datetime(2026, 4, 16, 15, 0)),
            prospect_email="prospect@example.com",
            meeting_platform=Meeting.PLATFORM_TEAMS,
        )
        Meeting.objects.filter(pk=meeting.pk).update(created_at=started_at)

        untouched = Prospect.objects.create(
            company_name="No Call Yesterday Co",
            contact_name="Other Prospect",
            linkedin_url="https://linkedin.com/in/no-call-yesterday",
            phone_number="+919812340009",
            assigned_to=self.staff,
            created_by=self.staff,
            approval_status=Prospect.APPROVAL_ACCEPTED,
            workflow_status=Prospect.WORKFLOW_READY_TO_CALL,
        )
        self.client.force_login(self.supervisor)
        with patch("leadgen.views._yesterday_bounds", return_value=(target_date, started_at.replace(hour=0, minute=0, second=0, microsecond=0), started_at.replace(hour=23, minute=59, second=59, microsecond=999999))):
            response = self.client.get(f"/supervisor/staff/{self.staff.pk}/prospects/?view=yesterday_calls")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.prospect.company_name)
        self.assertContains(response, "Call connected yesterday.")
        self.assertContains(response, "Apr 16, 2026 3:00 p.m.")
        self.assertNotContains(response, untouched.company_name)

    def test_supervisor_can_open_all_prospects_page(self):
        self.client.force_login(self.supervisor)
        response = self.client.get("/supervisor/prospects/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.prospect.company_name)
        self.assertContains(response, self.staff.name)

    def test_daily_target_report_includes_manual_status_update_counts(self):
        target_date = datetime(2026, 4, 14).date()
        started_at = timezone.make_aware(datetime(2026, 4, 14, 11, 0))
        batch = CallImportBatch.objects.create(
            import_date=target_date,
            uploaded_file=SimpleUploadedFile("calls.xlsx", b"placeholder"),
            imported_by=self.supervisor,
        )
        CallLog.objects.create(
            call_sid="REPORT-CALL-1",
            batch=batch,
            staff=self.staff,
            prospect=self.prospect,
            started_at=started_at,
            ended_at=started_at + timedelta(minutes=2),
            from_number=self.staff.calling_number,
            to_number=self.prospect.phone_number,
            direction="outbound",
            crm_status=CallLog.STATUS_COMPLETED,
            was_connected=True,
            matched=True,
            raw_data={},
        )
        follow_up = ProspectStatusUpdate.objects.create(
            prospect=self.prospect,
            staff=self.staff,
            outcome=ProspectStatusUpdate.OUTCOME_FOLLOW_UP,
            reason="Follow up booked.",
        )
        scheduled = ProspectStatusUpdate.objects.create(
            prospect=self.prospect,
            staff=self.staff,
            outcome=ProspectStatusUpdate.OUTCOME_SCHEDULED,
            reason="Meeting booked.",
        )
        ProspectStatusUpdate.objects.filter(pk=follow_up.pk).update(created_at=started_at)
        ProspectStatusUpdate.objects.filter(pk=scheduled.pk).update(created_at=started_at + timedelta(minutes=5))

        report = build_daily_target_report(target_date=target_date, tz_name="Asia/Kolkata")
        row = next(item for item in report["staff_rows"] if item["staff"] == self.staff)
        self.assertEqual(row["actual_attempts"], 1)
        self.assertEqual(row["follow_up_updates"], 1)
        self.assertEqual(row["declined_updates"], 0)
        self.assertEqual(row["scheduled_updates"], 1)

    def test_supervisor_can_delete_any_prospect(self):
        prospect = Prospect.objects.create(
            company_name="Delete Me Co",
            contact_name="Delete Person",
            linkedin_url="https://linkedin.com/in/delete-person",
            phone_number="+919812349999",
            assigned_to=self.other_staff,
            created_by=self.staff,
            approval_status=Prospect.APPROVAL_ACCEPTED,
            workflow_status=Prospect.WORKFLOW_READY_TO_CALL,
        )
        self.client.force_login(self.supervisor)
        response = self.client.post(f"/supervisor/prospects/{prospect.pk}/delete/")
        self.assertRedirects(response, "/supervisor/prospects/")
        self.assertFalse(Prospect.objects.filter(pk=prospect.pk).exists())

    def test_supervisor_can_filter_meetings_by_date(self):
        same_day_meeting = apply_call_outcome(
            self.prospect,
            self.staff,
            {
                "outcome": "scheduled",
                "scheduled_for": datetime(2026, 4, 23, 17, 30),
                "prospect_email": "prospect@example.com",
                "meeting_platform": Meeting.PLATFORM_TEAMS,
                "reason": "",
                "follow_up_date": None,
            },
        )
        happened_prospect = Prospect.objects.create(
            company_name="Happened Co",
            contact_name="Happened Person",
            linkedin_url="https://linkedin.com/in/happened-person",
            phone_number="+919812347777",
            assigned_to=self.other_staff,
            created_by=self.other_staff,
            approval_status=Prospect.APPROVAL_ACCEPTED,
            workflow_status=Prospect.WORKFLOW_READY_TO_CALL,
        )
        happened_meeting = Meeting.objects.create(
            prospect=happened_prospect,
            scheduled_by=self.other_staff,
            scheduled_for=timezone.make_aware(datetime(2026, 4, 23, 12, 0)),
            prospect_email="happened@example.com",
            meeting_platform=Meeting.PLATFORM_ZOOM,
            status=Meeting.STATUS_HAPPENED,
            outcome_updated_at=timezone.make_aware(datetime(2026, 4, 23, 12, 45)),
        )
        other_day_prospect = Prospect.objects.create(
            company_name="Other Day Co",
            contact_name="Other Day Person",
            linkedin_url="https://linkedin.com/in/other-day-person",
            phone_number="+919812347778",
            assigned_to=self.other_staff,
            created_by=self.other_staff,
            approval_status=Prospect.APPROVAL_ACCEPTED,
            workflow_status=Prospect.WORKFLOW_READY_TO_CALL,
        )
        other_day_meeting = Meeting.objects.create(
            prospect=other_day_prospect,
            scheduled_by=self.other_staff,
            scheduled_for=timezone.make_aware(datetime(2026, 4, 24, 12, 0)),
            prospect_email="otherday@example.com",
            meeting_platform=Meeting.PLATFORM_ZOOM,
            status=Meeting.STATUS_SCHEDULED,
        )

        self.client.force_login(self.supervisor)
        response = self.client.get("/supervisor/meetings/?meeting_date=2026-04-23")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, same_day_meeting.prospect.company_name)
        self.assertContains(response, happened_meeting.prospect.company_name)
        self.assertNotContains(response, other_day_meeting.prospect.company_name)
        self.assertEqual(response.context["summary"]["scheduled"], 1)
        self.assertEqual(response.context["summary"]["happened"], 1)
        self.assertEqual(response.context["summary"]["total"], 2)

    def test_supervisor_can_delete_scheduled_meeting_and_revert_prospect(self):
        meeting = apply_call_outcome(
            self.prospect,
            self.staff,
            {
                "outcome": "scheduled",
                "scheduled_for": datetime(2026, 4, 23, 17, 30),
                "prospect_email": "prospect@example.com",
                "meeting_platform": Meeting.PLATFORM_TEAMS,
                "reason": "",
                "follow_up_date": None,
            },
        )

        self.client.force_login(self.supervisor)
        response = self.client.post(f"/supervisor/meetings/{meeting.pk}/delete/", {"meeting_date": "2026-04-23"})

        self.assertRedirects(response, "/supervisor/meetings/?meeting_date=2026-04-23")
        self.assertFalse(Meeting.objects.filter(pk=meeting.pk).exists())
        self.prospect.refresh_from_db()
        self.assertEqual(self.prospect.workflow_status, Prospect.WORKFLOW_FOLLOW_UP)
        self.assertEqual(self.prospect.follow_up_reason, "Meeting deleted by supervisor")
        latest_update = self.prospect.status_updates.order_by("-created_at", "-pk").first()
        self.assertEqual(latest_update.outcome, ProspectStatusUpdate.OUTCOME_FOLLOW_UP)
        self.assertEqual(latest_update.reason, "Meeting deleted by supervisor")

# Create your tests here.
