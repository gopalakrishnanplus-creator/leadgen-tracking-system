from django.conf import settings
from django.contrib.auth.models import AbstractUser
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from django.utils import timezone


class User(AbstractUser):
    ROLE_SUPERVISOR = "supervisor"
    ROLE_STAFF = "staff"
    ROLE_CHOICES = [
        (ROLE_SUPERVISOR, "Supervisor"),
        (ROLE_STAFF, "Lead Gen Staff"),
    ]

    role = models.CharField(max_length=20, choices=ROLE_CHOICES, default=ROLE_STAFF)
    name = models.CharField(max_length=255)
    email = models.EmailField(unique=True)
    calling_number = models.CharField(max_length=20, blank=True, unique=True, null=True)
    whatsapp_number = models.CharField(max_length=20, blank=True)
    must_change_password = models.BooleanField(default=False)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["role"],
                condition=Q(role="supervisor"),
                name="unique_supervisor_user",
            )
        ]

    def clean(self):
        if self.role == self.ROLE_STAFF and not self.calling_number:
            raise ValidationError("Lead gen staff must have a calling number.")
        if self.role == self.ROLE_SUPERVISOR and self.calling_number:
            raise ValidationError("Supervisor accounts cannot have a calling number.")

    def save(self, *args, **kwargs):
        self.email = (self.email or "").lower()
        self.username = self.email
        super().save(*args, **kwargs)

    @property
    def is_supervisor(self):
        return self.role == self.ROLE_SUPERVISOR

    @property
    def is_staff_user(self):
        return self.role == self.ROLE_STAFF

    def __str__(self):
        return self.name or self.email


class SystemSetting(models.Model):
    supervisor_name = models.CharField(max_length=255, default="Bhavesh Kataria")
    supervisor_sender_email = models.EmailField(default="bhavesh.kataria@inditech.co.in")
    default_timezone = models.CharField(max_length=64, default="Asia/Kolkata")
    sales_email_1 = models.EmailField(blank=True)
    sales_email_2 = models.EmailField(blank=True)
    sales_email_3 = models.EmailField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)

    @classmethod
    def load(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj

    def sales_emails(self):
        return [email for email in [self.sales_email_1, self.sales_email_2, self.sales_email_3] if email]

    def __str__(self):
        return "System settings"


class Prospect(models.Model):
    APPROVAL_PENDING = "pending"
    APPROVAL_ACCEPTED = "accepted"
    APPROVAL_REJECTED = "rejected"
    APPROVAL_CHOICES = [
        (APPROVAL_PENDING, "Pending"),
        (APPROVAL_ACCEPTED, "Accepted"),
        (APPROVAL_REJECTED, "Rejected"),
    ]

    WORKFLOW_PENDING_REVIEW = "pending_review"
    WORKFLOW_READY_TO_CALL = "ready_to_call"
    WORKFLOW_FOLLOW_UP = "follow_up_to_schedule"
    WORKFLOW_DECLINED = "does_not_agree"
    WORKFLOW_SCHEDULED = "scheduled"
    WORKFLOW_MEETING_HAPPENED = "meeting_happened"
    WORKFLOW_CHOICES = [
        (WORKFLOW_PENDING_REVIEW, "Pending review"),
        (WORKFLOW_READY_TO_CALL, "Ready to call"),
        (WORKFLOW_FOLLOW_UP, "Follow up to schedule"),
        (WORKFLOW_DECLINED, "Does not agree"),
        (WORKFLOW_SCHEDULED, "Scheduled"),
        (WORKFLOW_MEETING_HAPPENED, "Meeting happened"),
    ]

    CRM_NOT_CALLED = "not_called"
    CRM_COMPLETED = "completed"
    CRM_NO_ANSWER = "no_answer"
    CRM_BUSY = "busy"
    CRM_FAILED = "failed"
    CRM_CHOICES = [
        (CRM_NOT_CALLED, "Not called"),
        (CRM_COMPLETED, "Completed"),
        (CRM_NO_ANSWER, "No answer"),
        (CRM_BUSY, "Busy"),
        (CRM_FAILED, "Failed"),
    ]

    company_name = models.CharField(max_length=255)
    contact_name = models.CharField(max_length=255)
    linkedin_url = models.URLField()
    phone_number = models.CharField(max_length=20, unique=True)
    assigned_to = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name="assigned_prospects",
        on_delete=models.PROTECT,
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name="created_prospects",
        on_delete=models.PROTECT,
    )
    approval_status = models.CharField(max_length=20, choices=APPROVAL_CHOICES, default=APPROVAL_PENDING)
    workflow_status = models.CharField(max_length=40, choices=WORKFLOW_CHOICES, default=WORKFLOW_PENDING_REVIEW)
    latest_crm_status = models.CharField(max_length=20, choices=CRM_CHOICES, default=CRM_NOT_CALLED)
    supervisor_notes = models.TextField(blank=True)
    reviewed_at = models.DateTimeField(blank=True, null=True)
    accepted_at = models.DateTimeField(blank=True, null=True)
    latest_call_attempt_at = models.DateTimeField(blank=True, null=True)
    latest_call_duration_seconds = models.PositiveIntegerField(default=0)
    total_call_attempts = models.PositiveIntegerField(default=0)
    total_connected_calls = models.PositiveIntegerField(default=0)
    follow_up_date = models.DateField(blank=True, null=True)
    follow_up_reason = models.TextField(blank=True)
    decline_reason = models.TextField(blank=True)
    prospect_email = models.EmailField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["company_name", "contact_name"]

    def clean(self):
        if self.assigned_to and not self.assigned_to.is_staff_user:
            raise ValidationError("Prospects must be assigned to a lead gen staff user.")

    @property
    def display_status(self):
        return dict(self.WORKFLOW_CHOICES).get(self.workflow_status, self.workflow_status)

    def __str__(self):
        return f"{self.company_name} - {self.contact_name}"


class CallImportBatch(models.Model):
    import_date = models.DateField()
    uploaded_file = models.FileField(upload_to="imports/")
    imported_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    total_rows = models.PositiveIntegerField(default=0)
    matched_rows = models.PositiveIntegerField(default=0)
    unmatched_rows = models.PositiveIntegerField(default=0)
    duplicate_rows = models.PositiveIntegerField(default=0)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-import_date", "-created_at"]

    def __str__(self):
        return f"Import {self.import_date:%Y-%m-%d}"


class CallLog(models.Model):
    STATUS_COMPLETED = "completed"
    STATUS_NO_ANSWER = "no-answer"
    STATUS_BUSY = "busy"
    STATUS_FAILED = "failed"
    STATUS_CHOICES = [
        (STATUS_COMPLETED, "Completed"),
        (STATUS_NO_ANSWER, "No answer"),
        (STATUS_BUSY, "Busy"),
        (STATUS_FAILED, "Failed"),
    ]

    call_sid = models.CharField(max_length=64, unique=True)
    batch = models.ForeignKey(CallImportBatch, related_name="call_logs", on_delete=models.CASCADE)
    staff = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name="call_logs",
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
    )
    prospect = models.ForeignKey(
        Prospect,
        related_name="call_logs",
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
    )
    started_at = models.DateTimeField(blank=True, null=True)
    ended_at = models.DateTimeField(blank=True, null=True)
    duration_seconds = models.PositiveIntegerField(default=0)
    from_number = models.CharField(max_length=20)
    to_number = models.CharField(max_length=20)
    direction = models.CharField(max_length=20, blank=True)
    crm_status = models.CharField(max_length=20, choices=STATUS_CHOICES)
    was_connected = models.BooleanField(default=False)
    matched = models.BooleanField(default=False)
    raw_data = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-started_at", "-created_at"]
        indexes = [
            models.Index(fields=["started_at", "staff"]),
            models.Index(fields=["started_at", "crm_status"]),
        ]

    def __str__(self):
        return self.call_sid


class ProspectStatusUpdate(models.Model):
    OUTCOME_FOLLOW_UP = "follow_up_to_schedule"
    OUTCOME_DECLINED = "does_not_agree"
    OUTCOME_SCHEDULED = "scheduled"
    OUTCOME_MEETING_HAPPENED = "meeting_happened"
    OUTCOME_MEETING_DID_NOT_HAPPEN = "meeting_did_not_happen"
    OUTCOME_CHOICES = [
        (OUTCOME_FOLLOW_UP, "Follow up to schedule"),
        (OUTCOME_DECLINED, "Does not agree"),
        (OUTCOME_SCHEDULED, "Scheduled"),
        (OUTCOME_MEETING_HAPPENED, "Meeting happened"),
        (OUTCOME_MEETING_DID_NOT_HAPPEN, "Meeting did not happen"),
    ]

    prospect = models.ForeignKey(Prospect, related_name="status_updates", on_delete=models.CASCADE)
    staff = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name="prospect_updates",
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
    )
    outcome = models.CharField(max_length=32, choices=OUTCOME_CHOICES)
    reason = models.TextField(blank=True)
    follow_up_date = models.DateField(blank=True, null=True)
    scheduled_for = models.DateTimeField(blank=True, null=True)
    prospect_email = models.EmailField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["created_at", "outcome"]),
        ]

    def __str__(self):
        return f"{self.prospect} - {self.outcome}"


class Meeting(models.Model):
    STATUS_SCHEDULED = "scheduled"
    STATUS_HAPPENED = "happened"
    STATUS_DID_NOT_HAPPEN = "did_not_happen"
    STATUS_CHOICES = [
        (STATUS_SCHEDULED, "Scheduled"),
        (STATUS_HAPPENED, "Meeting happened"),
        (STATUS_DID_NOT_HAPPEN, "Did not happen"),
    ]

    prospect = models.ForeignKey(Prospect, related_name="meetings", on_delete=models.CASCADE)
    scheduled_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name="scheduled_meetings",
        on_delete=models.PROTECT,
    )
    scheduled_for = models.DateTimeField()
    prospect_email = models.EmailField()
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_SCHEDULED)
    recipient_emails = models.JSONField(default=list, blank=True)
    invite_sent_at = models.DateTimeField(blank=True, null=True)
    outcome_updated_at = models.DateTimeField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-scheduled_for"]
        indexes = [
            models.Index(fields=["scheduled_for", "status"]),
            models.Index(fields=["created_at", "status"]),
        ]

    def mark_invite_sent(self):
        self.invite_sent_at = timezone.now()
        self.save(update_fields=["invite_sent_at", "updated_at"])

    def __str__(self):
        return f"{self.prospect} @ {self.scheduled_for:%Y-%m-%d %H:%M}"
