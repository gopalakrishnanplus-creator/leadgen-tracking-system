import base64
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from django.conf import settings
from django.db import connection
from django.core.mail import EmailMultiAlternatives
from django.db import transaction
from django.db.models import Avg, Count, Max, Q, Sum
from django.template.loader import render_to_string
from django.utils import timezone
from icalendar import Calendar, Event, vCalAddress, vText
from openpyxl import load_workbook
from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import Attachment, Disposition, FileContent, FileName, FileType, Mail

from .models import CallImportBatch, CallLog, Meeting, Prospect, ProspectStatusUpdate, SystemSetting, User


CRM_STATUS_MAP = {
    "completed": Prospect.CRM_COMPLETED,
    "no-answer": Prospect.CRM_NO_ANSWER,
    "busy": Prospect.CRM_BUSY,
    "failed": Prospect.CRM_FAILED,
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
    event.add(
        "description",
        f"Prospect: {meeting.prospect.contact_name}\n"
        f"Company: {meeting.prospect.company_name}\n"
        f"LinkedIn: {meeting.prospect.linkedin_url}\n"
        f"Lead Gen Staff: {meeting.scheduled_by.name}",
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


def send_email(subject, html_body, text_body, to_emails, cc_emails=None, attachments=None):
    cc_emails = cc_emails or []
    attachments = attachments or []
    if settings.SENDGRID_API_KEY:
        message = Mail(
            from_email=settings.DEFAULT_FROM_EMAIL,
            to_emails=to_emails,
            subject=subject,
            html_content=html_body,
            plain_text_content=text_body,
        )
        if cc_emails:
            message.cc = cc_emails
        for attachment in attachments:
            message.attachment = Attachment(
                FileContent(base64.b64encode(attachment["content"]).decode("utf-8")),
                FileName(attachment["filename"]),
                FileType(attachment["type"]),
                Disposition("attachment"),
            )
        SendGridAPIClient(settings.SENDGRID_API_KEY).send(message)
        return

    email = EmailMultiAlternatives(
        subject=subject,
        body=text_body,
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=to_emails,
        cc=cc_emails,
    )
    email.attach_alternative(html_body, "text/html")
    for attachment in attachments:
        email.attach(attachment["filename"], attachment["content"], attachment["type"])
    email.send()


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
        subject=f"Meeting invitation: {meeting.prospect.company_name} / {meeting.prospect.contact_name}",
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


@transaction.atomic
def apply_call_outcome(prospect, staff, cleaned_data):
    settings_obj = SystemSetting.load()
    outcome = cleaned_data["outcome"]
    reason = cleaned_data.get("reason", "")
    follow_up_date = cleaned_data.get("follow_up_date")
    scheduled_for = cleaned_data.get("scheduled_for")
    prospect_email = cleaned_data.get("prospect_email", "")
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
            settings_obj.sales_emails(),
        )
        meeting = Meeting.objects.create(
            prospect=prospect,
            scheduled_by=staff,
            scheduled_for=scheduled_for,
            prospect_email=prospect_email,
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
        send_meeting_invitation(meeting)
    return meeting


@transaction.atomic
def update_meeting_outcome(meeting, status):
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
    send_did_not_happen_email(meeting)
