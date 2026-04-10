import logging
import base64
import socket
from decimal import Decimal
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from django.conf import settings
from django.db import connection
from django.core.mail import EmailMultiAlternatives
from django.db import transaction
from django.db.models import Avg, Count, F, Max, Q, Sum
from django.template.loader import render_to_string
from django.utils import timezone
from icalendar import Calendar, Event, vCalAddress, vText
from openpyxl import load_workbook
from sendgrid import SendGridAPIClient

from .models import (
    CallImportBatch,
    CallLog,
    ContractCollection,
    ContractCollectionContact,
    ContractCollectionFile,
    ContractCollectionInstallment,
    Meeting,
    MeetingReminder,
    Prospect,
    ProspectStatusUpdate,
    SalesConversation,
    SalesConversationBrand,
    SalesConversationContact,
    SalesConversationFile,
    SystemSetting,
    User,
)


logger = logging.getLogger(__name__)


CRM_STATUS_MAP = {
    "completed": Prospect.CRM_COMPLETED,
    "no-answer": Prospect.CRM_NO_ANSWER,
    "busy": Prospect.CRM_BUSY,
    "failed": Prospect.CRM_FAILED,
}

ACTIVE_CALLING_WORKFLOWS = {
    Prospect.WORKFLOW_READY_TO_CALL,
    Prospect.WORKFLOW_FOLLOW_UP,
}

REMINDER_GRACE_WINDOWS = {
    MeetingReminder.TYPE_WHATSAPP_INITIAL: timedelta(minutes=30),
    MeetingReminder.TYPE_EMAIL_DAY_BEFORE: timedelta(hours=1),
    MeetingReminder.TYPE_EMAIL_SAME_DAY: timedelta(hours=1),
    MeetingReminder.TYPE_WHATSAPP_FINAL: timedelta(minutes=15),
}


def normalize_phone(value):
    if value is None:
        return ""
    text = str(value).strip()
    if not text:
        return ""
    if text.startswith("+"):
        return "+" + "".join(ch for ch in text[1:] if ch.isdigit())
    digits = "".join(ch for ch in text if ch.isdigit())
    return f"+{digits}" if digits else ""


def parse_report_datetime(value, tz_name):
    if not value:
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        dt = datetime.fromisoformat(str(value))
    if timezone.is_naive(dt):
        return timezone.make_aware(dt, ZoneInfo(tz_name))
    return dt.astimezone(ZoneInfo(tz_name))


def _prospect_latest_log(prospect):
    return prospect.call_logs.order_by("-started_at", "-created_at").first()


def refresh_prospect_call_metrics(prospect):
    aggregates = prospect.call_logs.aggregate(
        total_attempts=Count("id"),
        total_connected=Count("id", filter=Q(was_connected=True)),
        last_started_at=Max("started_at"),
        total_duration=Sum("duration_seconds"),
    )
    last_log = _prospect_latest_log(prospect)
    prospect.total_call_attempts = aggregates["total_attempts"] or 0
    prospect.total_connected_calls = aggregates["total_connected"] or 0
    prospect.latest_call_attempt_at = aggregates["last_started_at"]
    prospect.latest_call_duration_seconds = last_log.duration_seconds if last_log else 0
    if last_log:
        prospect.latest_crm_status = CRM_STATUS_MAP.get(last_log.crm_status, Prospect.CRM_NOT_CALLED)
    prospect.save(
        update_fields=[
            "total_call_attempts",
            "total_connected_calls",
            "latest_call_attempt_at",
            "latest_call_duration_seconds",
            "latest_crm_status",
            "updated_at",
        ]
    )


def unanswered_attempt_count(prospect):
    return prospect.call_logs.filter(crm_status=CallLog.STATUS_NO_ANSWER).count()


def refresh_prospect_import_state(prospect):
    if prospect.approval_status != Prospect.APPROVAL_ACCEPTED:
        return
    if prospect.workflow_status == Prospect.WORKFLOW_INVALID_NUMBER:
        return
    if prospect.latest_crm_status == Prospect.CRM_FAILED:
        prospect.workflow_status = Prospect.WORKFLOW_INVALID_NUMBER
        prospect.system_action_note = "Reported as an invalid number by the Exotel import."
        prospect.follow_up_date = None
        prospect.follow_up_reason = ""
        prospect.save(
            update_fields=[
                "workflow_status",
                "system_action_note",
                "follow_up_date",
                "follow_up_reason",
                "updated_at",
            ]
        )
        return
    if prospect.workflow_status not in ACTIVE_CALLING_WORKFLOWS:
        return
    unanswered_count = unanswered_attempt_count(prospect)
    if (
        prospect.latest_crm_status == Prospect.CRM_NO_ANSWER
        and unanswered_count - prospect.no_answer_reset_count >= 5
    ):
        prospect.workflow_status = Prospect.WORKFLOW_SUPERVISOR_ACTION
        prospect.system_action_note = "Attempted five times with no answer."
        prospect.save(update_fields=["workflow_status", "system_action_note", "updated_at"])


def database_healthcheck():
    with connection.cursor() as cursor:
        cursor.execute("SELECT 1")
        row = cursor.fetchone()
    return row == (1,)


def build_supervisor_report(start_date, end_date, tz_name):
    tz = ZoneInfo(tz_name)
    start_dt = timezone.make_aware(datetime.combine(start_date, datetime.min.time()), tz)
    end_dt = timezone.make_aware(datetime.combine(end_date, datetime.max.time()), tz)

    calls = CallLog.objects.filter(started_at__range=(start_dt, end_dt))
    status_updates = ProspectStatusUpdate.objects.filter(created_at__range=(start_dt, end_dt))
    meetings_created = Meeting.objects.filter(created_at__range=(start_dt, end_dt))
    meetings_outcome = Meeting.objects.filter(outcome_updated_at__range=(start_dt, end_dt))
    imports = CallImportBatch.objects.filter(import_date__range=(start_date, end_date))

    summary = {
        "attempts": calls.count(),
        "connects": calls.filter(was_connected=True).count(),
        "connect_rate": 0,
        "avg_connected_duration": calls.filter(was_connected=True).aggregate(avg=Avg("duration_seconds"))["avg"] or 0,
        "no_answer_count": calls.filter(crm_status=CallLog.STATUS_NO_ANSWER).count(),
        "busy_count": calls.filter(crm_status=CallLog.STATUS_BUSY).count(),
        "failed_count": calls.filter(crm_status=CallLog.STATUS_FAILED).count(),
        "follow_ups": status_updates.filter(outcome=ProspectStatusUpdate.OUTCOME_FOLLOW_UP).count(),
        "declines": status_updates.filter(outcome=ProspectStatusUpdate.OUTCOME_DECLINED).count(),
        "meetings_scheduled": meetings_created.count(),
        "meetings_happened": meetings_outcome.filter(status=Meeting.STATUS_HAPPENED).count(),
        "meetings_not_happened": meetings_outcome.filter(status=Meeting.STATUS_DID_NOT_HAPPEN).count(),
        "imports_count": imports.count(),
        "imported_rows": imports.aggregate(total=Sum("total_rows"))["total"] or 0,
        "unmatched_import_rows": imports.aggregate(total=Sum("unmatched_rows"))["total"] or 0,
        "duplicate_import_rows": imports.aggregate(total=Sum("duplicate_rows"))["total"] or 0,
    }
    if summary["attempts"]:
        summary["connect_rate"] = round((summary["connects"] / summary["attempts"]) * 100, 1)

    staff_metrics = []
    for staff in User.objects.filter(role=User.ROLE_STAFF).order_by("name"):
        staff_calls = calls.filter(staff=staff)
        attempts = staff_calls.count()
        connects = staff_calls.filter(was_connected=True).count()
        scheduled = meetings_created.filter(scheduled_by=staff).count()
        happened = meetings_outcome.filter(scheduled_by=staff, status=Meeting.STATUS_HAPPENED).count()
        not_happened = meetings_outcome.filter(scheduled_by=staff, status=Meeting.STATUS_DID_NOT_HAPPEN).count()
        follow_ups = status_updates.filter(staff=staff, outcome=ProspectStatusUpdate.OUTCOME_FOLLOW_UP).count()
        declines = status_updates.filter(staff=staff, outcome=ProspectStatusUpdate.OUTCOME_DECLINED).count()
        staff_metrics.append(
            {
                "staff": staff,
                "attempts": attempts,
                "connects": connects,
                "connect_rate": round((connects / attempts) * 100, 1) if attempts else 0,
                "follow_ups": follow_ups,
                "declines": declines,
                "meetings_scheduled": scheduled,
                "meetings_happened": happened,
                "meetings_not_happened": not_happened,
            }
        )

    import_batches = list(imports.order_by("-import_date", "-created_at")[:10])
    return {
        "summary": summary,
        "staff_metrics": staff_metrics,
        "import_batches": import_batches,
        "start_dt": start_dt,
        "end_dt": end_dt,
    }


def build_daily_target_report(target_date, tz_name):
    tz = ZoneInfo(tz_name)
    start_dt = timezone.make_aware(datetime.combine(target_date, datetime.min.time()), tz)
    end_dt = timezone.make_aware(datetime.combine(target_date, datetime.max.time()), tz)
    staff_rows = []

    for staff in User.objects.filter(role=User.ROLE_STAFF, is_active=True).order_by("name", "email"):
        assigned_prospects = Prospect.objects.filter(
            assigned_to=staff,
            approval_status=Prospect.APPROVAL_ACCEPTED,
            created_at__lte=end_dt,
        ).filter(Q(accepted_at__isnull=True) | Q(accepted_at__lte=end_dt))
        target_queryset = assigned_prospects.filter(
            Q(workflow_status__in=ACTIVE_CALLING_WORKFLOWS)
            | Q(call_logs__started_at__range=(start_dt, end_dt))
        ).distinct()
        actual_attempts = CallLog.objects.filter(staff=staff, started_at__range=(start_dt, end_dt)).count()
        staff_rows.append(
            {
                "staff": staff,
                "target_count": target_queryset.count(),
                "actual_attempts": actual_attempts,
                "delta": actual_attempts - target_queryset.count(),
            }
        )

    return {
        "target_date": target_date,
        "staff_rows": staff_rows,
    }


def import_exotel_report(batch):
    workbook = load_workbook(batch.uploaded_file.path, data_only=True)
    sheet = workbook[workbook.sheetnames[0]]
    header = [str(value).strip() for value in next(sheet.iter_rows(values_only=True))]
    required = ["Call SID", "Start Time", "End Time", "From", "To", "Direction", "Status"]
    missing = [col for col in required if col not in header]
    if missing:
        raise ValueError(f"Missing required columns: {', '.join(missing)}")

    indexes = {name: header.index(name) for name in required}
    touched_prospects = set()
    tz_name = SystemSetting.load().default_timezone

    for row in sheet.iter_rows(min_row=2, values_only=True):
        if not any(row):
            continue
        batch.total_rows += 1
        row_data = {col: row[idx] for col, idx in indexes.items()}
        call_sid = str(row_data["Call SID"]).strip()
        from_number = normalize_phone(row_data["From"])
        to_number = normalize_phone(row_data["To"])
        crm_status = str(row_data["Status"]).strip().lower()
        if crm_status not in dict(CallLog.STATUS_CHOICES):
            crm_status = CallLog.STATUS_FAILED

        staff = User.objects.filter(role=User.ROLE_STAFF, calling_number=from_number).first()
        prospect = None
        if staff:
            prospect = Prospect.objects.filter(assigned_to=staff, phone_number=to_number).first()
        matched = bool(staff and prospect)
        defaults = {
            "batch": batch,
            "staff": staff,
            "prospect": prospect,
            "started_at": parse_report_datetime(row_data["Start Time"], tz_name),
            "ended_at": parse_report_datetime(row_data["End Time"], tz_name),
            "from_number": from_number,
            "to_number": to_number,
            "direction": str(row_data["Direction"]).strip().lower(),
            "crm_status": crm_status,
            "was_connected": crm_status == CallLog.STATUS_COMPLETED,
            "matched": matched,
            "raw_data": {k: str(v) if v is not None else "" for k, v in row_data.items()},
        }
        if defaults["started_at"] and defaults["ended_at"]:
            defaults["duration_seconds"] = max(
                0,
                int((defaults["ended_at"] - defaults["started_at"]).total_seconds()),
            )
        else:
            defaults["duration_seconds"] = 0
        _, created = CallLog.objects.update_or_create(call_sid=call_sid, defaults=defaults)
        if created:
            if matched:
                batch.matched_rows += 1
            else:
                batch.unmatched_rows += 1
        else:
            batch.duplicate_rows += 1
        if prospect:
            touched_prospects.add(prospect.pk)

    batch.save(
        update_fields=[
            "total_rows",
            "matched_rows",
            "unmatched_rows",
            "duplicate_rows",
        ]
    )
    for prospect in Prospect.objects.filter(pk__in=touched_prospects):
        refresh_prospect_call_metrics(prospect)
        refresh_prospect_import_state(prospect)


def unique_emails(*groups):
    seen = set()
    emails = []
    for group in groups:
        for email in group:
            normalized = (email or "").strip().lower()
            if normalized and normalized not in seen:
                seen.add(normalized)
                emails.append(normalized)
    return emails


def mask_secret(value):
    if not value:
        return ""
    if len(value) <= 8:
        return "*" * len(value)
    return f"{value[:4]}...{value[-4:]}"


def probe_sendgrid_connectivity():
    try:
        addresses = socket.getaddrinfo("api.sendgrid.com", 443, type=socket.SOCK_STREAM)
        hosts = sorted({item[4][0] for item in addresses if item[4]})
        return {"dns_ok": True, "resolved_hosts": hosts[:5]}
    except Exception as exc:
        return {
            "dns_ok": False,
            "error_type": exc.__class__.__name__,
            "error_message": str(exc),
        }


def build_calendar_invite(meeting, settings_obj):
    tz = ZoneInfo(settings_obj.default_timezone)
    start = meeting.scheduled_for.astimezone(tz)
    end = start + timedelta(minutes=30)

    calendar = Calendar()
    calendar.add("prodid", "-//Leadgen Tracking System//inditech.co.in//")
    calendar.add("version", "2.0")
    calendar.add("method", "REQUEST")

    event = Event()
    event.add("uid", f"meeting-{meeting.pk}@leadgen.local")
    event.add("summary", f"Meeting with {meeting.prospect.contact_name} - {meeting.prospect.company_name}")
    event.add("dtstart", start)
    event.add("dtend", end)
    event.add("dtstamp", timezone.now())
    if meeting.meeting_link:
        event.add("location", meeting.meeting_link)
        event.add("url", meeting.meeting_link)
    description_lines = [
        f"Prospect: {meeting.prospect.contact_name}",
        f"Company: {meeting.prospect.company_name}",
        f"LinkedIn: {meeting.prospect.linkedin_url}",
        f"Lead Gen Staff: {meeting.scheduled_by.name}",
        f"Meeting Platform: {meeting.get_meeting_platform_display()}",
    ]
    description_lines.extend(meeting.meeting_access_lines)
    event.add(
        "description",
        "\n".join([line for line in description_lines if line]),
    )
    organizer = vCalAddress(f"MAILTO:{settings_obj.supervisor_sender_email}")
    organizer.params["cn"] = vText(settings_obj.supervisor_name)
    organizer.params["role"] = vText("CHAIR")
    event["organizer"] = organizer
    for email in meeting.recipient_emails:
        attendee = vCalAddress(f"MAILTO:{email}")
        attendee.params["cn"] = vText(email)
        attendee.params["role"] = vText("REQ-PARTICIPANT")
        event.add("attendee", attendee, encode=0)
    calendar.add_component(event)
    return calendar.to_ical()


def _deliver_email(subject, html_body, text_body, to_emails, cc_emails=None, attachments=None):
    cc_emails = cc_emails or []
    attachments = attachments or []
    if settings.SENDGRID_API_KEY:
        payload = {
            "personalizations": [
                {
                    "to": [{"email": email} for email in to_emails],
                    "subject": subject,
                }
            ],
            "from": {"email": settings.DEFAULT_FROM_EMAIL},
            "reply_to": {"email": settings.REPLY_TO_EMAIL},
            "content": [
                {"type": "text/plain", "value": text_body},
                {"type": "text/html", "value": html_body},
            ],
        }
        if cc_emails:
            payload["personalizations"][0]["cc"] = [{"email": email} for email in cc_emails]
        if attachments:
            payload["attachments"] = [
                {
                    "content": base64.b64encode(attachment["content"]).decode("utf-8"),
                    "filename": attachment["filename"],
                    "type": attachment["type"],
                    "disposition": "attachment",
                }
                for attachment in attachments
            ]
        response = SendGridAPIClient(settings.SENDGRID_API_KEY).client.mail.send.post(request_body=payload)
        response_body = getattr(response, "body", b"")
        if isinstance(response_body, bytes):
            response_body = response_body.decode("utf-8", errors="ignore")
        if response.status_code < 200 or response.status_code >= 300:
            raise RuntimeError(f"SendGrid returned status {response.status_code}: {response_body}")
        return {
            "transport": "sendgrid",
            "status_code": response.status_code,
            "response_body": response_body[:1000],
            "payload_summary": {
                "from_email": settings.DEFAULT_FROM_EMAIL,
                "reply_to": settings.REPLY_TO_EMAIL,
                "to_count": len(to_emails),
                "cc_count": len(cc_emails),
                "attachment_count": len(attachments),
            },
        }

    email = EmailMultiAlternatives(
        subject=subject,
        body=text_body,
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=to_emails,
        cc=cc_emails,
        reply_to=[settings.REPLY_TO_EMAIL],
    )
    email.attach_alternative(html_body, "text/html")
    for attachment in attachments:
        email.attach(attachment["filename"], attachment["content"], attachment["type"])
    sent_count = email.send()
    return {
        "transport": "django-email-backend",
        "email_backend": settings.EMAIL_BACKEND,
        "sent_count": sent_count,
        "payload_summary": {
            "from_email": settings.DEFAULT_FROM_EMAIL,
            "reply_to": settings.REPLY_TO_EMAIL,
            "to_count": len(to_emails),
            "cc_count": len(cc_emails),
            "attachment_count": len(attachments),
        },
    }


def send_email(subject, html_body, text_body, to_emails, cc_emails=None, attachments=None):
    _deliver_email(
        subject=subject,
        html_body=html_body,
        text_body=text_body,
        to_emails=to_emails,
        cc_emails=cc_emails,
        attachments=attachments,
    )


def send_test_email_diagnostic(recipient_email):
    timestamp = timezone.now()
    subject = f"Leadgen email diagnostics {timestamp:%Y-%m-%d %H:%M:%S %Z}"
    html_body = (
        "<p>This is a diagnostic test email from the Leadgen Tracking System.</p>"
        f"<p>Timestamp: {timestamp.isoformat()}</p>"
        f"<p>Recipient: {recipient_email}</p>"
        f"<p>From: {settings.DEFAULT_FROM_EMAIL}</p>"
        f"<p>Reply-To: {settings.REPLY_TO_EMAIL}</p>"
    )
    text_body = (
        "This is a diagnostic test email from the Leadgen Tracking System.\n"
        f"Timestamp: {timestamp.isoformat()}\n"
        f"Recipient: {recipient_email}\n"
        f"From: {settings.DEFAULT_FROM_EMAIL}\n"
        f"Reply-To: {settings.REPLY_TO_EMAIL}\n"
    )
    diagnostics = {
        "success": False,
        "timestamp": timestamp.isoformat(),
        "app_env": settings.APP_ENV if hasattr(settings, "APP_ENV") else "",
        "email_backend": settings.EMAIL_BACKEND,
        "sendgrid_configured": bool(settings.SENDGRID_API_KEY),
        "sendgrid_api_key_masked": mask_secret(settings.SENDGRID_API_KEY),
        "from_email": settings.DEFAULT_FROM_EMAIL,
        "reply_to_email": settings.REPLY_TO_EMAIL,
        "to_email": recipient_email,
        "sendgrid_connectivity": probe_sendgrid_connectivity(),
    }
    try:
        delivery = _deliver_email(
            subject=subject,
            html_body=html_body,
            text_body=text_body,
            to_emails=[recipient_email],
        )
        diagnostics["success"] = True
        diagnostics["delivery"] = delivery
    except Exception as exc:
        diagnostics["error_type"] = exc.__class__.__name__
        diagnostics["error_message"] = str(exc)
        logger.exception("Email diagnostics failed for recipient=%s", recipient_email)
    return diagnostics


def send_meeting_invitation(meeting):
    settings_obj = SystemSetting.load()
    html_body = render_to_string(
        "emails/meeting_invite.html",
        {"meeting": meeting, "settings_obj": settings_obj},
    )
    text_body = render_to_string(
        "emails/meeting_invite.txt",
        {"meeting": meeting, "settings_obj": settings_obj},
    )
    send_email(
        subject=f"20-min: Outcome-Linked Campaign – Discussion with {meeting.prospect.company_name}",
        html_body=html_body,
        text_body=text_body,
        to_emails=[meeting.prospect_email],
        cc_emails=[email for email in meeting.recipient_emails if email != meeting.prospect_email],
        attachments=[
            {
                "filename": "meeting-invite.ics",
                "content": build_calendar_invite(meeting, settings_obj),
                "type": "text/calendar",
            }
        ],
    )
    meeting.mark_invite_sent()


def send_did_not_happen_email(meeting):
    settings_obj = SystemSetting.load()
    html_body = render_to_string(
        "emails/meeting_did_not_happen.html",
        {"meeting": meeting, "settings_obj": settings_obj},
    )
    text_body = render_to_string(
        "emails/meeting_did_not_happen.txt",
        {"meeting": meeting, "settings_obj": settings_obj},
    )
    send_email(
        subject=f"Meeting did not happen: {meeting.prospect.company_name} / {meeting.prospect.contact_name}",
        html_body=html_body,
        text_body=text_body,
        to_emails=[meeting.scheduled_by.email],
        cc_emails=[settings_obj.supervisor_sender_email],
    )


def meeting_reminder_due_at(meeting, reminder_type, tz_name):
    tz = ZoneInfo(tz_name)
    scheduled_local = meeting.scheduled_for.astimezone(tz)
    created_local = meeting.created_at.astimezone(tz)
    if reminder_type == MeetingReminder.TYPE_WHATSAPP_INITIAL:
        return created_local
    if reminder_type == MeetingReminder.TYPE_EMAIL_DAY_BEFORE:
        return scheduled_local - timedelta(hours=24)
    if reminder_type == MeetingReminder.TYPE_EMAIL_SAME_DAY:
        return timezone.make_aware(
            datetime.combine(scheduled_local.date(), datetime.min.time()).replace(hour=9),
            tz,
        )
    if reminder_type == MeetingReminder.TYPE_WHATSAPP_FINAL:
        return scheduled_local - timedelta(hours=1)
    raise ValueError(f"Unsupported reminder type: {reminder_type}")


def reminder_log_map(meeting):
    return {reminder.reminder_type: reminder for reminder in meeting.reminders.all()}


def reminder_status_for_meeting(meeting, reminder_type, tz_name, now=None):
    now = now or timezone.now()
    due_at = meeting_reminder_due_at(meeting, reminder_type, tz_name)
    reminder = reminder_log_map(meeting).get(reminder_type)
    if reminder:
        return {
            "due_at": due_at,
            "reminder": reminder,
            "is_sent": True,
            "is_missed": False,
        }
    grace = REMINDER_GRACE_WINDOWS[reminder_type]
    return {
        "due_at": due_at,
        "reminder": None,
        "is_sent": False,
        "is_missed": now >= due_at + grace,
    }


def log_whatsapp_reminder(meeting, reminder_type, recipient_number, screenshot, sent_by):
    defaults = {
        "recipient_number": normalize_phone(recipient_number) or recipient_number.strip(),
        "screenshot": screenshot,
        "sent_by": sent_by,
        "sent_at": timezone.now(),
    }
    reminder, _ = MeetingReminder.objects.update_or_create(
        meeting=meeting,
        reminder_type=reminder_type,
        defaults=defaults,
    )
    return reminder


def send_meeting_reminder_email(meeting, reminder_type):
    settings_obj = SystemSetting.load()
    if reminder_type == MeetingReminder.TYPE_EMAIL_DAY_BEFORE:
        html_template = "emails/meeting_reminder_day_before.html"
        text_template = "emails/meeting_reminder_day_before.txt"
    elif reminder_type == MeetingReminder.TYPE_EMAIL_SAME_DAY:
        html_template = "emails/meeting_reminder_same_day.html"
        text_template = "emails/meeting_reminder_same_day.txt"
    else:
        raise ValueError(f"Unsupported email reminder type: {reminder_type}")

    html_body = render_to_string(
        html_template,
        {"meeting": meeting, "settings_obj": settings_obj},
    )
    text_body = render_to_string(
        text_template,
        {"meeting": meeting, "settings_obj": settings_obj},
    )
    send_email(
        subject=f"20-min: Outcome-Linked Campaign - Discussion with {meeting.prospect.company_name}",
        html_body=html_body,
        text_body=text_body,
        to_emails=[meeting.prospect_email],
    )
    return MeetingReminder.objects.create(
        meeting=meeting,
        reminder_type=reminder_type,
        recipient_number="",
        sent_at=timezone.now(),
    )


def send_due_meeting_reminder_emails(now=None):
    now = now or timezone.now()
    settings_obj = SystemSetting.load()
    reminders_sent = 0
    meetings = Meeting.objects.filter(status=Meeting.STATUS_SCHEDULED).select_related("prospect", "scheduled_by")
    for meeting in meetings:
        existing = reminder_log_map(meeting)
        for reminder_type in (
            MeetingReminder.TYPE_EMAIL_DAY_BEFORE,
            MeetingReminder.TYPE_EMAIL_SAME_DAY,
        ):
            if reminder_type in existing:
                continue
            if meeting_reminder_due_at(meeting, reminder_type, settings_obj.default_timezone) <= now:
                try:
                    send_meeting_reminder_email(meeting, reminder_type)
                except Exception:
                    logger.exception(
                        "Failed to send meeting reminder email for meeting_id=%s reminder_type=%s",
                        meeting.pk,
                        reminder_type,
                    )
                    continue
                reminders_sent += 1
    return reminders_sent


def build_reminder_dashboard(tz_name, now=None):
    now = now or timezone.now()
    recent_happened_cutoff = now - timedelta(days=10)
    meetings = (
        Meeting.objects.select_related("prospect", "scheduled_by")
        .prefetch_related("reminders")
        .filter(
            Q(status=Meeting.STATUS_SCHEDULED)
            | Q(status=Meeting.STATUS_HAPPENED, outcome_updated_at__gte=recent_happened_cutoff)
        )
        .order_by("scheduled_for")
    )
    rows = []
    summary = {
        "first_whatsapp_sent": 0,
        "second_whatsapp_sent": 0,
        "missed_total": 0,
    }
    for meeting in meetings:
        first_whatsapp = reminder_status_for_meeting(meeting, MeetingReminder.TYPE_WHATSAPP_INITIAL, tz_name, now=now)
        day_before_email = reminder_status_for_meeting(meeting, MeetingReminder.TYPE_EMAIL_DAY_BEFORE, tz_name, now=now)
        same_day_email = reminder_status_for_meeting(meeting, MeetingReminder.TYPE_EMAIL_SAME_DAY, tz_name, now=now)
        final_whatsapp = reminder_status_for_meeting(meeting, MeetingReminder.TYPE_WHATSAPP_FINAL, tz_name, now=now)
        missed = [
            label
            for label, status in (
                ("First WhatsApp", first_whatsapp),
                ("24-hour email", day_before_email),
                ("9 a.m. email", same_day_email),
                ("Second WhatsApp", final_whatsapp),
            )
            if status["is_missed"]
        ]
        if first_whatsapp["is_sent"]:
            summary["first_whatsapp_sent"] += 1
        if final_whatsapp["is_sent"]:
            summary["second_whatsapp_sent"] += 1
        summary["missed_total"] += len(missed)
        rows.append(
            {
                "meeting": meeting,
                "first_whatsapp": first_whatsapp,
                "day_before_email": day_before_email,
                "same_day_email": same_day_email,
                "final_whatsapp": final_whatsapp,
                "missed": missed,
            }
        )
    return {
        "summary": summary,
        "rows": rows,
        "recent_happened_cutoff": recent_happened_cutoff,
    }


def active_sales_manager_emails():
    return list(
        User.objects.filter(role=User.ROLE_SALES_MANAGER, is_active=True)
        .exclude(email="")
        .values_list("email", flat=True)
    )


def default_sales_manager():
    managers = list(User.objects.filter(role=User.ROLE_SALES_MANAGER, is_active=True).order_by("name", "email")[:2])
    if len(managers) == 1:
        return managers[0]
    return None


def active_finance_manager_emails():
    return list(
        User.objects.filter(role=User.ROLE_FINANCE_MANAGER, is_active=True)
        .exclude(email="")
        .values_list("email", flat=True)
    )


def sync_sales_conversation_data(sales_conversation, cleaned_data, uploaded_files=None):
    uploaded_files = uploaded_files or {}
    sales_conversation.assigned_sales_manager = cleaned_data.get("assigned_sales_manager")
    sales_conversation.conversation_status = cleaned_data["conversation_status"]
    sales_conversation.proposal_status = cleaned_data["proposal_status"]
    sales_conversation.contract_signed = cleaned_data.get("contract_signed", False)
    sales_conversation.comments = cleaned_data.get("comments", "")
    sales_conversation.save()

    sales_conversation.contacts.all().delete()
    SalesConversationContact.objects.bulk_create(
        [
            SalesConversationContact(
                sales_conversation=sales_conversation,
                position=row["position"],
                name=row["name"],
                email=row["email"],
                whatsapp_number=row["whatsapp_number"],
            )
            for row in cleaned_data["contact_rows"]
        ]
    )

    sales_conversation.brands.all().delete()
    SalesConversationBrand.objects.bulk_create(
        [
            SalesConversationBrand(sales_conversation=sales_conversation, name=brand_name)
            for brand_name in cleaned_data["brands_input"]
        ]
    )

    for category, field_name in (
        (SalesConversationFile.CATEGORY_SOLUTION, "solution_files"),
        (SalesConversationFile.CATEGORY_PROPOSAL, "proposal_files"),
    ):
        for uploaded_file in uploaded_files.get(field_name, []):
            SalesConversationFile.objects.create(
                sales_conversation=sales_conversation,
                category=category,
                file=uploaded_file,
            )
    return sales_conversation


def get_or_create_sales_conversation_from_meeting(meeting, created_by=None):
    defaults = {
        "company_name": meeting.prospect.company_name,
        "assigned_sales_manager": default_sales_manager(),
        "created_by": created_by or meeting.scheduled_by,
    }
    sales_conversation, created = SalesConversation.objects.get_or_create(
        source_meeting=meeting,
        defaults=defaults,
    )
    if created:
        SalesConversationContact.objects.create(
            sales_conversation=sales_conversation,
            position=1,
            name=meeting.prospect.contact_name,
            email=meeting.prospect_email or meeting.prospect.prospect_email,
            whatsapp_number="",
        )
    elif not sales_conversation.assigned_sales_manager_id:
        manager = default_sales_manager()
        if manager:
            sales_conversation.assigned_sales_manager = manager
            sales_conversation.save(update_fields=["assigned_sales_manager", "updated_at"])
    return sales_conversation


def get_or_create_contract_collection_from_sales_conversation(sales_conversation, created_by=None):
    defaults = {
        "company_name": sales_conversation.company_name,
        "sales_manager": sales_conversation.assigned_sales_manager,
        "created_by": created_by or sales_conversation.created_by,
    }
    contract_collection, created = ContractCollection.objects.get_or_create(
        source_sales_conversation=sales_conversation,
        defaults=defaults,
    )
    if created:
        ContractCollectionContact.objects.bulk_create(
            [
                ContractCollectionContact(
                    contract_collection=contract_collection,
                    position=contact.position,
                    name=contact.name,
                    email=contact.email,
                    whatsapp_number=contact.whatsapp_number,
                )
                for contact in sales_conversation.contacts.all()
            ]
        )
    else:
        updates = []
        if not contract_collection.sales_manager_id and sales_conversation.assigned_sales_manager_id:
            contract_collection.sales_manager = sales_conversation.assigned_sales_manager
            updates.append("sales_manager")
        if updates:
            updates.append("updated_at")
            contract_collection.save(update_fields=updates)
    return contract_collection


def sync_contract_collection_data(contract_collection, cleaned_data, uploaded_files=None):
    uploaded_files = uploaded_files or {}
    contract_collection.sales_manager = cleaned_data.get("sales_manager")
    if contract_collection.contract_value is None:
        contract_collection.contract_value = cleaned_data.get("contract_value")
    contract_collection.save()

    contract_collection.contacts.all().delete()
    ContractCollectionContact.objects.bulk_create(
        [
            ContractCollectionContact(
                contract_collection=contract_collection,
                position=row["position"],
                name=row["name"],
                email=row["email"],
                whatsapp_number=row["whatsapp_number"],
            )
            for row in cleaned_data["contact_rows"]
        ]
    )

    if uploaded_files.get("contract_files") and not contract_collection.files.exists():
        for uploaded_file in uploaded_files["contract_files"]:
            ContractCollectionFile.objects.create(
                contract_collection=contract_collection,
                file=uploaded_file,
            )

    installments_by_position = {item.position: item for item in contract_collection.installments.all()}
    for row in cleaned_data["installment_rows"]:
        installment = installments_by_position.get(row["position"])
        if not installment:
            installment = ContractCollectionInstallment.objects.create(
                contract_collection=contract_collection,
                position=row["position"],
            )
        if installment.installment_amount is None and row["installment_amount"] is not None:
            installment.installment_amount = row["installment_amount"]
        if installment.invoice_date is None and row["invoice_date"] is not None:
            installment.invoice_date = row["invoice_date"]
            installment.invoice_notification_sent_at = None
        if installment.expected_collection_date is None and row["expected_collection_date"] is not None:
            installment.expected_collection_date = row["expected_collection_date"]
        installment.revised_collection_date = row["revised_collection_date"]
        installment.contract_summary = row["contract_summary"]
        installment.invoiced_service_description = row["invoiced_service_description"]
        installment.legal_due_reason = row["legal_due_reason"]
        installment.save()

    if contract_collection.source_sales_conversation_id and contract_collection.sales_manager_id:
        sales_conversation = contract_collection.source_sales_conversation
        if sales_conversation.assigned_sales_manager_id != contract_collection.sales_manager_id:
            sales_conversation.assigned_sales_manager = contract_collection.sales_manager
            sales_conversation.save(update_fields=["assigned_sales_manager", "updated_at"])
    return contract_collection


def sync_finance_collection_data(contract_collection, cleaned_data):
    installments_by_position = {item.position: item for item in contract_collection.installments.all()}
    for row in cleaned_data["finance_rows"]:
        installment = installments_by_position.get(row["position"])
        if not installment:
            installment = ContractCollectionInstallment.objects.create(
                contract_collection=contract_collection,
                position=row["position"],
            )
        installment.collected_amount = row["collected_amount"]
        installment.collection_date = row["collection_date"]
        installment.save()
    return contract_collection


def build_pending_collections(contract_query_set, today=None):
    today = today or timezone.localdate()
    installments = ContractCollectionInstallment.objects.select_related(
        "contract_collection",
        "contract_collection__sales_manager",
    ).prefetch_related("contract_collection__contacts")
    installments = installments.filter(contract_collection__in=contract_query_set)

    invoiced = installments.filter(invoice_date__lt=today).filter(
        Q(collection_date__isnull=True)
        | Q(collected_amount__isnull=True)
        | Q(collected_amount__lt=F("installment_amount"))
    )
    to_be_invoiced = installments.filter(
        Q(invoice_date__isnull=True) | Q(invoice_date__gte=today)
    ).filter(installment_amount__isnull=False)

    return {
        "invoiced_pending": invoiced.order_by("invoice_date", "contract_collection__company_name", "position"),
        "invoiced_pending_total": sum(
            [item.installment_amount or Decimal("0.00") for item in invoiced],
            Decimal("0.00"),
        ),
        "yet_to_invoice": to_be_invoiced.order_by("invoice_date", "contract_collection__company_name", "position"),
        "yet_to_invoice_total": sum(
            [item.installment_amount or Decimal("0.00") for item in to_be_invoiced],
            Decimal("0.00"),
        ),
    }


def send_invoice_due_email(installment):
    contract = installment.contract_collection
    settings_obj = SystemSetting.load()
    html_body = render_to_string(
        "emails/invoice_due.html",
        {"contract": contract, "installment": installment, "settings_obj": settings_obj},
    )
    text_body = render_to_string(
        "emails/invoice_due.txt",
        {"contract": contract, "installment": installment, "settings_obj": settings_obj},
    )
    recipients = unique_emails(
        [settings_obj.supervisor_sender_email],
        [contract.sales_manager.email] if contract.sales_manager_id else [],
        active_finance_manager_emails(),
    )
    send_email(
        subject=f"Invoice to be raised today: {contract.company_name} / installment {installment.position}",
        html_body=html_body,
        text_body=text_body,
        to_emails=[recipients[0]],
        cc_emails=recipients[1:],
    )


def send_due_invoice_notifications(target_date=None):
    target_date = target_date or timezone.localdate()
    installments = ContractCollectionInstallment.objects.select_related(
        "contract_collection",
        "contract_collection__sales_manager",
    ).filter(
        invoice_date=target_date,
        invoice_notification_sent_at__isnull=True,
    )
    sent_count = 0
    for installment in installments:
        try:
            send_invoice_due_email(installment)
        except Exception:
            logger.exception(
                "Failed to send invoice due email for contract_collection_id=%s installment=%s",
                installment.contract_collection.contract_collection_id,
                installment.position,
            )
            continue
        installment.invoice_notification_sent_at = timezone.now()
        installment.save(update_fields=["invoice_notification_sent_at"])
        sent_count += 1
    return sent_count


def _send_meeting_invitation_after_commit(meeting_id):
    try:
        meeting = Meeting.objects.select_related("prospect", "scheduled_by").get(pk=meeting_id)
        send_meeting_invitation(meeting)
    except Exception:
        logger.exception("Failed to send meeting invitation for meeting_id=%s", meeting_id)


def _send_did_not_happen_email_after_commit(meeting_id):
    try:
        meeting = Meeting.objects.select_related("prospect", "scheduled_by").get(pk=meeting_id)
        send_did_not_happen_email(meeting)
    except Exception:
        logger.exception("Failed to send meeting follow-up email for meeting_id=%s", meeting_id)


@transaction.atomic
def apply_call_outcome(prospect, staff, cleaned_data):
    settings_obj = SystemSetting.load()
    outcome = cleaned_data["outcome"]
    reason = cleaned_data.get("reason", "")
    follow_up_date = cleaned_data.get("follow_up_date")
    scheduled_for = cleaned_data.get("scheduled_for")
    prospect_email = cleaned_data.get("prospect_email", "")
    meeting_platform = cleaned_data.get("meeting_platform", "")
    meeting = None

    if outcome == ProspectStatusUpdate.OUTCOME_FOLLOW_UP:
        prospect.workflow_status = Prospect.WORKFLOW_FOLLOW_UP
        prospect.follow_up_date = follow_up_date
        prospect.follow_up_reason = reason
        prospect.decline_reason = ""
    elif outcome == ProspectStatusUpdate.OUTCOME_DECLINED:
        prospect.workflow_status = Prospect.WORKFLOW_DECLINED
        prospect.decline_reason = reason
        prospect.follow_up_date = None
        prospect.follow_up_reason = ""
    elif outcome == ProspectStatusUpdate.OUTCOME_SCHEDULED:
        if timezone.is_naive(scheduled_for):
            scheduled_for = timezone.make_aware(scheduled_for, ZoneInfo(settings_obj.default_timezone))
        recipient_emails = unique_emails(
            [prospect_email, settings_obj.supervisor_sender_email, staff.email],
            active_sales_manager_emails(),
            settings_obj.sales_emails(),
        )
        meeting = Meeting.objects.create(
            prospect=prospect,
            scheduled_by=staff,
            scheduled_for=scheduled_for,
            prospect_email=prospect_email,
            meeting_platform=meeting_platform,
            recipient_emails=recipient_emails,
        )
        prospect.workflow_status = Prospect.WORKFLOW_SCHEDULED
        prospect.prospect_email = prospect_email
        prospect.follow_up_date = None
        prospect.follow_up_reason = ""
        prospect.decline_reason = ""

    prospect.save()
    ProspectStatusUpdate.objects.create(
        prospect=prospect,
        staff=staff,
        outcome=outcome,
        reason=reason,
        follow_up_date=follow_up_date,
        scheduled_for=scheduled_for,
        prospect_email=prospect_email,
    )
    if meeting:
        transaction.on_commit(lambda: _send_meeting_invitation_after_commit(meeting.pk))
    return meeting


@transaction.atomic
def update_meeting_outcome(meeting, status, updated_by=None):
    meeting.status = status
    meeting.outcome_updated_at = timezone.now()
    meeting.save(update_fields=["status", "outcome_updated_at", "updated_at"])

    if status == Meeting.STATUS_HAPPENED:
        meeting.prospect.workflow_status = Prospect.WORKFLOW_MEETING_HAPPENED
        meeting.prospect.save(update_fields=["workflow_status", "updated_at"])
        ProspectStatusUpdate.objects.create(
            prospect=meeting.prospect,
            staff=meeting.scheduled_by,
            outcome=ProspectStatusUpdate.OUTCOME_MEETING_HAPPENED,
            scheduled_for=meeting.scheduled_for,
            prospect_email=meeting.prospect_email,
        )
        get_or_create_sales_conversation_from_meeting(meeting, created_by=updated_by)
        return

    meeting.prospect.workflow_status = Prospect.WORKFLOW_FOLLOW_UP
    meeting.prospect.follow_up_date = meeting.scheduled_for.astimezone(
        ZoneInfo(SystemSetting.load().default_timezone)
    ).date()
    meeting.prospect.follow_up_reason = "Meeting did not happen"
    meeting.prospect.save(update_fields=["workflow_status", "follow_up_date", "follow_up_reason", "updated_at"])
    ProspectStatusUpdate.objects.create(
        prospect=meeting.prospect,
        staff=meeting.scheduled_by,
        outcome=ProspectStatusUpdate.OUTCOME_MEETING_DID_NOT_HAPPEN,
        reason="Meeting did not happen",
        follow_up_date=meeting.prospect.follow_up_date,
        scheduled_for=meeting.scheduled_for,
        prospect_email=meeting.prospect_email,
    )
    transaction.on_commit(lambda: _send_did_not_happen_email_after_commit(meeting.pk))
