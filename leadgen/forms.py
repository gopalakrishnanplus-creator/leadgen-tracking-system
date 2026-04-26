from django import forms
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import URLValidator
from django.utils import timezone
from decimal import Decimal

from .models import (
    CallImportBatch,
    CashflowImportedItem,
    CashflowProjectedCollection,
    CashflowSnapshot,
    CashflowWorkOrderRequest,
    ContractCollection,
    ContractCollectionInstallment,
    DirectMarketingActivity,
    Meeting,
    MeetingReminder,
    PublicDownloadFile,
    Prospect,
    SalesConversation,
    SupervisorAccessEmail,
    SystemSetting,
    User,
)


class StyledFormMixin:
    def _apply_classes(self):
        for field in self.fields.values():
            classes = field.widget.attrs.get("class", "")
            field.widget.attrs["class"] = f"{classes} form-control".strip()

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._apply_classes()


class MultiFileInput(forms.ClearableFileInput):
    allow_multiple_selected = True


class MultiFileField(forms.FileField):
    widget = MultiFileInput

    def clean(self, data, initial=None):
        if not data:
            return []
        if not isinstance(data, (list, tuple)):
            data = [data]
        cleaned_files = []
        errors = []
        for item in data:
            try:
                cleaned_files.append(super().clean(item, initial))
            except ValidationError as exc:
                errors.extend(exc.error_list)
        if errors:
            raise ValidationError(errors)
        return cleaned_files


PUBLIC_DOWNLOAD_MAX_FILE_SIZE = 200 * 1024 * 1024


def validate_uploaded_file_sizes(files, field_label, max_size=None):
    max_size = max_size or getattr(settings, "MAX_UPLOAD_FILE_SIZE", 10 * 1024 * 1024)
    over_limit = [uploaded_file.name for uploaded_file in files if uploaded_file.size > max_size]
    if over_limit:
        max_size_mb = max_size / (1024 * 1024)
        raise ValidationError(
            f"Each file in {field_label} must be {max_size_mb:.0f} MB or smaller. Oversized files: {', '.join(over_limit)}."
        )


def installment_textarea_field(label):
    return forms.CharField(
        required=False,
        label=label,
        widget=forms.Textarea(attrs={"rows": 3}),
    )


def role_email_exists(email, role, exclude_pk=None):
    queryset = User.objects.filter(email=email, role=role)
    if exclude_pk is not None:
        queryset = queryset.exclude(pk=exclude_pk)
    return queryset.exists()


class FixedRoleUserFormMixin:
    target_role = None

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.target_role:
            self.instance.role = self.target_role
        self.instance.calling_number = None


class StaffCreateForm(StyledFormMixin, forms.ModelForm):
    class Meta:
        model = User
        fields = ["name", "email", "calling_number", "whatsapp_number"]

    def clean_email(self):
        email = self.cleaned_data["email"].lower()
        if role_email_exists(email, User.ROLE_STAFF):
            raise ValidationError("A lead gen staff user with this email already exists.")
        return email

    def save(self, commit=True):
        user = super().save(commit=False)
        user.role = User.ROLE_STAFF
        user.email = user.email.lower()
        user.must_change_password = False
        user.set_unusable_password()
        if commit:
            user.save()
        return user


class StaffUpdateForm(StyledFormMixin, forms.ModelForm):
    class Meta:
        model = User
        fields = ["name", "email", "calling_number", "whatsapp_number", "is_active"]

    def clean_email(self):
        email = self.cleaned_data["email"].lower()
        if role_email_exists(email, User.ROLE_STAFF, exclude_pk=self.instance.pk):
            raise ValidationError("A lead gen staff user with this email already exists.")
        return email


class SalesManagerCreateForm(FixedRoleUserFormMixin, StyledFormMixin, forms.ModelForm):
    target_role = User.ROLE_SALES_MANAGER

    class Meta:
        model = User
        fields = ["name", "email", "whatsapp_number"]

    def clean_email(self):
        email = self.cleaned_data["email"].lower()
        if role_email_exists(email, User.ROLE_SALES_MANAGER):
            raise ValidationError("A sales manager with this email already exists.")
        return email

    def save(self, commit=True):
        user = super().save(commit=False)
        user.role = User.ROLE_SALES_MANAGER
        user.email = user.email.lower()
        user.must_change_password = False
        user.calling_number = None
        user.set_unusable_password()
        if commit:
            user.save()
        return user


class SalesManagerUpdateForm(FixedRoleUserFormMixin, StyledFormMixin, forms.ModelForm):
    target_role = User.ROLE_SALES_MANAGER

    class Meta:
        model = User
        fields = ["name", "email", "whatsapp_number", "is_active"]

    def clean_email(self):
        email = self.cleaned_data["email"].lower()
        if role_email_exists(email, User.ROLE_SALES_MANAGER, exclude_pk=self.instance.pk):
            raise ValidationError("A sales manager with this email already exists.")
        return email

    def save(self, commit=True):
        user = super().save(commit=False)
        user.role = User.ROLE_SALES_MANAGER
        user.calling_number = None
        if commit:
            user.save()
        return user


class FinanceManagerCreateForm(FixedRoleUserFormMixin, StyledFormMixin, forms.ModelForm):
    target_role = User.ROLE_FINANCE_MANAGER

    class Meta:
        model = User
        fields = ["name", "email", "whatsapp_number"]

    def clean_email(self):
        email = self.cleaned_data["email"].lower()
        if role_email_exists(email, User.ROLE_FINANCE_MANAGER):
            raise ValidationError("A finance manager with this email already exists.")
        return email

    def save(self, commit=True):
        user = super().save(commit=False)
        user.role = User.ROLE_FINANCE_MANAGER
        user.email = user.email.lower()
        user.must_change_password = False
        user.calling_number = None
        user.set_unusable_password()
        if commit:
            user.save()
        return user


class FinanceManagerUpdateForm(FixedRoleUserFormMixin, StyledFormMixin, forms.ModelForm):
    target_role = User.ROLE_FINANCE_MANAGER

    class Meta:
        model = User
        fields = ["name", "email", "whatsapp_number", "is_active"]

    def clean_email(self):
        email = self.cleaned_data["email"].lower()
        if role_email_exists(email, User.ROLE_FINANCE_MANAGER, exclude_pk=self.instance.pk):
            raise ValidationError("A finance manager with this email already exists.")
        return email

    def save(self, commit=True):
        user = super().save(commit=False)
        user.role = User.ROLE_FINANCE_MANAGER
        user.calling_number = None
        if commit:
            user.save()
        return user


class BusinessManagerCreateForm(FixedRoleUserFormMixin, StyledFormMixin, forms.ModelForm):
    target_role = User.ROLE_BUSINESS_MANAGER

    class Meta:
        model = User
        fields = ["name", "email"]

    def clean_email(self):
        email = self.cleaned_data["email"].lower()
        if role_email_exists(email, User.ROLE_BUSINESS_MANAGER):
            raise ValidationError("A business manager with this email already exists.")
        return email

    def save(self, commit=True):
        user = super().save(commit=False)
        user.role = User.ROLE_BUSINESS_MANAGER
        user.email = user.email.lower()
        user.must_change_password = False
        user.calling_number = None
        user.whatsapp_number = ""
        user.set_unusable_password()
        if commit:
            user.save()
        return user


class BusinessManagerUpdateForm(FixedRoleUserFormMixin, StyledFormMixin, forms.ModelForm):
    target_role = User.ROLE_BUSINESS_MANAGER

    class Meta:
        model = User
        fields = ["name", "email", "is_active"]

    def clean_email(self):
        email = self.cleaned_data["email"].lower()
        if role_email_exists(email, User.ROLE_BUSINESS_MANAGER, exclude_pk=self.instance.pk):
            raise ValidationError("A business manager with this email already exists.")
        return email

    def save(self, commit=True):
        user = super().save(commit=False)
        user.role = User.ROLE_BUSINESS_MANAGER
        user.calling_number = None
        user.whatsapp_number = ""
        if commit:
            user.save()
        return user


class SupervisorAccessEmailForm(StyledFormMixin, forms.ModelForm):
    reactivated_instance = None

    class Meta:
        model = SupervisorAccessEmail
        fields = ["email"]

    def clean_email(self):
        email = (self.cleaned_data.get("email") or "").strip().lower()
        existing = SupervisorAccessEmail.objects.exclude(pk=self.instance.pk).filter(email=email).first()
        if existing and existing.is_active:
            raise ValidationError("This supervisor access email already exists.")
        self.reactivated_instance = existing
        return email


class ProspectCreateForm(StyledFormMixin, forms.ModelForm):
    class Meta:
        model = Prospect
        fields = ["company_name", "contact_name", "linkedin_url", "phone_number"]
        widgets = {
            "linkedin_url": forms.TextInput(
                attrs={"placeholder": "www.linkedin.com/in/username"}
            ),
        }

    def clean_linkedin_url(self):
        linkedin_url = (self.data.get(self.add_prefix("linkedin_url")) or "").strip()
        if linkedin_url and "://" not in linkedin_url:
            linkedin_url = f"https://{linkedin_url}"
        URLValidator()(linkedin_url)
        return linkedin_url


def active_staff_queryset():
    return User.objects.filter(role=User.ROLE_STAFF, is_active=True).order_by("name", "email")


class SupervisorProspectCreateForm(ProspectCreateForm):
    assigned_to = forms.ModelChoiceField(queryset=User.objects.none(), empty_label=None)

    class Meta(ProspectCreateForm.Meta):
        fields = ["company_name", "contact_name", "linkedin_url", "phone_number", "assigned_to"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["assigned_to"].queryset = active_staff_queryset()


class ProspectReviewForm(StyledFormMixin, forms.Form):
    assigned_to = forms.ModelChoiceField(queryset=User.objects.none(), empty_label=None)
    decision = forms.ChoiceField(
        choices=[("accept", "Accept"), ("reject", "Reject")],
        widget=forms.RadioSelect,
    )
    supervisor_notes = forms.CharField(widget=forms.Textarea(attrs={"rows": 3}), required=False)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["assigned_to"].queryset = active_staff_queryset()


class SupervisorProspectActionForm(StyledFormMixin, forms.Form):
    assigned_to = forms.ModelChoiceField(queryset=User.objects.none(), empty_label=None)
    supervisor_notes = forms.CharField(widget=forms.Textarea(attrs={"rows": 3}), required=False)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["assigned_to"].queryset = active_staff_queryset()


class CallOutcomeForm(StyledFormMixin, forms.Form):
    outcome = forms.ChoiceField(
        choices=[
            ("follow_up_to_schedule", "Follow-up to schedule"),
            ("does_not_agree", "Does not agree"),
            ("scheduled", "Scheduled"),
        ]
    )
    follow_up_date = forms.DateField(
        required=False,
        widget=forms.DateInput(attrs={"type": "date"}),
    )
    reason = forms.CharField(widget=forms.Textarea(attrs={"rows": 3}), required=False)
    scheduled_for = forms.DateTimeField(
        required=False,
        widget=forms.DateTimeInput(attrs={"type": "datetime-local"}),
        input_formats=["%Y-%m-%dT%H:%M"],
    )
    prospect_email = forms.EmailField(required=False)
    meeting_platform = forms.ChoiceField(
        required=False,
        choices=[("", "Select platform")] + Meeting.PLATFORM_CHOICES,
    )

    def clean(self):
        cleaned_data = super().clean()
        outcome = cleaned_data.get("outcome")
        if outcome == "follow_up_to_schedule" and not cleaned_data.get("follow_up_date"):
            raise ValidationError("Follow-up date is required.")
        if outcome in {"follow_up_to_schedule", "does_not_agree"} and not cleaned_data.get("reason"):
            raise ValidationError("A reason is required.")
        if outcome == "scheduled":
            if not cleaned_data.get("scheduled_for"):
                raise ValidationError("Meeting date and time is required.")
            if not cleaned_data.get("prospect_email"):
                raise ValidationError("Prospect email is required for a scheduled meeting.")
            if not cleaned_data.get("meeting_platform"):
                raise ValidationError("Meeting platform is required.")
        return cleaned_data


class MeetingReminderLogForm(StyledFormMixin, forms.ModelForm):
    class Meta:
        model = MeetingReminder
        fields = ["reminder_type", "recipient_number", "screenshot"]

    def __init__(self, *args, **kwargs):
        available_reminder_choices = kwargs.pop("available_reminder_choices", None)
        super().__init__(*args, **kwargs)
        self.fields["reminder_type"].choices = available_reminder_choices or []
        self.fields["recipient_number"].required = True
        self.fields["screenshot"].required = True


class ImportBatchForm(StyledFormMixin, forms.ModelForm):
    class Meta:
        model = CallImportBatch
        fields = ["import_date", "uploaded_file"]
        widgets = {
            "import_date": forms.DateInput(attrs={"type": "date"}),
        }


class CashflowSnapshotUploadForm(StyledFormMixin, forms.ModelForm):
    class Meta:
        model = CashflowSnapshot
        fields = ["as_of_date", "payables_file", "provisions_file", "receivables_file"]
        widgets = {
            "as_of_date": forms.DateInput(attrs={"type": "date"}),
        }


class CashflowCollectionRevisionForm(StyledFormMixin, forms.Form):
    revised_collection_date = forms.DateField(
        widget=forms.DateInput(attrs={"type": "date"}),
        label="Revised collection date",
    )


CASHFLOW_PLAN_SLOT_COUNT = 12


class CashflowImportedItemForm(StyledFormMixin, forms.ModelForm):
    class Meta:
        model = CashflowImportedItem
        fields = [
            "primary_classification",
            "secondary_classification",
            "cost_type",
            "recurring_payable_day",
        ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for position in range(1, CASHFLOW_PLAN_SLOT_COUNT + 1):
            self.fields[f"plan_{position}_amount"] = forms.DecimalField(
                required=False,
                min_value=0,
                decimal_places=2,
                max_digits=14,
                label=f"Payment {position} amount",
            )
            self.fields[f"plan_{position}_date"] = forms.DateField(
                required=False,
                widget=forms.DateInput(attrs={"type": "date"}),
                label=f"Payment {position} date",
            )
        if self.instance.pk:
            existing_plans = list(self.instance.payment_plans.all())
            for index, plan in enumerate(existing_plans[:CASHFLOW_PLAN_SLOT_COUNT], start=1):
                self.fields[f"plan_{index}_amount"].initial = plan.amount
                self.fields[f"plan_{index}_date"].initial = plan.payment_date
        self._apply_classes()

    def clean(self):
        cleaned_data = super().clean()
        cost_type = cleaned_data.get("cost_type")
        recurring_day = cleaned_data.get("recurring_payable_day")
        if cost_type == CashflowImportedItem.COST_RECURRING and not recurring_day:
            self.add_error("recurring_payable_day", "Recurring monthly items must include an approximate payable day.")
        payment_rows = []
        for position in range(1, CASHFLOW_PLAN_SLOT_COUNT + 1):
            amount = cleaned_data.get(f"plan_{position}_amount")
            payment_date = cleaned_data.get(f"plan_{position}_date")
            if amount is None and payment_date is None:
                continue
            if amount is None:
                self.add_error(f"plan_{position}_amount", "Payment amount is required when a payment date is entered.")
                continue
            if payment_date is None:
                self.add_error(f"plan_{position}_date", "Payment date is required when a payment amount is entered.")
                continue
            payment_rows.append(
                {
                    "position": position,
                    "amount": amount,
                    "payment_date": payment_date,
                }
            )
        cleaned_data["payment_plan_rows"] = payment_rows
        if self.instance.pk and self.instance.is_outflow:
            total_amount = sum((row["amount"] for row in payment_rows), Decimal("0.00"))
            if total_amount != (self.instance.amount or Decimal("0.00")):
                raise ValidationError(
                    f"Payment plan total must equal the imported amount of Rs {self.instance.amount}."
                )
        return cleaned_data


class CashflowProjectedCollectionForm(StyledFormMixin, forms.ModelForm):
    class Meta:
        model = CashflowProjectedCollection
        fields = [
            "company_name",
            "description",
            "amount",
            "expected_collection_date",
            "revised_collection_date",
        ]
        widgets = {
            "expected_collection_date": forms.DateInput(attrs={"type": "date"}),
            "revised_collection_date": forms.DateInput(attrs={"type": "date"}),
        }

    def clean_company_name(self):
        return (self.cleaned_data.get("company_name") or "").strip()

    def clean_description(self):
        return (self.cleaned_data.get("description") or "").strip()


WORK_ORDER_INSTALLMENT_SLOT_COUNT = 8


class CashflowWorkOrderRequestForm(StyledFormMixin, forms.ModelForm):
    class Meta:
        model = CashflowWorkOrderRequest
        fields = ["description", "party_name", "total_amount"]
        widgets = {
            "description": forms.Textarea(attrs={"rows": 4}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for position in range(1, WORK_ORDER_INSTALLMENT_SLOT_COUNT + 1):
            self.fields[f"installment_{position}_amount"] = forms.DecimalField(
                required=False,
                min_value=0,
                decimal_places=2,
                max_digits=14,
                label=f"Installment {position} amount",
            )
            self.fields[f"installment_{position}_payment_date"] = forms.DateField(
                required=False,
                widget=forms.DateInput(attrs={"type": "date"}),
                label=f"Installment {position} payment date",
            )
        self._apply_classes()

    def clean_party_name(self):
        return (self.cleaned_data.get("party_name") or "").strip()

    def clean_description(self):
        return (self.cleaned_data.get("description") or "").strip()

    def clean(self):
        cleaned_data = super().clean()
        installment_rows = []
        for position in range(1, WORK_ORDER_INSTALLMENT_SLOT_COUNT + 1):
            amount = cleaned_data.get(f"installment_{position}_amount")
            payment_date = cleaned_data.get(f"installment_{position}_payment_date")
            if amount is None and payment_date is None:
                continue
            if amount is None:
                self.add_error(
                    f"installment_{position}_amount",
                    "Installment amount is required when a payment date is entered.",
                )
                continue
            if payment_date is None:
                self.add_error(
                    f"installment_{position}_payment_date",
                    "Payment date is required when an installment amount is entered.",
                )
                continue
            installment_rows.append(
                {
                    "position": position,
                    "amount": amount,
                    "payment_date": payment_date,
                }
            )
        if not installment_rows:
            raise ValidationError("At least one installment amount and payment date is required.")
        total_amount = sum((row["amount"] for row in installment_rows), Decimal("0.00"))
        if cleaned_data.get("total_amount") is not None and total_amount != cleaned_data["total_amount"]:
            raise ValidationError("The sum of installment amounts must equal the total amount.")
        cleaned_data["installment_rows"] = installment_rows
        return cleaned_data


class PublicDownloadUploadForm(StyledFormMixin, forms.ModelForm):
    class Meta:
        model = PublicDownloadFile
        fields = ["title", "file"]
        widgets = {
            "title": forms.TextInput(attrs={"placeholder": "Optional label for your own reference"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["file"].help_text = "Maximum 200 MB per file."

    def clean(self):
        cleaned_data = super().clean()
        uploaded_file = cleaned_data.get("file")
        if uploaded_file:
            validate_uploaded_file_sizes(
                [uploaded_file],
                "public downloads",
                max_size=PUBLIC_DOWNLOAD_MAX_FILE_SIZE,
            )
        return cleaned_data


class MeetingStatusUpdateForm(StyledFormMixin, forms.ModelForm):
    rescheduled_for = forms.DateTimeField(
        required=False,
        widget=forms.DateTimeInput(attrs={"type": "datetime-local"}),
        input_formats=["%Y-%m-%dT%H:%M"],
        label="Reschedule to",
    )

    class Meta:
        model = Meeting
        fields = ["status"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["status"].choices = [
            (Meeting.STATUS_HAPPENED, "Meeting happened"),
            (Meeting.STATUS_DID_NOT_HAPPEN, "Did not happen"),
            (Meeting.STATUS_NO_SHOW, "Meeting did not happen - no show"),
            (Meeting.STATUS_RESCHEDULED, "Reschedule meeting"),
        ]
        if self.instance.pk:
            self.fields["rescheduled_for"].initial = timezone.localtime(self.instance.scheduled_for).strftime("%Y-%m-%dT%H:%M")

    def clean(self):
        cleaned_data = super().clean()
        status = cleaned_data.get("status")
        if status == Meeting.STATUS_RESCHEDULED and not cleaned_data.get("rescheduled_for"):
            self.add_error("rescheduled_for", "A new meeting date and time is required.")
        return cleaned_data


class SystemSettingForm(StyledFormMixin, forms.ModelForm):
    class Meta:
        model = SystemSetting
        fields = [
            "supervisor_name",
            "supervisor_sender_email",
            "default_timezone",
            "sales_email_1",
            "sales_email_2",
            "sales_email_3",
        ]


class ReportFilterForm(StyledFormMixin, forms.Form):
    start_date = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}))
    end_date = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}))

    def clean(self):
        cleaned_data = super().clean()
        start_date = cleaned_data.get("start_date")
        end_date = cleaned_data.get("end_date")
        if start_date and end_date and start_date > end_date:
            raise ValidationError("Start date must be before end date.")
        return cleaned_data


class MeetingDateFilterForm(StyledFormMixin, forms.Form):
    meeting_date = forms.DateField(
        required=False,
        widget=forms.DateInput(attrs={"type": "date"}),
        label="Meeting date",
    )


class DirectMarketingActivityForm(StyledFormMixin, forms.ModelForm):
    class Meta:
        model = DirectMarketingActivity
        fields = ["therapy_area", "sent_on", "prospect_count"]
        widgets = {
            "sent_on": forms.DateInput(attrs={"type": "date"}),
        }

    def clean_therapy_area(self):
        return (self.cleaned_data.get("therapy_area") or "").strip()


class SalesConversationForm(StyledFormMixin, forms.ModelForm):
    assigned_sales_manager = forms.ModelChoiceField(
        queryset=User.objects.none(),
        required=False,
        empty_label="Unassigned",
    )
    brands_input = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"rows": 3, "placeholder": "One brand per line or comma separated"}),
        label="Brands",
    )
    solution_files = MultiFileField(
        required=False,
        label="Solution files",
        help_text="Maximum 10 MB per file.",
    )
    proposal_files = MultiFileField(
        required=False,
        label="Proposal files",
        help_text="Maximum 10 MB per file.",
    )

    class Meta:
        model = SalesConversation
        fields = [
            "company_name",
            "assigned_sales_manager",
            "conversation_status",
            "proposal_status",
            "next_action_date",
            "contract_signed",
            "comments",
        ]
        widgets = {
            "next_action_date": forms.DateInput(attrs={"type": "date"}),
            "comments": forms.Textarea(attrs={"rows": 5}),
        }

    def __init__(self, *args, allow_company_name_edits=False, **kwargs):
        self.allow_company_name_edits = allow_company_name_edits
        super().__init__(*args, **kwargs)
        self.fields["assigned_sales_manager"].queryset = User.objects.filter(
            role=User.ROLE_SALES_MANAGER,
            is_active=True,
        ).order_by("name", "email")
        if self.instance.pk:
            self.fields["company_name"].disabled = not self.allow_company_name_edits
            if self.allow_company_name_edits:
                self.fields["company_name"].help_text = "Lead gen supervisors can correct the company name if it was entered incorrectly."
            else:
                self.fields["company_name"].help_text = "Company name is fixed after the sales conversation is created."
            self.fields["brands_input"].initial = "\n".join(self.instance.brands.values_list("name", flat=True))
            for contact in self.instance.contacts.all():
                self.fields[f"contact_{contact.position}_name"].initial = contact.name
                self.fields[f"contact_{contact.position}_email"].initial = contact.email
                self.fields[f"contact_{contact.position}_whatsapp"].initial = contact.whatsapp_number

    contact_1_name = forms.CharField(required=False, label="Contact person 1")
    contact_1_email = forms.EmailField(required=False, label="Contact person 1 email")
    contact_1_whatsapp = forms.CharField(required=False, label="Contact person 1 WhatsApp")
    contact_2_name = forms.CharField(required=False, label="Contact person 2")
    contact_2_email = forms.EmailField(required=False, label="Contact person 2 email")
    contact_2_whatsapp = forms.CharField(required=False, label="Contact person 2 WhatsApp")
    contact_3_name = forms.CharField(required=False, label="Contact person 3")
    contact_3_email = forms.EmailField(required=False, label="Contact person 3 email")
    contact_3_whatsapp = forms.CharField(required=False, label="Contact person 3 WhatsApp")

    def clean_brands_input(self):
        raw_value = (self.cleaned_data.get("brands_input") or "").replace(",", "\n")
        brands = []
        seen = set()
        for item in raw_value.splitlines():
            normalized = item.strip()
            lower_value = normalized.lower()
            if normalized and lower_value not in seen:
                brands.append(normalized)
                seen.add(lower_value)
        return brands

    def clean_company_name(self):
        if self.instance.pk and not self.allow_company_name_edits:
            return self.instance.company_name
        return (self.cleaned_data.get("company_name") or "").strip()

    def clean(self):
        cleaned_data = super().clean()
        contacts = []
        for index in range(1, 4):
            name = (cleaned_data.get(f"contact_{index}_name") or "").strip()
            email = (cleaned_data.get(f"contact_{index}_email") or "").strip().lower()
            whatsapp = (cleaned_data.get(f"contact_{index}_whatsapp") or "").strip()
            if email or whatsapp:
                if not name:
                    self.add_error(f"contact_{index}_name", "A contact name is required when email or WhatsApp is provided.")
            if name:
                contacts.append(
                    {
                        "position": index,
                        "name": name,
                        "email": email,
                        "whatsapp_number": whatsapp,
                    }
                )
        if not contacts:
            raise ValidationError("At least one contact person is required.")
        cleaned_data["contact_rows"] = contacts
        for field_name, label in (
            ("solution_files", "solution files"),
            ("proposal_files", "proposal files"),
        ):
            try:
                validate_uploaded_file_sizes(self.files.getlist(field_name), label)
            except ValidationError as exc:
                self.add_error(field_name, exc)
        return cleaned_data


class SalesConversationFilterForm(StyledFormMixin, forms.Form):
    conversation_status = forms.MultipleChoiceField(
        required=False,
        choices=SalesConversation.STATUS_CHOICES,
        label="Conversation status",
        widget=forms.SelectMultiple(attrs={"size": 6}),
    )
    proposal_status = forms.MultipleChoiceField(
        required=False,
        choices=SalesConversation.PROPOSAL_STATUS_CHOICES,
        label="Proposal status",
        widget=forms.SelectMultiple(attrs={"size": 5}),
    )
    next_action_date = forms.DateField(
        required=False,
        label="Next action date",
        widget=forms.DateInput(attrs={"type": "date"}),
    )
    brand = forms.CharField(required=False, label="Brand name")


class ContractCollectionForm(StyledFormMixin, forms.ModelForm):
    sales_manager = forms.ModelChoiceField(
        queryset=User.objects.none(),
        required=False,
        empty_label="Unassigned",
        label="Sales manager",
    )
    contract_files = MultiFileField(
        required=False,
        label="Contract files",
        help_text="Maximum 10 MB per file.",
    )

    class Meta:
        model = ContractCollection
        fields = ["company_name", "sales_manager", "contract_value"]

    contact_1_name = forms.CharField(required=False, label="Contact person 1")
    contact_1_email = forms.EmailField(required=False, label="Contact person 1 email")
    contact_1_whatsapp = forms.CharField(required=False, label="Contact person 1 WhatsApp")
    contact_2_name = forms.CharField(required=False, label="Contact person 2")
    contact_2_email = forms.EmailField(required=False, label="Contact person 2 email")
    contact_2_whatsapp = forms.CharField(required=False, label="Contact person 2 WhatsApp")
    contact_3_name = forms.CharField(required=False, label="Contact person 3")
    contact_3_email = forms.EmailField(required=False, label="Contact person 3 email")
    contact_3_whatsapp = forms.CharField(required=False, label="Contact person 3 WhatsApp")

    installment_1_amount = forms.DecimalField(required=False, min_value=0, decimal_places=2, max_digits=14, label="Installment 1 amount")
    installment_1_invoice_date = forms.DateField(required=False, widget=forms.DateInput(attrs={"type": "date"}), label="Installment 1 invoice date")
    installment_1_expected_collection_date = forms.DateField(required=False, widget=forms.DateInput(attrs={"type": "date"}), label="Installment 1 expected collection date")
    installment_1_revised_collection_date = forms.DateField(required=False, widget=forms.DateInput(attrs={"type": "date"}), label="Installment 1 revised collection date")
    installment_1_contract_summary = installment_textarea_field("Installment 1 contract summary")
    installment_1_service_description = installment_textarea_field("Installment 1 invoiced service description")
    installment_1_legal_due_reason = installment_textarea_field("Installment 1 why the invoice is legally due")
    installment_2_amount = forms.DecimalField(required=False, min_value=0, decimal_places=2, max_digits=14, label="Installment 2 amount")
    installment_2_invoice_date = forms.DateField(required=False, widget=forms.DateInput(attrs={"type": "date"}), label="Installment 2 invoice date")
    installment_2_expected_collection_date = forms.DateField(required=False, widget=forms.DateInput(attrs={"type": "date"}), label="Installment 2 expected collection date")
    installment_2_revised_collection_date = forms.DateField(required=False, widget=forms.DateInput(attrs={"type": "date"}), label="Installment 2 revised collection date")
    installment_2_contract_summary = installment_textarea_field("Installment 2 contract summary")
    installment_2_service_description = installment_textarea_field("Installment 2 invoiced service description")
    installment_2_legal_due_reason = installment_textarea_field("Installment 2 why the invoice is legally due")
    installment_3_amount = forms.DecimalField(required=False, min_value=0, decimal_places=2, max_digits=14, label="Installment 3 amount")
    installment_3_invoice_date = forms.DateField(required=False, widget=forms.DateInput(attrs={"type": "date"}), label="Installment 3 invoice date")
    installment_3_expected_collection_date = forms.DateField(required=False, widget=forms.DateInput(attrs={"type": "date"}), label="Installment 3 expected collection date")
    installment_3_revised_collection_date = forms.DateField(required=False, widget=forms.DateInput(attrs={"type": "date"}), label="Installment 3 revised collection date")
    installment_3_contract_summary = installment_textarea_field("Installment 3 contract summary")
    installment_3_service_description = installment_textarea_field("Installment 3 invoiced service description")
    installment_3_legal_due_reason = installment_textarea_field("Installment 3 why the invoice is legally due")
    installment_4_amount = forms.DecimalField(required=False, min_value=0, decimal_places=2, max_digits=14, label="Installment 4 amount")
    installment_4_invoice_date = forms.DateField(required=False, widget=forms.DateInput(attrs={"type": "date"}), label="Installment 4 invoice date")
    installment_4_expected_collection_date = forms.DateField(required=False, widget=forms.DateInput(attrs={"type": "date"}), label="Installment 4 expected collection date")
    installment_4_revised_collection_date = forms.DateField(required=False, widget=forms.DateInput(attrs={"type": "date"}), label="Installment 4 revised collection date")
    installment_4_contract_summary = installment_textarea_field("Installment 4 contract summary")
    installment_4_service_description = installment_textarea_field("Installment 4 invoiced service description")
    installment_4_legal_due_reason = installment_textarea_field("Installment 4 why the invoice is legally due")
    installment_5_amount = forms.DecimalField(required=False, min_value=0, decimal_places=2, max_digits=14, label="Installment 5 amount")
    installment_5_invoice_date = forms.DateField(required=False, widget=forms.DateInput(attrs={"type": "date"}), label="Installment 5 invoice date")
    installment_5_expected_collection_date = forms.DateField(required=False, widget=forms.DateInput(attrs={"type": "date"}), label="Installment 5 expected collection date")
    installment_5_revised_collection_date = forms.DateField(required=False, widget=forms.DateInput(attrs={"type": "date"}), label="Installment 5 revised collection date")
    installment_5_contract_summary = installment_textarea_field("Installment 5 contract summary")
    installment_5_service_description = installment_textarea_field("Installment 5 invoiced service description")
    installment_5_legal_due_reason = installment_textarea_field("Installment 5 why the invoice is legally due")
    installment_6_amount = forms.DecimalField(required=False, min_value=0, decimal_places=2, max_digits=14, label="Installment 6 amount")
    installment_6_invoice_date = forms.DateField(required=False, widget=forms.DateInput(attrs={"type": "date"}), label="Installment 6 invoice date")
    installment_6_expected_collection_date = forms.DateField(required=False, widget=forms.DateInput(attrs={"type": "date"}), label="Installment 6 expected collection date")
    installment_6_revised_collection_date = forms.DateField(required=False, widget=forms.DateInput(attrs={"type": "date"}), label="Installment 6 revised collection date")
    installment_6_contract_summary = installment_textarea_field("Installment 6 contract summary")
    installment_6_service_description = installment_textarea_field("Installment 6 invoiced service description")
    installment_6_legal_due_reason = installment_textarea_field("Installment 6 why the invoice is legally due")

    def __init__(self, *args, allow_locked_field_edits=False, allow_expected_collection_date_edits=False, **kwargs):
        self.allow_locked_field_edits = allow_locked_field_edits
        self.allow_expected_collection_date_edits = allow_expected_collection_date_edits
        super().__init__(*args, **kwargs)
        self.fields["sales_manager"].queryset = User.objects.filter(
            role=User.ROLE_SALES_MANAGER,
            is_active=True,
        ).order_by("name", "email")
        if self.instance.pk:
            self.fields["company_name"].disabled = True
            self.fields["company_name"].help_text = "Company name is fixed after the contract is created."
            for contact in self.instance.contacts.all():
                self.fields[f"contact_{contact.position}_name"].initial = contact.name
                self.fields[f"contact_{contact.position}_email"].initial = contact.email
                self.fields[f"contact_{contact.position}_whatsapp"].initial = contact.whatsapp_number
            installments = {item.position: item for item in self.instance.installments.all()}
            for position, installment in installments.items():
                if installment.installment_amount is not None:
                    self.fields[f"installment_{position}_amount"].initial = installment.installment_amount
                    self.fields[f"installment_{position}_amount"].disabled = not self.allow_locked_field_edits
                if installment.invoice_date is not None:
                    self.fields[f"installment_{position}_invoice_date"].initial = installment.invoice_date
                    self.fields[f"installment_{position}_invoice_date"].disabled = not self.allow_locked_field_edits
                if installment.expected_collection_date is not None:
                    self.fields[f"installment_{position}_expected_collection_date"].initial = installment.expected_collection_date
                    self.fields[f"installment_{position}_expected_collection_date"].disabled = not (
                        self.allow_locked_field_edits or self.allow_expected_collection_date_edits
                    )
                if installment.revised_collection_date is not None:
                    self.fields[f"installment_{position}_revised_collection_date"].initial = installment.revised_collection_date
                self.fields[f"installment_{position}_contract_summary"].initial = installment.contract_summary
                self.fields[f"installment_{position}_service_description"].initial = installment.invoiced_service_description
                self.fields[f"installment_{position}_legal_due_reason"].initial = installment.legal_due_reason
            if self.instance.contract_value is not None:
                self.fields["contract_value"].disabled = not self.allow_locked_field_edits
                if not self.allow_locked_field_edits:
                    self.fields["contract_value"].help_text = "Contract value can only be set once."
            if self.instance.files.exists():
                self.fields["contract_files"].disabled = True
                self.fields["contract_files"].help_text = "Contract files can only be uploaded once."

    def clean_company_name(self):
        if self.instance.pk:
            return self.instance.company_name
        return (self.cleaned_data.get("company_name") or "").strip()

    def clean(self):
        cleaned_data = super().clean()
        contacts = []
        for index in range(1, 4):
            name = (cleaned_data.get(f"contact_{index}_name") or "").strip()
            email = (cleaned_data.get(f"contact_{index}_email") or "").strip().lower()
            whatsapp = (cleaned_data.get(f"contact_{index}_whatsapp") or "").strip()
            if email or whatsapp:
                if not name:
                    self.add_error(f"contact_{index}_name", "A contact name is required when email or WhatsApp is provided.")
            if name:
                contacts.append(
                    {
                        "position": index,
                        "name": name,
                        "email": email,
                        "whatsapp_number": whatsapp,
                    }
                )
        if not contacts:
            raise ValidationError("At least one contact person is required.")
        cleaned_data["contact_rows"] = contacts

        installments = []
        for position in range(1, 7):
            amount = cleaned_data.get(f"installment_{position}_amount")
            invoice_date = cleaned_data.get(f"installment_{position}_invoice_date")
            expected_date = cleaned_data.get(f"installment_{position}_expected_collection_date")
            revised_date = cleaned_data.get(f"installment_{position}_revised_collection_date")
            contract_summary = (cleaned_data.get(f"installment_{position}_contract_summary") or "").strip()
            service_description = (cleaned_data.get(f"installment_{position}_service_description") or "").strip()
            legal_due_reason = (cleaned_data.get(f"installment_{position}_legal_due_reason") or "").strip()
            if any(
                value not in (None, "")
                for value in [
                    amount,
                    invoice_date,
                    expected_date,
                    revised_date,
                    contract_summary,
                    service_description,
                    legal_due_reason,
                ]
            ):
                if amount in (None, ""):
                    self.add_error(f"installment_{position}_amount", "Installment amount is required when installment dates are provided.")
                if invoice_date in (None, ""):
                    self.add_error(
                        f"installment_{position}_invoice_date",
                        "Invoice date is required when installment details are provided.",
                    )
                if expected_date in (None, ""):
                    self.add_error(
                        f"installment_{position}_expected_collection_date",
                        "Expected collection date is required when installment details are provided.",
                    )
                if not contract_summary:
                    self.add_error(
                        f"installment_{position}_contract_summary",
                        "Contract summary is required when installment details are provided.",
                    )
                if not service_description:
                    self.add_error(
                        f"installment_{position}_service_description",
                        "Invoiced service description is required when installment details are provided.",
                    )
                if not legal_due_reason:
                    self.add_error(
                        f"installment_{position}_legal_due_reason",
                        "Legal due reason is required when installment details are provided.",
                    )
                installments.append(
                    {
                        "position": position,
                        "installment_amount": amount,
                        "invoice_date": invoice_date,
                        "expected_collection_date": expected_date,
                        "revised_collection_date": revised_date,
                        "contract_summary": contract_summary,
                        "invoiced_service_description": service_description,
                        "legal_due_reason": legal_due_reason,
                    }
                )
        cleaned_data["installment_rows"] = installments
        try:
            validate_uploaded_file_sizes(self.files.getlist("contract_files"), "contract files")
        except ValidationError as exc:
            self.add_error("contract_files", exc)
        return cleaned_data


class FinanceCollectionUpdateForm(StyledFormMixin, forms.Form):
    installment_1_collected_amount = forms.DecimalField(required=False, min_value=0, decimal_places=2, max_digits=14, label="Installment 1 collected amount")
    installment_1_collection_date = forms.DateField(required=False, widget=forms.DateInput(attrs={"type": "date"}), label="Installment 1 collection date")
    installment_2_collected_amount = forms.DecimalField(required=False, min_value=0, decimal_places=2, max_digits=14, label="Installment 2 collected amount")
    installment_2_collection_date = forms.DateField(required=False, widget=forms.DateInput(attrs={"type": "date"}), label="Installment 2 collection date")
    installment_3_collected_amount = forms.DecimalField(required=False, min_value=0, decimal_places=2, max_digits=14, label="Installment 3 collected amount")
    installment_3_collection_date = forms.DateField(required=False, widget=forms.DateInput(attrs={"type": "date"}), label="Installment 3 collection date")
    installment_4_collected_amount = forms.DecimalField(required=False, min_value=0, decimal_places=2, max_digits=14, label="Installment 4 collected amount")
    installment_4_collection_date = forms.DateField(required=False, widget=forms.DateInput(attrs={"type": "date"}), label="Installment 4 collection date")
    installment_5_collected_amount = forms.DecimalField(required=False, min_value=0, decimal_places=2, max_digits=14, label="Installment 5 collected amount")
    installment_5_collection_date = forms.DateField(required=False, widget=forms.DateInput(attrs={"type": "date"}), label="Installment 5 collection date")
    installment_6_collected_amount = forms.DecimalField(required=False, min_value=0, decimal_places=2, max_digits=14, label="Installment 6 collected amount")
    installment_6_collection_date = forms.DateField(required=False, widget=forms.DateInput(attrs={"type": "date"}), label="Installment 6 collection date")

    def __init__(self, *args, contract_collection=None, **kwargs):
        self.contract_collection = contract_collection
        super().__init__(*args, **kwargs)
        if self.contract_collection:
            installments = {item.position: item for item in self.contract_collection.installments.all()}
            for position, installment in installments.items():
                self.fields[f"installment_{position}_collected_amount"].initial = installment.collected_amount
                self.fields[f"installment_{position}_collection_date"].initial = installment.collection_date

    def clean(self):
        cleaned_data = super().clean()
        finance_rows = []
        for position in range(1, 7):
            amount = cleaned_data.get(f"installment_{position}_collected_amount")
            collection_date = cleaned_data.get(f"installment_{position}_collection_date")
            if amount is not None and not collection_date:
                self.add_error(f"installment_{position}_collection_date", "Collection date is required when collected amount is entered.")
            if collection_date and amount is None:
                self.add_error(f"installment_{position}_collected_amount", "Collected amount is required when collection date is entered.")
            if amount is not None or collection_date is not None:
                finance_rows.append(
                    {
                        "position": position,
                        "collected_amount": amount,
                        "collection_date": collection_date,
                    }
                )
        cleaned_data["finance_rows"] = finance_rows
        return cleaned_data
