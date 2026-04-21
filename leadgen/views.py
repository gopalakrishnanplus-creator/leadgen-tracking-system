from datetime import datetime, timedelta
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from django.contrib import messages
from django.contrib.auth import logout
from django.contrib.auth.decorators import login_required
from django.db.models import Prefetch
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme

from .decorators import role_required, roles_required, supervisor_access_required
from .forms import (
    CallOutcomeForm,
    ContractCollectionForm,
    FinanceCollectionUpdateForm,
    FinanceManagerCreateForm,
    FinanceManagerUpdateForm,
    ImportBatchForm,
    MeetingDateFilterForm,
    MeetingStatusUpdateForm,
    MeetingReminderLogForm,
    ProspectCreateForm,
    SupervisorProspectActionForm,
    SupervisorAccessEmailForm,
    ProspectReviewForm,
    ReportFilterForm,
    SalesConversationFilterForm,
    SalesConversationForm,
    SalesManagerCreateForm,
    SalesManagerUpdateForm,
    StaffCreateForm,
    StaffUpdateForm,
    SupervisorProspectCreateForm,
    SystemSettingForm,
)
from .models import (
    CallImportBatch,
    CallLog,
    ContractCollection,
    Meeting,
    Prospect,
    ProspectStatusUpdate,
    SalesConversation,
    SalesConversationFile,
    SupervisorAccessEmail,
    SystemSetting,
    User,
)
from .services import (
    apply_call_outcome,
    build_daily_target_report,
    build_pending_collections,
    build_reminder_dashboard,
    build_supervisor_report,
    database_healthcheck,
    delete_meeting,
    get_or_create_contract_collection_from_sales_conversation,
    import_exotel_report,
    log_whatsapp_reminder,
    reschedule_meeting,
    send_due_invoice_notifications,
    sync_contract_collection_data,
    sync_finance_collection_data,
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


STAFF_HIDDEN_WORKFLOWS = {
    Prospect.WORKFLOW_INVALID_NUMBER,
    Prospect.WORKFLOW_SUPERVISOR_ACTION,
}

YESTERDAY_CALLS_FILTER = "yesterday_calls"


@login_required
def home(request):
    if getattr(request, "is_dual_workspace_user", False):
        if request.current_workspace == "supervisor":
            return redirect("supervisor_dashboard")
        if request.current_workspace == "sales":
            return redirect("sales_pipeline_dashboard")
        return workspace_choice(request)
    if getattr(request, "current_workspace", None) == "supervisor":
        return redirect("supervisor_dashboard")
    if getattr(request, "current_workspace", None) == "sales":
        return redirect("sales_pipeline_dashboard")
    if getattr(request, "current_workspace", None) == "finance":
        return redirect("contracts_dashboard")
    return redirect("staff_dashboard")


@login_required
def workspace_choice(request):
    if not getattr(request, "is_dual_workspace_user", False):
        return redirect("home")
    return render(request, "leadgen/workspace_choice.html")


@login_required
def select_workspace(request, workspace):
    if workspace not in {"supervisor", "sales"}:
        raise Http404("Workspace not found.")
    if not getattr(request, "is_dual_workspace_user", False):
        return redirect("home")
    if workspace == "supervisor" and not getattr(request, "has_supervisor_workspace_access", False):
        raise Http404("Workspace not found.")
    if workspace == "sales" and not getattr(request, "has_sales_workspace_access", False):
        raise Http404("Workspace not found.")
    request.session["workspace_mode"] = workspace
    if workspace == "supervisor":
        return redirect("supervisor_dashboard")
    return redirect("sales_pipeline_dashboard")


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
        "invalid_number_count": Prospect.objects.filter(workflow_status=Prospect.WORKFLOW_INVALID_NUMBER).count(),
        "supervisor_action_count": Prospect.objects.filter(workflow_status=Prospect.WORKFLOW_SUPERVISOR_ACTION).count(),
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
        "finance_manager_count": User.objects.filter(role=User.ROLE_FINANCE_MANAGER, is_active=True).count(),
        "active_sales_pipeline_count": SalesConversation.objects.filter(contract_signed=False).count(),
        "active_contract_count": ContractCollection.objects.count(),
    }
    return render(request, "leadgen/supervisor_dashboard.html", context)


def _supervisor_user_management_context(request, access_form=None):
    return {
        "is_system_admin_view": getattr(request, "is_system_admin", False),
        "is_leadgen_supervisor_view": getattr(request, "is_leadgen_supervisor", False),
        "supervisor_access_form": access_form or SupervisorAccessEmailForm(),
        "system_admin_access_emails": SupervisorAccessEmail.objects.filter(
            access_level=SupervisorAccessEmail.ACCESS_SYSTEM_ADMIN
        ).order_by("email"),
        "leadgen_supervisor_access_emails": SupervisorAccessEmail.objects.filter(
            access_level=SupervisorAccessEmail.ACCESS_LEADGEN_SUPERVISOR
        ).order_by("email"),
        "staff_members": User.objects.filter(role=User.ROLE_STAFF).order_by("name", "email"),
        "sales_managers": User.objects.filter(role=User.ROLE_SALES_MANAGER).order_by("name", "email"),
        "finance_managers": User.objects.filter(role=User.ROLE_FINANCE_MANAGER).order_by("name", "email"),
    }


@supervisor_access_required(
    SupervisorAccessEmail.ACCESS_SYSTEM_ADMIN,
    SupervisorAccessEmail.ACCESS_LEADGEN_SUPERVISOR,
)
def supervisor_user_management(request):
    access_form = SupervisorAccessEmailForm(request.POST or None)
    if request.method == "POST":
        if not request.is_system_admin:
            return redirect("supervisor_user_management")
        if access_form.is_valid():
            reactivated_instance = access_form.reactivated_instance
            if reactivated_instance is not None:
                reactivated_instance.is_active = True
                reactivated_instance.save(update_fields=["is_active", "updated_at"])
            else:
                access_form.save()
            messages.success(request, "Supervisor access updated.")
            return redirect("supervisor_user_management")
        messages.error(request, "Please correct the supervisor access email and try again.")
    return render(
        request,
        "leadgen/supervisor_user_management.html",
        _supervisor_user_management_context(request, access_form),
    )


@supervisor_access_required(SupervisorAccessEmail.ACCESS_SYSTEM_ADMIN)
def supervisor_access_email_delete(request, access_email_id):
    access_email = get_object_or_404(
        SupervisorAccessEmail,
        pk=access_email_id,
        access_level=SupervisorAccessEmail.ACCESS_LEADGEN_SUPERVISOR,
    )
    if request.method == "POST":
        access_email.is_active = False
        access_email.save(update_fields=["is_active", "updated_at"])
        messages.success(request, "Supervisor access removed.")
        return redirect("supervisor_user_management")
    return render(
        request,
        "leadgen/confirm_delete.html",
        {
            "object": access_email,
            "title": "Remove supervisor access",
            "description": "This will stop this Google email from opening the supervisor dashboard.",
        },
    )


@role_required(User.ROLE_SUPERVISOR)
def staff_list(request):
    staff_members = User.objects.filter(role=User.ROLE_STAFF).order_by("name")
    return render(request, "leadgen/staff_list.html", {"staff_members": staff_members})


@supervisor_access_required(
    SupervisorAccessEmail.ACCESS_SYSTEM_ADMIN,
    SupervisorAccessEmail.ACCESS_LEADGEN_SUPERVISOR,
)
def staff_create(request):
    form = StaffCreateForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Lead gen staff account created.")
        return redirect("supervisor_user_management")
    return render(request, "leadgen/staff_form.html", {"form": form, "title": "Add lead gen staff"})


def _build_staff_dashboard_context(staff_user):
    prospects = _visible_staff_prospects_queryset(staff_user)
    return {
        "dashboard_owner": staff_user,
        "total_prospects": prospects.count(),
        "pending_review_count": prospects.filter(approval_status=Prospect.APPROVAL_PENDING).count(),
        "accepted_count": prospects.filter(approval_status=Prospect.APPROVAL_ACCEPTED).count(),
        "follow_up_count": prospects.filter(workflow_status=Prospect.WORKFLOW_FOLLOW_UP).count(),
        "scheduled_count": prospects.filter(workflow_status=Prospect.WORKFLOW_SCHEDULED).count(),
        "recent_prospects": prospects.order_by("-created_at")[:5],
        "upcoming_meetings": Meeting.objects.filter(
            scheduled_by=staff_user,
            status=Meeting.STATUS_SCHEDULED,
        ).select_related("prospect")[:5],
    }


def _visible_staff_prospects_queryset(staff_user):
    return Prospect.objects.filter(assigned_to=staff_user).exclude(workflow_status__in=STAFF_HIDDEN_WORKFLOWS)


def _local_day_bounds(target_date, tz_name):
    tz = ZoneInfo(tz_name)
    start_dt = timezone.make_aware(datetime.combine(target_date, datetime.min.time()), tz)
    end_dt = timezone.make_aware(datetime.combine(target_date, datetime.max.time()), tz)
    return start_dt, end_dt


def _yesterday_bounds(tz_name):
    tz = ZoneInfo(tz_name)
    target_date = timezone.localtime(timezone.now(), tz).date() - timedelta(days=1)
    start_dt, end_dt = _local_day_bounds(target_date, tz_name)
    return target_date, start_dt, end_dt


def _safe_next_url(request, default_url):
    next_url = request.POST.get("next") or request.GET.get("next") or ""
    if next_url and url_has_allowed_host_and_scheme(next_url, allowed_hosts={request.get_host()}):
        return next_url
    return default_url


def _filtered_staff_prospects_queryset(staff_user, status_filter, include_hidden=False):
    prospects = Prospect.objects.filter(assigned_to=staff_user).select_related("assigned_to", "created_by")
    if not include_hidden:
        prospects = prospects.exclude(workflow_status__in=STAFF_HIDDEN_WORKFLOWS)
    if status_filter == "accepted":
        prospects = prospects.filter(approval_status=Prospect.APPROVAL_ACCEPTED)
    elif status_filter == YESTERDAY_CALLS_FILTER:
        _, start_dt, end_dt = _yesterday_bounds(SystemSetting.load().default_timezone)
        prospects = prospects.filter(call_logs__started_at__range=(start_dt, end_dt)).distinct()
    return prospects.order_by("-latest_call_attempt_at", "-created_at").prefetch_related(
        Prefetch("call_logs", queryset=CallLog.objects.order_by("-started_at", "-created_at")),
        Prefetch("status_updates", queryset=ProspectStatusUpdate.objects.order_by("-created_at")),
        Prefetch("meetings", queryset=Meeting.objects.order_by("-created_at", "-scheduled_for")),
    )


def _build_staff_prospect_rows(prospects, status_filter):
    review_date = None
    start_dt = end_dt = None
    if status_filter == YESTERDAY_CALLS_FILTER:
        review_date, start_dt, end_dt = _yesterday_bounds(SystemSetting.load().default_timezone)

    rows = []
    for prospect in prospects:
        call_logs = list(prospect.call_logs.all())
        status_updates = list(prospect.status_updates.all())
        meetings = list(prospect.meetings.all())
        if start_dt and end_dt:
            call_logs = [item for item in call_logs if item.started_at and start_dt <= item.started_at <= end_dt]
            status_updates = [item for item in status_updates if start_dt <= item.created_at <= end_dt]
            meetings = [item for item in meetings if start_dt <= item.created_at <= end_dt]
        rows.append(
            {
                "prospect": prospect,
                "latest_call_log": call_logs[0] if call_logs else None,
                "latest_status_update": status_updates[0] if status_updates else None,
                "latest_meeting": meetings[0] if meetings else None,
            }
        )
    return rows, review_date


@role_required(User.ROLE_SUPERVISOR)
def supervisor_staff_dashboard(request, user_id):
    staff_member = _get_staff_or_404(user_id)
    context = _build_staff_dashboard_context(staff_member)
    context["is_supervisor_view"] = True
    return render(request, "leadgen/staff_dashboard.html", context)


def _get_staff_or_404(user_id):
    return get_object_or_404(User, pk=user_id, role=User.ROLE_STAFF)


def _get_sales_manager_or_404(user_id):
    return get_object_or_404(User, pk=user_id, role=User.ROLE_SALES_MANAGER)


def _get_finance_manager_or_404(user_id):
    return get_object_or_404(User, pk=user_id, role=User.ROLE_FINANCE_MANAGER)


@supervisor_access_required(
    SupervisorAccessEmail.ACCESS_SYSTEM_ADMIN,
    SupervisorAccessEmail.ACCESS_LEADGEN_SUPERVISOR,
)
def staff_update(request, user_id):
    staff_member = _get_staff_or_404(user_id)
    form = StaffUpdateForm(request.POST or None, instance=staff_member)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Lead gen staff updated.")
        return redirect("supervisor_user_management")
    return render(request, "leadgen/staff_form.html", {"form": form, "title": "Edit lead gen staff"})


@supervisor_access_required(
    SupervisorAccessEmail.ACCESS_SYSTEM_ADMIN,
    SupervisorAccessEmail.ACCESS_LEADGEN_SUPERVISOR,
)
def staff_delete(request, user_id):
    staff_member = _get_staff_or_404(user_id)
    if request.method == "POST":
        staff_member.is_active = False
        staff_member.save(update_fields=["is_active"])
        messages.success(request, "Lead gen staff deactivated.")
        return redirect("supervisor_user_management")
    return render(request, "leadgen/confirm_delete.html", {"object": staff_member, "title": "Deactivate lead gen staff"})


@supervisor_access_required(SupervisorAccessEmail.ACCESS_SYSTEM_ADMIN)
def sales_manager_list(request):
    sales_managers = User.objects.filter(role=User.ROLE_SALES_MANAGER).order_by("name", "email")
    return render(request, "leadgen/sales_manager_list.html", {"sales_managers": sales_managers})


@supervisor_access_required(SupervisorAccessEmail.ACCESS_SYSTEM_ADMIN)
def sales_manager_create(request):
    form = SalesManagerCreateForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Sales manager account created.")
        return redirect("supervisor_user_management")
    return render(request, "leadgen/sales_manager_form.html", {"form": form, "title": "Add sales manager"})


@supervisor_access_required(SupervisorAccessEmail.ACCESS_SYSTEM_ADMIN)
def sales_manager_update(request, user_id):
    sales_manager = _get_sales_manager_or_404(user_id)
    form = SalesManagerUpdateForm(request.POST or None, instance=sales_manager)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Sales manager updated.")
        return redirect("supervisor_user_management")
    return render(request, "leadgen/sales_manager_form.html", {"form": form, "title": "Edit sales manager"})


@supervisor_access_required(SupervisorAccessEmail.ACCESS_SYSTEM_ADMIN)
def sales_manager_delete(request, user_id):
    sales_manager = _get_sales_manager_or_404(user_id)
    if request.method == "POST":
        sales_manager.is_active = False
        sales_manager.save(update_fields=["is_active"])
        messages.success(request, "Sales manager deactivated.")
        return redirect("supervisor_user_management")
    return render(request, "leadgen/confirm_delete.html", {"object": sales_manager, "title": "Deactivate sales manager"})


@supervisor_access_required(SupervisorAccessEmail.ACCESS_SYSTEM_ADMIN)
def finance_manager_list(request):
    finance_managers = User.objects.filter(role=User.ROLE_FINANCE_MANAGER).order_by("name", "email")
    return render(request, "leadgen/finance_manager_list.html", {"finance_managers": finance_managers})


@supervisor_access_required(SupervisorAccessEmail.ACCESS_SYSTEM_ADMIN)
def finance_manager_create(request):
    form = FinanceManagerCreateForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Finance manager account created.")
        return redirect("supervisor_user_management")
    return render(request, "leadgen/finance_manager_form.html", {"form": form, "title": "Add finance manager"})


@supervisor_access_required(SupervisorAccessEmail.ACCESS_SYSTEM_ADMIN)
def finance_manager_update(request, user_id):
    finance_manager = _get_finance_manager_or_404(user_id)
    form = FinanceManagerUpdateForm(request.POST or None, instance=finance_manager)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Finance manager updated.")
        return redirect("supervisor_user_management")
    return render(request, "leadgen/finance_manager_form.html", {"form": form, "title": "Edit finance manager"})


@supervisor_access_required(SupervisorAccessEmail.ACCESS_SYSTEM_ADMIN)
def finance_manager_delete(request, user_id):
    finance_manager = _get_finance_manager_or_404(user_id)
    if request.method == "POST":
        finance_manager.is_active = False
        finance_manager.save(update_fields=["is_active"])
        messages.success(request, "Finance manager deactivated.")
        return redirect("supervisor_user_management")
    return render(request, "leadgen/confirm_delete.html", {"object": finance_manager, "title": "Deactivate finance manager"})


@role_required(User.ROLE_SUPERVISOR)
def supervisor_prospect_review(request):
    prospects = Prospect.objects.filter(approval_status=Prospect.APPROVAL_PENDING).select_related("assigned_to")
    return render(request, "leadgen/supervisor_prospect_review.html", {"prospects": prospects})


@role_required(User.ROLE_SUPERVISOR)
def supervisor_prospect_list(request):
    prospects = Prospect.objects.select_related("assigned_to", "created_by").order_by("-created_at")
    return render(request, "leadgen/supervisor_prospect_list.html", {"prospects": prospects})


@role_required(User.ROLE_SUPERVISOR)
def supervisor_invalid_prospect_list(request):
    prospects = Prospect.objects.filter(workflow_status=Prospect.WORKFLOW_INVALID_NUMBER).select_related("assigned_to")
    return render(request, "leadgen/supervisor_invalid_prospect_list.html", {"prospects": prospects})


@role_required(User.ROLE_SUPERVISOR)
def supervisor_action_prospect_list(request):
    prospects = Prospect.objects.filter(workflow_status=Prospect.WORKFLOW_SUPERVISOR_ACTION).select_related("assigned_to")
    return render(request, "leadgen/supervisor_action_prospect_list.html", {"prospects": prospects})


@role_required(User.ROLE_SUPERVISOR)
def supervisor_action_prospect_manage(request, prospect_id):
    prospect = get_object_or_404(Prospect.objects.select_related("assigned_to"), pk=prospect_id)
    if prospect.workflow_status != Prospect.WORKFLOW_SUPERVISOR_ACTION:
        raise Http404("This prospect does not require supervisor action.")
    form = SupervisorProspectActionForm(
        request.POST or None,
        initial={
            "assigned_to": prospect.assigned_to,
            "supervisor_notes": prospect.supervisor_notes,
        },
    )
    if request.method == "POST" and form.is_valid():
        action = request.POST.get("action")
        prospect.assigned_to = form.cleaned_data["assigned_to"]
        prospect.supervisor_notes = form.cleaned_data["supervisor_notes"]
        if action == "reassign":
            prospect.workflow_status = Prospect.WORKFLOW_READY_TO_CALL
            prospect.system_action_note = ""
            prospect.no_answer_reset_count = prospect.call_logs.filter(crm_status=CallLog.STATUS_NO_ANSWER).count()
            prospect.save(
                update_fields=[
                    "assigned_to",
                    "supervisor_notes",
                    "workflow_status",
                    "system_action_note",
                    "no_answer_reset_count",
                    "updated_at",
                ]
            )
            messages.success(request, "Prospect reassigned and returned to the lead gen staff dashboard.")
            return redirect("supervisor_staff_dashboard", user_id=prospect.assigned_to_id)
        if action == "mark_invalid":
            prospect.workflow_status = Prospect.WORKFLOW_INVALID_NUMBER
            prospect.system_action_note = "Marked invalid by supervisor after five unanswered attempts."
            prospect.follow_up_date = None
            prospect.follow_up_reason = ""
            prospect.save(
                update_fields=[
                    "assigned_to",
                    "supervisor_notes",
                    "workflow_status",
                    "system_action_note",
                    "follow_up_date",
                    "follow_up_reason",
                    "updated_at",
                ]
            )
            messages.success(request, "Prospect marked as an invalid number.")
            return redirect("supervisor_invalid_prospect_list")
        messages.error(request, "Choose a valid action.")
    return render(
        request,
        "leadgen/supervisor_action_prospect_manage.html",
        {"prospect": prospect, "form": form},
    )


@role_required(User.ROLE_SUPERVISOR)
def supervisor_prospect_create(request):
    form = SupervisorProspectCreateForm(
        request.POST or None,
        instance=Prospect(created_by=request.user),
    )
    if request.method == "POST" and form.is_valid():
        prospect = form.save(commit=False)
        prospect.created_by = request.user
        prospect.approval_status = Prospect.APPROVAL_ACCEPTED
        prospect.workflow_status = Prospect.WORKFLOW_READY_TO_CALL
        prospect.reviewed_at = timezone.now()
        prospect.accepted_at = timezone.now()
        prospect.save()
        messages.success(request, "Prospect added and assigned to lead gen staff.")
        return redirect("supervisor_staff_dashboard", user_id=prospect.assigned_to_id)
    return render(
        request,
        "leadgen/prospect_form.html",
        {
            "form": form,
            "eyebrow": "Supervisor",
            "title": "Add prospect",
            "submit_label": "Add prospect",
        },
    )


@role_required(User.ROLE_SUPERVISOR)
def review_prospect(request, prospect_id):
    prospect = get_object_or_404(Prospect, pk=prospect_id)
    form = ProspectReviewForm(
        request.POST or None,
        initial={
            "assigned_to": prospect.assigned_to,
            "supervisor_notes": prospect.supervisor_notes,
        },
    )
    if request.method == "POST" and form.is_valid():
        prospect.assigned_to = form.cleaned_data["assigned_to"]
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
def supervisor_prospect_delete(request, prospect_id):
    prospect = get_object_or_404(Prospect.objects.select_related("assigned_to"), pk=prospect_id)
    fallback_url = reverse("supervisor_prospect_list")
    next_url = _safe_next_url(request, fallback_url)
    if request.method == "POST":
        company_name = prospect.company_name
        contact_name = prospect.contact_name
        prospect.delete()
        messages.success(request, f"Prospect deleted: {company_name} / {contact_name}.")
        return redirect(next_url)
    return render(
        request,
        "leadgen/confirm_delete.html",
        {
            "object": prospect,
            "title": "Delete prospect",
            "description": "This will permanently remove the prospect and its meeting/status history from the system.",
            "next_url": next_url,
        },
    )


@role_required(User.ROLE_SUPERVISOR)
def supervisor_move_prospect(request, prospect_id):
    prospect = get_object_or_404(Prospect.objects.select_related("assigned_to"), pk=prospect_id)
    fallback_url = reverse("supervisor_staff_prospect_list", args=[prospect.assigned_to_id])
    next_url = _safe_next_url(request, fallback_url)
    form = SupervisorProspectActionForm(
        request.POST or None,
        initial={
            "assigned_to": prospect.assigned_to,
            "supervisor_notes": prospect.supervisor_notes,
        },
    )
    if request.method == "POST" and form.is_valid():
        prospect.assigned_to = form.cleaned_data["assigned_to"]
        prospect.supervisor_notes = form.cleaned_data["supervisor_notes"]
        prospect.save(update_fields=["assigned_to", "supervisor_notes", "updated_at"])
        messages.success(request, "Prospect reassigned.")
        return redirect(next_url)
    return render(
        request,
        "leadgen/supervisor_move_prospect.html",
        {
            "prospect": prospect,
            "form": form,
            "next_url": next_url,
        },
    )


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
    settings_obj = SystemSetting.load()
    date_form = MeetingDateFilterForm(request.GET or None)
    meetings = Meeting.objects.select_related("prospect", "scheduled_by")
    selected_date = None
    if date_form.is_valid():
        selected_date = date_form.cleaned_data.get("meeting_date")
        if selected_date:
            start_dt, end_dt = _local_day_bounds(selected_date, settings_obj.default_timezone)
            meetings = meetings.filter(scheduled_for__range=(start_dt, end_dt))
    summary = {
        "total": meetings.count(),
        "scheduled": meetings.filter(status=Meeting.STATUS_SCHEDULED).count(),
        "happened": meetings.filter(status=Meeting.STATUS_HAPPENED).count(),
        "did_not_happen": meetings.filter(status=Meeting.STATUS_DID_NOT_HAPPEN).count(),
        "rescheduled": meetings.filter(status=Meeting.STATUS_RESCHEDULED).count(),
    }
    return render(
        request,
        "leadgen/supervisor_meeting_list.html",
        {
            "meetings": meetings,
            "date_form": date_form,
            "selected_date": selected_date,
            "summary": summary,
        },
    )


@role_required(User.ROLE_SUPERVISOR)
def supervisor_reminder_dashboard(request):
    settings_obj = SystemSetting.load()
    dashboard = build_reminder_dashboard(settings_obj.default_timezone)
    return render(request, "leadgen/supervisor_reminder_dashboard.html", {"dashboard": dashboard})


@role_required(User.ROLE_SUPERVISOR)
def update_meeting_status(request, meeting_id):
    meeting = get_object_or_404(Meeting.objects.select_related("prospect", "scheduled_by"), pk=meeting_id)
    form = MeetingStatusUpdateForm(request.POST or None, instance=meeting)
    if request.method == "POST" and form.is_valid():
        if form.cleaned_data["status"] == Meeting.STATUS_RESCHEDULED:
            reschedule_meeting(meeting, form.cleaned_data["rescheduled_for"], updated_by=request.user)
            messages.success(request, "Meeting rescheduled and a new invitation sent.")
        else:
            update_meeting_outcome(meeting, form.cleaned_data["status"], updated_by=request.user)
            messages.success(request, "Meeting status updated.")
        return redirect("supervisor_meeting_list")
    return render(request, "leadgen/update_meeting_status.html", {"meeting": meeting, "form": form})


@role_required(User.ROLE_SUPERVISOR)
def supervisor_meeting_delete(request, meeting_id):
    if request.method != "POST":
        raise Http404
    meeting = get_object_or_404(Meeting.objects.select_related("prospect", "scheduled_by"), pk=meeting_id)
    meeting_date = request.POST.get("meeting_date", "").strip()
    delete_meeting(meeting)
    messages.success(request, "Meeting deleted.")
    redirect_url = reverse("supervisor_meeting_list")
    if meeting_date:
        redirect_url = f"{redirect_url}?{urlencode({'meeting_date': meeting_date})}"
    return redirect(redirect_url)


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


@role_required(User.ROLE_SUPERVISOR)
def supervisor_daily_targets(request):
    settings_obj = SystemSetting.load()
    target_date = timezone.localtime(timezone.now(), ZoneInfo(settings_obj.default_timezone)).date() - timedelta(days=1)
    report = build_daily_target_report(target_date=target_date, tz_name=settings_obj.default_timezone)
    return render(request, "leadgen/supervisor_daily_targets.html", {"report": report})


@role_required(User.ROLE_STAFF)
def staff_dashboard(request):
    context = _build_staff_dashboard_context(request.user)
    context["is_supervisor_view"] = False
    return render(request, "leadgen/staff_dashboard.html", context)


@role_required(User.ROLE_STAFF)
def staff_prospect_list(request):
    status_filter = request.GET.get("view", "all")
    prospects = _filtered_staff_prospects_queryset(request.user, status_filter)
    prospect_rows, review_date = _build_staff_prospect_rows(prospects, status_filter)
    return render(
        request,
        "leadgen/staff_prospect_list.html",
        {
            "prospect_rows": prospect_rows,
            "dashboard_owner": request.user,
            "is_supervisor_view": False,
            "status_filter": status_filter,
            "review_date": review_date,
        },
    )


@role_required(User.ROLE_SUPERVISOR)
def supervisor_staff_prospect_list(request, user_id):
    staff_member = _get_staff_or_404(user_id)
    status_filter = request.GET.get("view", "all")
    prospects = _filtered_staff_prospects_queryset(
        staff_member,
        status_filter,
        include_hidden=status_filter == YESTERDAY_CALLS_FILTER,
    )
    prospect_rows, review_date = _build_staff_prospect_rows(prospects, status_filter)
    return render(
        request,
        "leadgen/staff_prospect_list.html",
        {
            "prospect_rows": prospect_rows,
            "dashboard_owner": staff_member,
            "is_supervisor_view": True,
            "status_filter": status_filter,
            "review_date": review_date,
        },
    )


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
    return render(
        request,
        "leadgen/prospect_form.html",
        {
            "form": form,
            "eyebrow": "Lead gen",
            "title": "Add prospect",
            "submit_label": "Submit for review",
        },
    )


def _get_staff_prospect(request, prospect_id):
    prospect = get_object_or_404(
        Prospect.objects.prefetch_related("status_updates", "meetings"),
        pk=prospect_id,
        assigned_to=request.user,
    )
    if prospect.workflow_status in STAFF_HIDDEN_WORKFLOWS:
        raise Http404("This prospect is not available for call updates.")
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
    meetings = Meeting.objects.filter(scheduled_by=request.user).select_related("prospect").prefetch_related("reminders")
    return render(request, "leadgen/staff_meeting_list.html", {"meetings": meetings})


def _staff_meeting_or_404(user, meeting_id):
    return get_object_or_404(
        Meeting.objects.select_related("prospect", "scheduled_by").prefetch_related("reminders"),
        pk=meeting_id,
        scheduled_by=user,
    )


@role_required(User.ROLE_STAFF)
def log_meeting_reminder(request, meeting_id):
    meeting = _staff_meeting_or_404(request.user, meeting_id)
    if meeting.status != Meeting.STATUS_SCHEDULED:
        raise Http404("Reminder logging is only available for scheduled meetings.")
    form = MeetingReminderLogForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        log_whatsapp_reminder(
            meeting=meeting,
            reminder_type=form.cleaned_data["reminder_type"],
            recipient_number=form.cleaned_data["recipient_number"],
            screenshot=form.cleaned_data["screenshot"],
            sent_by=request.user,
        )
        messages.success(request, "Reminder logged.")
        return redirect("staff_meeting_list")
    return render(
        request,
        "leadgen/meeting_reminder_form.html",
        {
            "meeting": meeting,
            "form": form,
        },
    )


def _is_supervisor_workspace(request):
    return getattr(request, "current_workspace", None) == "supervisor" and getattr(
        request, "has_supervisor_workspace_access", False
    )


def _is_effective_supervisor(request):
    return request.user.is_supervisor or _is_supervisor_workspace(request)


def _is_effective_sales_manager(request):
    return request.user.is_sales_manager and not _is_supervisor_workspace(request)


def _workspace_eyebrow(request):
    if getattr(request, "current_workspace", None) == "finance":
        return "Finance"
    if _is_effective_supervisor(request):
        return "Supervisor"
    return "Sales"


def _allow_sales_company_name_edits(request):
    return _is_effective_supervisor(request)


def _sales_pipeline_queryset_for_request(request):
    queryset = SalesConversation.objects.select_related("assigned_sales_manager", "source_meeting").prefetch_related(
        "contacts",
        "brands",
        "files",
    )
    if _is_effective_sales_manager(request):
        queryset = queryset.filter(assigned_sales_manager=request.user)
    return queryset


def _configure_sales_conversation_form(form, request):
    if _is_effective_sales_manager(request):
        form.fields["assigned_sales_manager"].queryset = User.objects.filter(pk=request.user.pk)
        form.fields["assigned_sales_manager"].empty_label = None
        if not form.is_bound and not form.instance.assigned_sales_manager_id:
            form.initial["assigned_sales_manager"] = request.user.pk
    return form


def _sales_conversation_for_request_or_404(request, conversation_id):
    queryset = _sales_pipeline_queryset_for_request(request)
    conversation = get_object_or_404(queryset, pk=conversation_id)
    if _is_effective_sales_manager(request) and conversation.assigned_sales_manager_id != request.user.pk:
        raise Http404("You do not have access to this sales conversation.")
    return conversation


def _uploaded_sales_files(request):
    return {
        "solution_files": request.FILES.getlist("solution_files"),
        "proposal_files": request.FILES.getlist("proposal_files"),
    }


def _contracts_queryset_for_request(request):
    queryset = ContractCollection.objects.select_related(
        "sales_manager",
        "source_sales_conversation",
    ).prefetch_related(
        "contacts",
        "files",
        "installments",
    )
    if _is_effective_sales_manager(request):
        queryset = queryset.filter(sales_manager=request.user)
    return queryset


def _configure_contract_form(form, request):
    if _is_effective_sales_manager(request):
        form.fields["sales_manager"].queryset = User.objects.filter(pk=request.user.pk)
        form.fields["sales_manager"].empty_label = None
        if not form.is_bound and not form.instance.sales_manager_id:
            form.initial["sales_manager"] = request.user.pk
    return form


def _contract_collection_for_request_or_404(request, contract_id):
    queryset = _contracts_queryset_for_request(request)
    contract_collection = get_object_or_404(queryset, pk=contract_id)
    if _is_effective_sales_manager(request) and contract_collection.sales_manager_id != request.user.pk:
        raise Http404("You do not have access to this contract.")
    return contract_collection


def _uploaded_contract_files(request):
    return {
        "contract_files": request.FILES.getlist("contract_files"),
    }


@roles_required(User.ROLE_SUPERVISOR, User.ROLE_SALES_MANAGER)
def sales_pipeline_dashboard(request):
    conversations = _sales_pipeline_queryset_for_request(request).filter(contract_signed=False)
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
        "workspace_eyebrow": _workspace_eyebrow(request),
        "active_count": conversations.count(),
        "signed_count": _sales_pipeline_queryset_for_request(request).filter(contract_signed=True).count(),
    }
    return render(request, "leadgen/sales_pipeline_dashboard.html", context)


@roles_required(User.ROLE_SUPERVISOR, User.ROLE_SALES_MANAGER)
def sales_conversation_create(request):
    form = SalesConversationForm(
        request.POST or None,
        request.FILES or None,
        allow_company_name_edits=_allow_sales_company_name_edits(request),
    )
    form = _configure_sales_conversation_form(form, request)
    if request.method == "POST" and form.is_valid():
        conversation = form.save(commit=False)
        if _is_effective_sales_manager(request):
            conversation.assigned_sales_manager = request.user
        conversation.created_by = request.user
        conversation.save()
        sync_sales_conversation_data(
            conversation,
            form.cleaned_data,
            _uploaded_sales_files(request),
            allow_company_name_edits=_allow_sales_company_name_edits(request),
        )
        if conversation.contract_signed:
            contract_collection = get_or_create_contract_collection_from_sales_conversation(conversation, created_by=request.user)
            messages.success(request, "Sales conversation created and moved into contracts and collections.")
            return redirect("contract_collection_update", contract_id=contract_collection.pk)
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
            "workspace_eyebrow": _workspace_eyebrow(request),
        },
    )


@roles_required(User.ROLE_SUPERVISOR, User.ROLE_SALES_MANAGER)
def sales_conversation_update(request, conversation_id):
    conversation = _sales_conversation_for_request_or_404(request, conversation_id)
    form = SalesConversationForm(
        request.POST or None,
        request.FILES or None,
        instance=conversation,
        allow_company_name_edits=_allow_sales_company_name_edits(request),
    )
    form = _configure_sales_conversation_form(form, request)
    if request.method == "POST" and form.is_valid():
        conversation = form.save(commit=False)
        if _is_effective_sales_manager(request):
            conversation.assigned_sales_manager = request.user
        conversation.save()
        sync_sales_conversation_data(
            conversation,
            form.cleaned_data,
            _uploaded_sales_files(request),
            allow_company_name_edits=_allow_sales_company_name_edits(request),
        )
        if conversation.contract_signed:
            contract_collection = get_or_create_contract_collection_from_sales_conversation(conversation, created_by=request.user)
            messages.success(request, "Sales conversation updated and moved into contracts and collections.")
            return redirect("contract_collection_update", contract_id=contract_collection.pk)
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
            "workspace_eyebrow": _workspace_eyebrow(request),
        },
    )


@roles_required(User.ROLE_SUPERVISOR, User.ROLE_SALES_MANAGER, User.ROLE_FINANCE_MANAGER)
def contracts_dashboard(request):
    contracts = _contracts_queryset_for_request(request).order_by("-updated_at")
    context = {
        "contracts": contracts,
        "workspace_eyebrow": _workspace_eyebrow(request),
        "can_send_invoice_alerts": _is_effective_supervisor(request),
        "can_add_contract": _is_effective_supervisor(request) or _is_effective_sales_manager(request),
        "active_count": contracts.count(),
        "pending_count": build_pending_collections(_contracts_queryset_for_request(request))["invoiced_pending"].count(),
    }
    return render(request, "leadgen/contracts_dashboard.html", context)


@roles_required(User.ROLE_SUPERVISOR, User.ROLE_SALES_MANAGER)
def contract_collection_create(request):
    form = ContractCollectionForm(
        request.POST or None,
        request.FILES or None,
        allow_locked_field_edits=_is_effective_supervisor(request),
    )
    form = _configure_contract_form(form, request)
    if request.method == "POST" and form.is_valid():
        contract_collection = form.save(commit=False)
        if _is_effective_sales_manager(request):
            contract_collection.sales_manager = request.user
        contract_collection.created_by = request.user
        contract_collection.save()
        sync_contract_collection_data(
            contract_collection,
            form.cleaned_data,
            _uploaded_contract_files(request),
            allow_locked_field_edits=_is_effective_supervisor(request),
        )
        messages.success(request, "Contract added to contracts and collections.")
        return redirect("contract_collection_update", contract_id=contract_collection.pk)
    return render(
        request,
        "leadgen/contract_collection_form.html",
        {
            "contract_collection": None,
            "terms_form": form,
            "finance_form": None,
            "existing_contract_files": [],
            "title": "Add contract",
            "can_edit_terms": True,
            "can_edit_finance": False,
            "workspace_eyebrow": _workspace_eyebrow(request),
        },
    )


@roles_required(User.ROLE_SUPERVISOR, User.ROLE_SALES_MANAGER, User.ROLE_FINANCE_MANAGER)
def contract_collection_update(request, contract_id):
    contract_collection = _contract_collection_for_request_or_404(request, contract_id)
    can_edit_terms = _is_effective_supervisor(request) or _is_effective_sales_manager(request)
    can_edit_finance = request.user.is_finance_manager
    terms_form = ContractCollectionForm(
        instance=contract_collection,
        allow_locked_field_edits=_is_effective_supervisor(request),
    )
    terms_form = _configure_contract_form(terms_form, request)
    finance_form = FinanceCollectionUpdateForm(contract_collection=contract_collection)

    if request.method == "POST" and can_edit_terms and request.POST.get("form_type") == "terms":
        terms_form = ContractCollectionForm(
            request.POST or None,
            request.FILES or None,
            instance=contract_collection,
            allow_locked_field_edits=_is_effective_supervisor(request),
        )
        terms_form = _configure_contract_form(terms_form, request)
        finance_form = FinanceCollectionUpdateForm(contract_collection=contract_collection)
        if terms_form.is_valid():
            updated_contract = terms_form.save(commit=False)
            if _is_effective_sales_manager(request):
                updated_contract.sales_manager = request.user
            updated_contract.save()
            sync_contract_collection_data(
                updated_contract,
                terms_form.cleaned_data,
                _uploaded_contract_files(request),
                allow_locked_field_edits=_is_effective_supervisor(request),
            )
            messages.success(request, "Contract details updated.")
            return redirect("contract_collection_update", contract_id=contract_collection.pk)

    if request.method == "POST" and can_edit_finance and request.POST.get("form_type") == "finance":
        finance_form = FinanceCollectionUpdateForm(request.POST or None, contract_collection=contract_collection)
        terms_form = ContractCollectionForm(
            instance=contract_collection,
            allow_locked_field_edits=_is_effective_supervisor(request),
        )
        terms_form = _configure_contract_form(terms_form, request)
        if finance_form.is_valid():
            sync_finance_collection_data(contract_collection, finance_form.cleaned_data)
            messages.success(request, "Collection updates saved.")
            return redirect("contract_collection_update", contract_id=contract_collection.pk)

    return render(
        request,
        "leadgen/contract_collection_form.html",
        {
            "contract_collection": contract_collection,
            "terms_form": terms_form,
            "finance_form": finance_form,
            "existing_contract_files": contract_collection.files.all(),
            "title": f"Contract {contract_collection.contract_collection_id}",
            "can_edit_terms": can_edit_terms,
            "can_edit_finance": can_edit_finance,
            "workspace_eyebrow": _workspace_eyebrow(request),
        },
    )


@roles_required(User.ROLE_SUPERVISOR, User.ROLE_SALES_MANAGER, User.ROLE_FINANCE_MANAGER)
def pending_collections_view(request):
    summary = build_pending_collections(_contracts_queryset_for_request(request))
    summary["workspace_eyebrow"] = _workspace_eyebrow(request)
    return render(request, "leadgen/pending_collections.html", summary)


@role_required(User.ROLE_SUPERVISOR)
def send_invoice_due_notifications_now(request):
    sent_count = send_due_invoice_notifications()
    messages.success(request, f"Invoice due notifications sent: {sent_count}")
    return redirect("contracts_dashboard")

# Create your views here.
