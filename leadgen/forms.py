from django import forms
from django.core.exceptions import ValidationError
from django.core.validators import URLValidator

from .models import (
    CallImportBatch,
    ContractCollection,
    ContractCollectionInstallment,
    Meeting,
    MeetingReminder,
    Prospect,
    SalesConversation,
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
        if User.objects.filter(email=email).exists():
            raise ValidationError("A user with this email already exists.")
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
        qs = User.objects.exclude(pk=self.instance.pk).filter(email=email)
        if qs.exists():
            raise ValidationError("A user with this email already exists.")
        return email


class SalesManagerCreateForm(FixedRoleUserFormMixin, StyledFormMixin, forms.ModelForm):
    target_role = User.ROLE_SALES_MANAGER

    class Meta:
        model = User
        fields = ["name", "email", "whatsapp_number"]

    def clean_email(self):
        email = self.cleaned_data["email"].lower()
        if User.objects.filter(email=email).exists():
            raise ValidationError("A user with this email already exists.")
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
        qs = User.objects.exclude(pk=self.instance.pk).filter(email=email)
        if qs.exists():
            raise ValidationError("A user with this email already exists.")
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
        if User.objects.filter(email=email).exists():
            raise ValidationError("A user with this email already exists.")
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
        qs = User.objects.exclude(pk=self.instance.pk).filter(email=email)
        if qs.exists():
            raise ValidationError("A user with this email already exists.")
        return email

    def save(self, commit=True):
        user = super().save(commit=False)
        user.role = User.ROLE_FINANCE_MANAGER
        user.calling_number = None
        if commit:
            user.save()
        return user


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
        super().__init__(*args, **kwargs)
        self.fields["reminder_type"].choices = [
            (MeetingReminder.TYPE_WHATSAPP_INITIAL, "First WhatsApp reminder"),
            (MeetingReminder.TYPE_WHATSAPP_FINAL, "Second WhatsApp reminder"),
        ]
        self.fields["recipient_number"].required = True
        self.fields["screenshot"].required = True


class ImportBatchForm(StyledFormMixin, forms.ModelForm):
    class Meta:
        model = CallImportBatch
        fields = ["import_date", "uploaded_file"]
        widgets = {
            "import_date": forms.DateInput(attrs={"type": "date"}),
        }


class MeetingStatusUpdateForm(StyledFormMixin, forms.ModelForm):
    class Meta:
        model = Meeting
        fields = ["status"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["status"].choices = [
            (Meeting.STATUS_HAPPENED, "Meeting happened"),
            (Meeting.STATUS_DID_NOT_HAPPEN, "Did not happen"),
        ]


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
    solution_files = forms.FileField(
        required=False,
        widget=MultiFileInput(),
        label="Solution files",
    )
    proposal_files = forms.FileField(
        required=False,
        widget=MultiFileInput(),
        label="Proposal files",
    )

    class Meta:
        model = SalesConversation
        fields = [
            "company_name",
            "assigned_sales_manager",
            "conversation_status",
            "proposal_status",
            "contract_signed",
            "comments",
        ]
        widgets = {
            "comments": forms.Textarea(attrs={"rows": 5}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["assigned_sales_manager"].queryset = User.objects.filter(
            role=User.ROLE_SALES_MANAGER,
            is_active=True,
        ).order_by("name", "email")
        if self.instance.pk:
            self.fields["company_name"].disabled = True
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
        return cleaned_data


class SalesConversationFilterForm(StyledFormMixin, forms.Form):
    conversation_status = forms.ChoiceField(
        required=False,
        choices=[("", "All conversation statuses"), *SalesConversation.STATUS_CHOICES],
        label="Conversation status",
    )
    proposal_status = forms.ChoiceField(
        required=False,
        choices=[("", "All proposal statuses"), *SalesConversation.PROPOSAL_STATUS_CHOICES],
        label="Proposal status",
    )
    brand = forms.CharField(required=False, label="Brand name")


class ContractCollectionForm(StyledFormMixin, forms.ModelForm):
    sales_manager = forms.ModelChoiceField(
        queryset=User.objects.none(),
        required=False,
        empty_label="Unassigned",
        label="Sales manager",
    )
    contract_files = forms.FileField(
        required=False,
        widget=MultiFileInput(),
        label="Contract files",
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
    installment_2_amount = forms.DecimalField(required=False, min_value=0, decimal_places=2, max_digits=14, label="Installment 2 amount")
    installment_2_invoice_date = forms.DateField(required=False, widget=forms.DateInput(attrs={"type": "date"}), label="Installment 2 invoice date")
    installment_2_expected_collection_date = forms.DateField(required=False, widget=forms.DateInput(attrs={"type": "date"}), label="Installment 2 expected collection date")
    installment_2_revised_collection_date = forms.DateField(required=False, widget=forms.DateInput(attrs={"type": "date"}), label="Installment 2 revised collection date")
    installment_3_amount = forms.DecimalField(required=False, min_value=0, decimal_places=2, max_digits=14, label="Installment 3 amount")
    installment_3_invoice_date = forms.DateField(required=False, widget=forms.DateInput(attrs={"type": "date"}), label="Installment 3 invoice date")
    installment_3_expected_collection_date = forms.DateField(required=False, widget=forms.DateInput(attrs={"type": "date"}), label="Installment 3 expected collection date")
    installment_3_revised_collection_date = forms.DateField(required=False, widget=forms.DateInput(attrs={"type": "date"}), label="Installment 3 revised collection date")
    installment_4_amount = forms.DecimalField(required=False, min_value=0, decimal_places=2, max_digits=14, label="Installment 4 amount")
    installment_4_invoice_date = forms.DateField(required=False, widget=forms.DateInput(attrs={"type": "date"}), label="Installment 4 invoice date")
    installment_4_expected_collection_date = forms.DateField(required=False, widget=forms.DateInput(attrs={"type": "date"}), label="Installment 4 expected collection date")
    installment_4_revised_collection_date = forms.DateField(required=False, widget=forms.DateInput(attrs={"type": "date"}), label="Installment 4 revised collection date")
    installment_5_amount = forms.DecimalField(required=False, min_value=0, decimal_places=2, max_digits=14, label="Installment 5 amount")
    installment_5_invoice_date = forms.DateField(required=False, widget=forms.DateInput(attrs={"type": "date"}), label="Installment 5 invoice date")
    installment_5_expected_collection_date = forms.DateField(required=False, widget=forms.DateInput(attrs={"type": "date"}), label="Installment 5 expected collection date")
    installment_5_revised_collection_date = forms.DateField(required=False, widget=forms.DateInput(attrs={"type": "date"}), label="Installment 5 revised collection date")
    installment_6_amount = forms.DecimalField(required=False, min_value=0, decimal_places=2, max_digits=14, label="Installment 6 amount")
    installment_6_invoice_date = forms.DateField(required=False, widget=forms.DateInput(attrs={"type": "date"}), label="Installment 6 invoice date")
    installment_6_expected_collection_date = forms.DateField(required=False, widget=forms.DateInput(attrs={"type": "date"}), label="Installment 6 expected collection date")
    installment_6_revised_collection_date = forms.DateField(required=False, widget=forms.DateInput(attrs={"type": "date"}), label="Installment 6 revised collection date")

    def __init__(self, *args, **kwargs):
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
                    self.fields[f"installment_{position}_amount"].disabled = True
                if installment.invoice_date is not None:
                    self.fields[f"installment_{position}_invoice_date"].initial = installment.invoice_date
                    self.fields[f"installment_{position}_invoice_date"].disabled = True
                if installment.expected_collection_date is not None:
                    self.fields[f"installment_{position}_expected_collection_date"].initial = installment.expected_collection_date
                    self.fields[f"installment_{position}_expected_collection_date"].disabled = True
                if installment.revised_collection_date is not None:
                    self.fields[f"installment_{position}_revised_collection_date"].initial = installment.revised_collection_date
            if self.instance.contract_value is not None:
                self.fields["contract_value"].disabled = True
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
            if any(value not in (None, "") for value in [amount, invoice_date, expected_date, revised_date]):
                if amount in (None, ""):
                    self.add_error(f"installment_{position}_amount", "Installment amount is required when installment dates are provided.")
                installments.append(
                    {
                        "position": position,
                        "installment_amount": amount,
                        "invoice_date": invoice_date,
                        "expected_collection_date": expected_date,
                        "revised_collection_date": revised_date,
                    }
                )
        cleaned_data["installment_rows"] = installments
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
