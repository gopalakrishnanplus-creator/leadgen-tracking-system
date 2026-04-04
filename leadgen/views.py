from datetime import datetime, timedelta

from django.contrib import messages
from django.contrib.auth import logout
from django.contrib.auth.decorators import login_required
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from .decorators import role_required, roles_required
from .forms import (
    CallOutcomeForm,
    ImportBatchForm,
    MeetingStatusUpdateForm,
    ProspectCreateForm,
    ProspectReviewForm,
    ReportFilterForm,
    SalesConversationFilterForm,
    SalesConversationForm,
    SalesManagerCreateForm,
    SalesManagerUpdateForm,
    StaffCreateForm,
    StaffUpdateForm,
    SystemSettingForm,
)
from .models import (
    CallImportBatch,
    CallLog,
    Meeting,
    Prospect,
    ProspectStatusUpdate,
    SalesConversation,
    SalesConversationFile,
    SystemSetting,
    User,
)
from .services import (
    apply_call_outcome,
    build_supervisor_report,
    database_healthcheck,
    import_exotel_report,
    sync_sales_conversation_data,
    update_meeting_outcome,
)


def login_page(request):
    if request.user.is_authenticated:
        return redirect("home")
    return render(request, "auth/login.html")


def logout_view(request):
    logout(request)
    return redirect("login")


def healthcheck(request):
    ok = database_healthcheck()
    status = 200 if ok else 503
    return JsonResponse({"status": "ok" if ok else "error"}, status=status)


@login_required
def home(request):
    if request.user.is_supervisor:
        return redirect("supervisor_dashboard")
    if request.user.is_sales_manager:
        return redirect("sales_pipeline_dashboard")
    return redirect("staff_dashboard")


@role_required(User.ROLE_SUPERVISOR)
def supervisor_dashboard(request):
    overdue_meetings = Meeting.objects.filter(
        status=Meeting.STATUS_SCHEDULED,
        scheduled_for__lt=timezone.now(),
    )
    recent_calls = CallLog.objects.filter(started_at__isnull=False).select_related("staff", "prospect")[:8]
    context = {
        "staff_count": User.objects.filter(role=User.ROLE_STAFF, is_active=True).count(),
        "pending_review_count": Prospect.objects.filter(approval_status=Prospect.APPROVAL_PENDING).count(),
        "accepted_count": Prospect.objects.filter(approval_status=Prospect.APPROVAL_ACCEPTED).count(),
        "scheduled_count": Meeting.objects.filter(status=Meeting.STATUS_SCHEDULED).count(),
        "overdue_meeting_count": overdue_meetings.count(),
        "follow_up_count": Prospect.objects.filter(workflow_status=Prospect.WORKFLOW_FOLLOW_UP).count(),
        "recent_imports": CallImportBatch.objects.all()[:5],
        "upcoming_meetings": Meeting.objects.filter(
            status=Meeting.STATUS_SCHEDULED,
            scheduled_for__gte=timezone.now(),
        )[:5],
        "recent_calls": recent_calls,
        "sales_manager_count": User.objects.filter(role=User.ROLE_SALES_MANAGER, is_active=True).count(),
        "active_sales_pipeline_count": SalesConversation.objects.filter(contract_signed=False).count(),
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


def _get_sales_manager_or_404(user_id):
    return get_object_or_404(User, pk=user_id, role=User.ROLE_SALES_MANAGER)


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
def sales_manager_list(request):
    sales_managers = User.objects.filter(role=User.ROLE_SALES_MANAGER).order_by("name", "email")
    return render(request, "leadgen/sales_manager_list.html", {"sales_managers": sales_managers})


@role_required(User.ROLE_SUPERVISOR)
def sales_manager_create(request):
    form = SalesManagerCreateForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Sales manager account created.")
        return redirect("sales_manager_list")
    return render(request, "leadgen/sales_manager_form.html", {"form": form, "title": "Add sales manager"})


@role_required(User.ROLE_SUPERVISOR)
def sales_manager_update(request, user_id):
    sales_manager = _get_sales_manager_or_404(user_id)
    form = SalesManagerUpdateForm(request.POST or None, instance=sales_manager)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Sales manager updated.")
        return redirect("sales_manager_list")
    return render(request, "leadgen/sales_manager_form.html", {"form": form, "title": "Edit sales manager"})


@role_required(User.ROLE_SUPERVISOR)
def sales_manager_delete(request, user_id):
    sales_manager = _get_sales_manager_or_404(user_id)
    if request.method == "POST":
        sales_manager.is_active = False
        sales_manager.save(update_fields=["is_active"])
        messages.success(request, "Sales manager deactivated.")
        return redirect("sales_manager_list")
    return render(request, "leadgen/confirm_delete.html", {"object": sales_manager, "title": "Deactivate sales manager"})


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
        update_meeting_outcome(meeting, form.cleaned_data["status"], updated_by=request.user)
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
        report = build_supervisor_report(
            start_date=form.cleaned_data["start_date"],
            end_date=form.cleaned_data["end_date"],
            tz_name=SystemSetting.load().default_timezone,
        )
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
    form = ProspectCreateForm(
        request.POST or None,
        instance=Prospect(
            assigned_to=request.user,
            created_by=request.user,
        ),
    )
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


def _sales_pipeline_queryset_for_user(user):
    queryset = SalesConversation.objects.select_related("assigned_sales_manager", "source_meeting").prefetch_related(
        "contacts",
        "brands",
        "files",
    )
    if user.is_sales_manager:
        queryset = queryset.filter(assigned_sales_manager=user)
    return queryset


def _configure_sales_conversation_form(form, user):
    if user.is_sales_manager:
        form.fields["assigned_sales_manager"].queryset = User.objects.filter(pk=user.pk)
        form.fields["assigned_sales_manager"].empty_label = None
        if not form.is_bound and not form.instance.assigned_sales_manager_id:
            form.initial["assigned_sales_manager"] = user.pk
    return form


def _sales_conversation_for_user_or_404(user, conversation_id):
    queryset = _sales_pipeline_queryset_for_user(user)
    conversation = get_object_or_404(queryset, pk=conversation_id)
    if user.is_sales_manager and conversation.assigned_sales_manager_id != user.pk:
        raise Http404("You do not have access to this sales conversation.")
    return conversation


def _uploaded_sales_files(request):
    return {
        "solution_files": request.FILES.getlist("solution_files"),
        "proposal_files": request.FILES.getlist("proposal_files"),
    }


@roles_required(User.ROLE_SUPERVISOR, User.ROLE_SALES_MANAGER)
def sales_pipeline_dashboard(request):
    conversations = _sales_pipeline_queryset_for_user(request.user).filter(contract_signed=False)
    form = SalesConversationFilterForm(request.GET or None)
    if form.is_valid():
        if form.cleaned_data["conversation_status"]:
            conversations = conversations.filter(conversation_status=form.cleaned_data["conversation_status"])
        if form.cleaned_data["proposal_status"]:
            conversations = conversations.filter(proposal_status=form.cleaned_data["proposal_status"])
        if form.cleaned_data["brand"]:
            conversations = conversations.filter(brands__name__icontains=form.cleaned_data["brand"].strip())
    conversations = conversations.distinct().order_by("-updated_at")
    context = {
        "form": form,
        "conversations": conversations,
        "active_count": conversations.count(),
        "signed_count": _sales_pipeline_queryset_for_user(request.user).filter(contract_signed=True).count(),
    }
    return render(request, "leadgen/sales_pipeline_dashboard.html", context)


@roles_required(User.ROLE_SUPERVISOR, User.ROLE_SALES_MANAGER)
def sales_conversation_create(request):
    form = SalesConversationForm(request.POST or None, request.FILES or None)
    form = _configure_sales_conversation_form(form, request.user)
    if request.method == "POST" and form.is_valid():
        conversation = form.save(commit=False)
        if request.user.is_sales_manager:
            conversation.assigned_sales_manager = request.user
        conversation.created_by = request.user
        conversation.save()
        sync_sales_conversation_data(conversation, form.cleaned_data, _uploaded_sales_files(request))
        messages.success(request, "Sales conversation created.")
        return redirect("sales_pipeline_dashboard")
    return render(
        request,
        "leadgen/sales_conversation_form.html",
        {
            "form": form,
            "title": "Add sales conversation",
            "conversation": None,
            "existing_solution_files": [],
            "existing_proposal_files": [],
        },
    )


@roles_required(User.ROLE_SUPERVISOR, User.ROLE_SALES_MANAGER)
def sales_conversation_update(request, conversation_id):
    conversation = _sales_conversation_for_user_or_404(request.user, conversation_id)
    form = SalesConversationForm(request.POST or None, request.FILES or None, instance=conversation)
    form = _configure_sales_conversation_form(form, request.user)
    if request.method == "POST" and form.is_valid():
        conversation = form.save(commit=False)
        if request.user.is_sales_manager:
            conversation.assigned_sales_manager = request.user
        conversation.save()
        sync_sales_conversation_data(conversation, form.cleaned_data, _uploaded_sales_files(request))
        if conversation.contract_signed:
            messages.success(request, "Sales conversation updated and removed from the active pipeline.")
            return redirect("sales_pipeline_dashboard")
        messages.success(request, "Sales conversation updated.")
        return redirect("sales_conversation_update", conversation_id=conversation.pk)
    return render(
        request,
        "leadgen/sales_conversation_form.html",
        {
            "form": form,
            "title": f"Sales conversation {conversation.sales_conversation_id}",
            "conversation": conversation,
            "existing_solution_files": conversation.files.filter(category=SalesConversationFile.CATEGORY_SOLUTION),
            "existing_proposal_files": conversation.files.filter(category=SalesConversationFile.CATEGORY_PROPOSAL),
        },
    )

# Create your views here.
