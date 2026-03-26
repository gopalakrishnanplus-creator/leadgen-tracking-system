from datetime import datetime, timedelta

from django.contrib import messages
from django.contrib.auth import logout
from django.contrib.auth.decorators import login_required
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from .decorators import role_required
from .forms import (
    CallOutcomeForm,
    ImportBatchForm,
    MeetingStatusUpdateForm,
    ProspectCreateForm,
    ProspectReviewForm,
    ReportFilterForm,
    StaffCreateForm,
    StaffUpdateForm,
    SystemSettingForm,
)
from .models import CallImportBatch, CallLog, Meeting, Prospect, ProspectStatusUpdate, SystemSetting, User
from .services import apply_call_outcome, import_exotel_report, update_meeting_outcome


def login_page(request):
    return render(request, "auth/login.html")


def logout_view(request):
    logout(request)
    return redirect("login")


@login_required
def home(request):
    if request.user.is_supervisor:
        return redirect("supervisor_dashboard")
    return redirect("staff_dashboard")


@role_required(User.ROLE_SUPERVISOR)
def supervisor_dashboard(request):
    context = {
        "staff_count": User.objects.filter(role=User.ROLE_STAFF, is_active=True).count(),
        "pending_review_count": Prospect.objects.filter(approval_status=Prospect.APPROVAL_PENDING).count(),
        "accepted_count": Prospect.objects.filter(approval_status=Prospect.APPROVAL_ACCEPTED).count(),
        "scheduled_count": Meeting.objects.filter(status=Meeting.STATUS_SCHEDULED).count(),
        "recent_imports": CallImportBatch.objects.all()[:5],
        "upcoming_meetings": Meeting.objects.filter(
            status=Meeting.STATUS_SCHEDULED,
            scheduled_for__gte=timezone.now(),
        )[:5],
    }
    return render(request, "leadgen/supervisor_dashboard.html", context)


@role_required(User.ROLE_SUPERVISOR)
def staff_list(request):
    staff_members = User.objects.filter(role=User.ROLE_STAFF).order_by("name")
    return render(request, "leadgen/staff_list.html", {"staff_members": staff_members})


@role_required(User.ROLE_SUPERVISOR)
def staff_create(request):
    form = StaffCreateForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Lead gen staff account created.")
        return redirect("staff_list")
    return render(request, "leadgen/staff_form.html", {"form": form, "title": "Add lead gen staff"})


def _get_staff_or_404(user_id):
    return get_object_or_404(User, pk=user_id, role=User.ROLE_STAFF)


@role_required(User.ROLE_SUPERVISOR)
def staff_update(request, user_id):
    staff_member = _get_staff_or_404(user_id)
    form = StaffUpdateForm(request.POST or None, instance=staff_member)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Lead gen staff updated.")
        return redirect("staff_list")
    return render(request, "leadgen/staff_form.html", {"form": form, "title": "Edit lead gen staff"})


@role_required(User.ROLE_SUPERVISOR)
def staff_delete(request, user_id):
    staff_member = _get_staff_or_404(user_id)
    if request.method == "POST":
        staff_member.is_active = False
        staff_member.save(update_fields=["is_active"])
        messages.success(request, "Lead gen staff deactivated.")
        return redirect("staff_list")
    return render(request, "leadgen/confirm_delete.html", {"object": staff_member, "title": "Deactivate lead gen staff"})


@role_required(User.ROLE_SUPERVISOR)
def supervisor_prospect_review(request):
    prospects = Prospect.objects.filter(approval_status=Prospect.APPROVAL_PENDING).select_related("assigned_to")
    return render(request, "leadgen/supervisor_prospect_review.html", {"prospects": prospects})


@role_required(User.ROLE_SUPERVISOR)
def review_prospect(request, prospect_id):
    prospect = get_object_or_404(Prospect, pk=prospect_id)
    form = ProspectReviewForm(request.POST or None, initial={"supervisor_notes": prospect.supervisor_notes})
    if request.method == "POST" and form.is_valid():
        prospect.supervisor_notes = form.cleaned_data["supervisor_notes"]
        prospect.reviewed_at = timezone.now()
        if form.cleaned_data["decision"] == "accept":
            prospect.approval_status = Prospect.APPROVAL_ACCEPTED
            prospect.workflow_status = Prospect.WORKFLOW_READY_TO_CALL
            prospect.accepted_at = timezone.now()
        else:
            prospect.approval_status = Prospect.APPROVAL_REJECTED
        prospect.save()
        messages.success(request, "Prospect review updated.")
        return redirect("supervisor_prospect_review")
    return render(request, "leadgen/review_prospect.html", {"prospect": prospect, "form": form})


@role_required(User.ROLE_SUPERVISOR)
def import_batch_create(request):
    form = ImportBatchForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        batch = form.save(commit=False)
        batch.imported_by = request.user
        batch.save()
        try:
            import_exotel_report(batch)
        except Exception as exc:
            batch.delete()
            messages.error(request, f"Import failed: {exc}")
        else:
            messages.success(
                request,
                f"Import completed. Rows: {batch.total_rows}, matched: {batch.matched_rows}, unmatched: {batch.unmatched_rows}, duplicates: {batch.duplicate_rows}.",
            )
            return redirect("import_batch_create")
    imports = CallImportBatch.objects.all()[:10]
    return render(request, "leadgen/import_batch_form.html", {"form": form, "imports": imports})


@role_required(User.ROLE_SUPERVISOR)
def supervisor_meeting_list(request):
    meetings = Meeting.objects.select_related("prospect", "scheduled_by")
    return render(request, "leadgen/supervisor_meeting_list.html", {"meetings": meetings})


@role_required(User.ROLE_SUPERVISOR)
def update_meeting_status(request, meeting_id):
    meeting = get_object_or_404(Meeting.objects.select_related("prospect", "scheduled_by"), pk=meeting_id)
    form = MeetingStatusUpdateForm(request.POST or None, instance=meeting)
    if request.method == "POST" and form.is_valid():
        update_meeting_outcome(meeting, form.cleaned_data["status"])
        messages.success(request, "Meeting status updated.")
        return redirect("supervisor_meeting_list")
    return render(request, "leadgen/update_meeting_status.html", {"meeting": meeting, "form": form})


@role_required(User.ROLE_SUPERVISOR)
def system_settings_view(request):
    settings_obj = SystemSetting.load()
    form = SystemSettingForm(request.POST or None, instance=settings_obj)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Settings updated.")
        return redirect("system_settings")
    return render(request, "leadgen/system_settings.html", {"form": form})


@role_required(User.ROLE_SUPERVISOR)
def supervisor_reports(request):
    today = timezone.localdate()
    initial = {
        "start_date": today - timedelta(days=30),
        "end_date": today,
    }
    form = ReportFilterForm(request.GET or None, initial=initial)
    report = None
    if form.is_valid():
        start_dt = timezone.make_aware(datetime.combine(form.cleaned_data["start_date"], datetime.min.time()))
        end_dt = timezone.make_aware(datetime.combine(form.cleaned_data["end_date"], datetime.max.time()))
        report = {
            "attempts": CallLog.objects.filter(started_at__range=(start_dt, end_dt)).count(),
            "connects": CallLog.objects.filter(started_at__range=(start_dt, end_dt), was_connected=True).count(),
            "follow_ups": ProspectStatusUpdate.objects.filter(
                created_at__range=(start_dt, end_dt),
                outcome=ProspectStatusUpdate.OUTCOME_FOLLOW_UP,
            ).count(),
            "meetings_scheduled": Meeting.objects.filter(created_at__range=(start_dt, end_dt)).count(),
            "meetings_not_happened": Meeting.objects.filter(
                created_at__range=(start_dt, end_dt),
                status=Meeting.STATUS_DID_NOT_HAPPEN,
            ).count(),
        }
    return render(request, "leadgen/supervisor_reports.html", {"form": form, "report": report})


@role_required(User.ROLE_STAFF)
def staff_dashboard(request):
    prospects = Prospect.objects.filter(assigned_to=request.user)
    context = {
        "total_prospects": prospects.count(),
        "pending_review_count": prospects.filter(approval_status=Prospect.APPROVAL_PENDING).count(),
        "accepted_count": prospects.filter(approval_status=Prospect.APPROVAL_ACCEPTED).count(),
        "follow_up_count": prospects.filter(workflow_status=Prospect.WORKFLOW_FOLLOW_UP).count(),
        "scheduled_count": prospects.filter(workflow_status=Prospect.WORKFLOW_SCHEDULED).count(),
        "recent_prospects": prospects.order_by("-created_at")[:5],
        "upcoming_meetings": Meeting.objects.filter(
            scheduled_by=request.user,
            status=Meeting.STATUS_SCHEDULED,
        ).select_related("prospect")[:5],
    }
    return render(request, "leadgen/staff_dashboard.html", context)


@role_required(User.ROLE_STAFF)
def staff_prospect_list(request):
    prospects = Prospect.objects.filter(assigned_to=request.user).order_by("-created_at")
    return render(request, "leadgen/staff_prospect_list.html", {"prospects": prospects})


@role_required(User.ROLE_STAFF)
def staff_prospect_create(request):
    form = ProspectCreateForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        prospect = form.save(commit=False)
        prospect.assigned_to = request.user
        prospect.created_by = request.user
        prospect.approval_status = Prospect.APPROVAL_PENDING
        prospect.workflow_status = Prospect.WORKFLOW_PENDING_REVIEW
        prospect.save()
        messages.success(request, "Prospect added for supervisor review.")
        return redirect("staff_prospect_list")
    return render(request, "leadgen/prospect_form.html", {"form": form})


def _get_staff_prospect(request, prospect_id):
    prospect = get_object_or_404(
        Prospect.objects.prefetch_related("status_updates", "meetings"),
        pk=prospect_id,
        assigned_to=request.user,
    )
    if prospect.approval_status != Prospect.APPROVAL_ACCEPTED:
        raise Http404("This prospect is not yet available for call updates.")
    return prospect


@role_required(User.ROLE_STAFF)
def update_call_outcome(request, prospect_id):
    prospect = _get_staff_prospect(request, prospect_id)
    form = CallOutcomeForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        apply_call_outcome(prospect, request.user, form.cleaned_data)
        messages.success(request, "Call outcome updated.")
        return redirect("staff_prospect_list")
    return render(
        request,
        "leadgen/update_call_outcome.html",
        {
            "prospect": prospect,
            "form": form,
            "status_updates": prospect.status_updates.all()[:10],
        },
    )


@role_required(User.ROLE_STAFF)
def staff_meeting_list(request):
    meetings = Meeting.objects.filter(scheduled_by=request.user).select_related("prospect")
    return render(request, "leadgen/staff_meeting_list.html", {"meetings": meetings})

# Create your views here.
