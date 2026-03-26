from django import forms
from django.core.exceptions import ValidationError

from .models import CallImportBatch, Meeting, Prospect, SystemSetting, User


class StyledFormMixin:
    def _apply_classes(self):
        for field in self.fields.values():
            classes = field.widget.attrs.get("class", "")
            field.widget.attrs["class"] = f"{classes} form-control".strip()

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._apply_classes()


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


class ProspectCreateForm(StyledFormMixin, forms.ModelForm):
    class Meta:
        model = Prospect
        fields = ["company_name", "contact_name", "linkedin_url", "phone_number"]


class ProspectReviewForm(StyledFormMixin, forms.Form):
    decision = forms.ChoiceField(
        choices=[("accept", "Accept"), ("reject", "Reject")],
        widget=forms.RadioSelect,
    )
    supervisor_notes = forms.CharField(widget=forms.Textarea(attrs={"rows": 3}), required=False)


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
        return cleaned_data


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
