import uuid

from django.conf import settings
from django.contrib.auth.models import AbstractUser
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from django.utils import timezone


class User(AbstractUser):
    ROLE_SUPERVISOR = "supervisor"
    ROLE_STAFF = "staff"
    ROLE_SALES_MANAGER = "sales_manager"
    ROLE_FINANCE_MANAGER = "finance_manager"
    ROLE_CHOICES = [
        (ROLE_SUPERVISOR, "Supervisor"),
        (ROLE_STAFF, "Lead Gen Staff"),
        (ROLE_SALES_MANAGER, "Sales Manager"),
        (ROLE_FINANCE_MANAGER, "Finance Manager"),
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
        if self.role in {self.ROLE_SUPERVISOR, self.ROLE_SALES_MANAGER, self.ROLE_FINANCE_MANAGER} and self.calling_number:
            raise ValidationError("Supervisor, sales manager, and finance manager accounts cannot have a calling number.")

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

    @property
    def is_sales_manager(self):
        return self.role == self.ROLE_SALES_MANAGER

    @property
    def is_finance_manager(self):
        return self.role == self.ROLE_FINANCE_MANAGER

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
    WORKFLOW_SUPERVISOR_ACTION = "supervisor_action_required"
    WORKFLOW_INVALID_NUMBER = "invalid_number"
    WORKFLOW_CHOICES = [
        (WORKFLOW_PENDING_REVIEW, "Pending review"),
        (WORKFLOW_READY_TO_CALL, "Ready to call"),
        (WORKFLOW_FOLLOW_UP, "Follow up to schedule"),
        (WORKFLOW_DECLINED, "Does not agree"),
        (WORKFLOW_SCHEDULED, "Scheduled"),
        (WORKFLOW_MEETING_HAPPENED, "Meeting happened"),
        (WORKFLOW_SUPERVISOR_ACTION, "Supervisor action required"),
        (WORKFLOW_INVALID_NUMBER, "Invalid number"),
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
    system_action_note = models.TextField(blank=True)
    no_answer_reset_count = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["company_name", "contact_name"]

    def clean(self):
        if not self.assigned_to_id:
            return
        if not self.assigned_to.is_staff_user:
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
    PLATFORM_TEAMS = "teams"
    PLATFORM_ZOOM = "zoom"
    PLATFORM_CHOICES = [
        (PLATFORM_TEAMS, "Teams meeting"),
        (PLATFORM_ZOOM, "Zoom meeting"),
    ]

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
    meeting_platform = models.CharField(max_length=20, choices=PLATFORM_CHOICES, blank=True, default="")
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

    @property
    def meeting_link(self):
        if self.meeting_platform == self.PLATFORM_TEAMS:
            return settings.TEAMS_MEETING_LINK
        if self.meeting_platform == self.PLATFORM_ZOOM:
            return settings.ZOOM_MEETING_LINK
        return ""

    @property
    def meeting_access_lines(self):
        if self.meeting_platform == self.PLATFORM_TEAMS:
            return [self.meeting_link] if self.meeting_link else []
        if self.meeting_platform == self.PLATFORM_ZOOM:
            lines = [line for line in [self.meeting_link] if line]
            if settings.ZOOM_MEETING_ID:
                lines.append(f"Meeting ID: {settings.ZOOM_MEETING_ID}")
            if settings.ZOOM_MEETING_PASSCODE:
                lines.append(f"Passcode: {settings.ZOOM_MEETING_PASSCODE}")
            return lines
        return []

    def __str__(self):
        return f"{self.prospect} @ {self.scheduled_for:%Y-%m-%d %H:%M}"


class MeetingReminder(models.Model):
    TYPE_WHATSAPP_INITIAL = "whatsapp_initial"
    TYPE_EMAIL_DAY_BEFORE = "email_day_before"
    TYPE_EMAIL_SAME_DAY = "email_same_day"
    TYPE_WHATSAPP_FINAL = "whatsapp_final"
    TYPE_CHOICES = [
        (TYPE_WHATSAPP_INITIAL, "First WhatsApp reminder"),
        (TYPE_EMAIL_DAY_BEFORE, "24-hour reminder email"),
        (TYPE_EMAIL_SAME_DAY, "Same-day reminder email"),
        (TYPE_WHATSAPP_FINAL, "Final WhatsApp reminder"),
    ]

    meeting = models.ForeignKey(Meeting, related_name="reminders", on_delete=models.CASCADE)
    reminder_type = models.CharField(max_length=32, choices=TYPE_CHOICES)
    recipient_number = models.CharField(max_length=20, blank=True)
    screenshot = models.FileField(upload_to="meeting-reminders/", blank=True)
    sent_at = models.DateTimeField(default=timezone.now)
    sent_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name="meeting_reminders",
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["sent_at", "created_at"]
        constraints = [
            models.UniqueConstraint(fields=["meeting", "reminder_type"], name="unique_meeting_reminder_type"),
        ]

    def __str__(self):
        return f"{self.meeting} - {self.get_reminder_type_display()}"


def generate_sales_conversation_id():
    return f"SC-{uuid.uuid4().hex[:10].upper()}"


def generate_contract_collection_id():
    return f"CC-{uuid.uuid4().hex[:10].upper()}"


class SalesConversation(models.Model):
    STATUS_ENGAGED = "engaged"
    STATUS_NOT_ENGAGED = "not_engaged"
    STATUS_TO_BE_REVIVED = "to_be_revived"
    STATUS_DEEPLY_ENGAGED = "deeply_engaged"
    STATUS_IN_NEGOTIATIONS = "in_negotiations"
    STATUS_IN_CONTRACTING = "in_contracting"
    STATUS_CHOICES = [
        (STATUS_ENGAGED, "Engaged"),
        (STATUS_NOT_ENGAGED, "Not engaged"),
        (STATUS_TO_BE_REVIVED, "To be revived"),
        (STATUS_DEEPLY_ENGAGED, "Deeply engaged"),
        (STATUS_IN_NEGOTIATIONS, "In negotiations"),
        (STATUS_IN_CONTRACTING, "In contracting"),
    ]

    PROPOSAL_SOLUTION_NEEDED = "solution_needed"
    PROPOSAL_SOLUTION_GIVEN = "solution_given"
    PROPOSAL_NEED_UPDATED_SOLUTION = "need_updated_solution"
    PROPOSAL_PROPOSAL_NEEDED = "proposal_needed"
    PROPOSAL_PROPOSAL_GIVEN = "proposal_given"
    PROPOSAL_STATUS_CHOICES = [
        (PROPOSAL_SOLUTION_NEEDED, "Solution needed"),
        (PROPOSAL_SOLUTION_GIVEN, "Solution given"),
        (PROPOSAL_NEED_UPDATED_SOLUTION, "Need updated solution"),
        (PROPOSAL_PROPOSAL_NEEDED, "Proposal needed"),
        (PROPOSAL_PROPOSAL_GIVEN, "Proposal given"),
    ]

    sales_conversation_id = models.CharField(
        max_length=32,
        unique=True,
        default=generate_sales_conversation_id,
        editable=False,
    )
    company_name = models.CharField(max_length=255)
    assigned_sales_manager = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name="sales_conversations",
        on_delete=models.PROTECT,
        blank=True,
        null=True,
    )
    conversation_status = models.CharField(
        max_length=32,
        choices=STATUS_CHOICES,
        default=STATUS_ENGAGED,
    )
    proposal_status = models.CharField(
        max_length=32,
        choices=PROPOSAL_STATUS_CHOICES,
        default=PROPOSAL_SOLUTION_NEEDED,
    )
    contract_signed = models.BooleanField(default=False)
    comments = models.TextField(blank=True)
    source_meeting = models.OneToOneField(
        Meeting,
        related_name="sales_conversation",
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name="created_sales_conversations",
        on_delete=models.PROTECT,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["contract_signed", "-updated_at", "company_name"]
        indexes = [
            models.Index(fields=["contract_signed", "conversation_status"]),
            models.Index(fields=["assigned_sales_manager", "contract_signed"]),
            models.Index(fields=["proposal_status", "contract_signed"]),
        ]

    def clean(self):
        if self.assigned_sales_manager_id and not self.assigned_sales_manager.is_sales_manager:
            raise ValidationError("Assigned sales manager must have the sales manager role.")

    def __str__(self):
        return f"{self.sales_conversation_id} - {self.company_name}"


class SalesConversationContact(models.Model):
    sales_conversation = models.ForeignKey(
        SalesConversation,
        related_name="contacts",
        on_delete=models.CASCADE,
    )
    position = models.PositiveSmallIntegerField()
    name = models.CharField(max_length=255)
    email = models.EmailField(blank=True)
    whatsapp_number = models.CharField(max_length=20, blank=True)

    class Meta:
        ordering = ["position"]
        constraints = [
            models.UniqueConstraint(
                fields=["sales_conversation", "position"],
                name="unique_sales_contact_position",
            )
        ]

    def clean(self):
        if self.position < 1 or self.position > 3:
            raise ValidationError("Only three contact slots are supported per sales conversation.")

    def __str__(self):
        return f"{self.sales_conversation.sales_conversation_id} / {self.name}"


class SalesConversationBrand(models.Model):
    sales_conversation = models.ForeignKey(
        SalesConversation,
        related_name="brands",
        on_delete=models.CASCADE,
    )
    name = models.CharField(max_length=255)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return f"{self.sales_conversation.sales_conversation_id} / {self.name}"


class SalesConversationFile(models.Model):
    CATEGORY_SOLUTION = "solution"
    CATEGORY_PROPOSAL = "proposal"
    CATEGORY_CHOICES = [
        (CATEGORY_SOLUTION, "Solution"),
        (CATEGORY_PROPOSAL, "Proposal"),
    ]

    sales_conversation = models.ForeignKey(
        SalesConversation,
        related_name="files",
        on_delete=models.CASCADE,
    )
    category = models.CharField(max_length=20, choices=CATEGORY_CHOICES)
    file = models.FileField(upload_to="sales_pipeline/")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["category", "-created_at"]

    def __str__(self):
        return f"{self.sales_conversation.sales_conversation_id} / {self.file.name}"


class ContractCollection(models.Model):
    contract_collection_id = models.CharField(
        max_length=32,
        unique=True,
        default=generate_contract_collection_id,
        editable=False,
    )
    source_sales_conversation = models.OneToOneField(
        SalesConversation,
        related_name="contract_collection",
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
    )
    company_name = models.CharField(max_length=255)
    sales_manager = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name="contract_collections",
        on_delete=models.PROTECT,
        blank=True,
        null=True,
    )
    contract_value = models.DecimalField(max_digits=14, decimal_places=2, blank=True, null=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name="created_contract_collections",
        on_delete=models.PROTECT,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at", "company_name"]
        indexes = [
            models.Index(fields=["sales_manager", "updated_at"]),
        ]

    def clean(self):
        if self.sales_manager_id and not self.sales_manager.is_sales_manager:
            raise ValidationError("Assigned sales manager must have the sales manager role.")

    @property
    def contract_terms_locked(self):
        if self.contract_value is not None:
            return True
        if self.files.exists():
            return True
        return self.installments.filter(
            Q(installment_amount__isnull=False)
            | Q(invoice_date__isnull=False)
            | Q(expected_collection_date__isnull=False)
        ).exists()

    def __str__(self):
        return f"{self.contract_collection_id} - {self.company_name}"


class ContractCollectionContact(models.Model):
    contract_collection = models.ForeignKey(
        ContractCollection,
        related_name="contacts",
        on_delete=models.CASCADE,
    )
    position = models.PositiveSmallIntegerField()
    name = models.CharField(max_length=255)
    email = models.EmailField(blank=True)
    whatsapp_number = models.CharField(max_length=20, blank=True)

    class Meta:
        ordering = ["position"]
        constraints = [
            models.UniqueConstraint(
                fields=["contract_collection", "position"],
                name="unique_contract_contact_position",
            )
        ]

    def clean(self):
        if self.position < 1 or self.position > 3:
            raise ValidationError("Only three contact slots are supported per contract.")

    def __str__(self):
        return f"{self.contract_collection.contract_collection_id} / {self.name}"


class ContractCollectionFile(models.Model):
    contract_collection = models.ForeignKey(
        ContractCollection,
        related_name="files",
        on_delete=models.CASCADE,
    )
    file = models.FileField(upload_to="contracts/")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.contract_collection.contract_collection_id} / {self.file.name}"


class ContractCollectionInstallment(models.Model):
    contract_collection = models.ForeignKey(
        ContractCollection,
        related_name="installments",
        on_delete=models.CASCADE,
    )
    position = models.PositiveSmallIntegerField()
    installment_amount = models.DecimalField(max_digits=14, decimal_places=2, blank=True, null=True)
    invoice_date = models.DateField(blank=True, null=True)
    expected_collection_date = models.DateField(blank=True, null=True)
    revised_collection_date = models.DateField(blank=True, null=True)
    contract_summary = models.TextField(blank=True)
    invoiced_service_description = models.TextField(blank=True)
    legal_due_reason = models.TextField(blank=True)
    collected_amount = models.DecimalField(max_digits=14, decimal_places=2, blank=True, null=True)
    collection_date = models.DateField(blank=True, null=True)
    invoice_notification_sent_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        ordering = ["position"]
        constraints = [
            models.UniqueConstraint(
                fields=["contract_collection", "position"],
                name="unique_contract_installment_position",
            )
        ]
        indexes = [
            models.Index(fields=["invoice_date", "collection_date"]),
            models.Index(fields=["expected_collection_date", "revised_collection_date"]),
        ]

    def clean(self):
        if self.position < 1 or self.position > 6:
            raise ValidationError("Only six installment slots are supported per contract.")

    @property
    def is_collected(self):
        if self.installment_amount is None:
            return False
        if self.collected_amount is None:
            return False
        return self.collected_amount >= self.installment_amount and self.collection_date is not None

    def __str__(self):
        return f"{self.contract_collection.contract_collection_id} / installment {self.position}"
