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
    ContractCollectionForm,
    FinanceCollectionUpdateForm,
    FinanceManagerCreateForm,
    FinanceManagerUpdateForm,
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
    ContractCollection,
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
    build_pending_collections,
    build_supervisor_report,
    database_healthcheck,
    get_or_create_contract_collection_from_sales_conversation,
    import_exotel_report,
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


@login_required
def home(request):
    if request.user.is_supervisor:
        return redirect("supervisor_dashboard")
    if request.user.is_sales_manager:
        return redirect("sales_pipeline_dashboard")
    if request.user.is_finance_manager:
        return redirect("contracts_dashboard")
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
        "finance_manager_count": User.objects.filter(role=User.ROLE_FINANCE_MANAGER, is_active=True).count(),
        "active_sales_pipeline_count": SalesConversation.objects.filter(contract_signed=False).count(),
        "active_contract_count": ContractCollection.objects.count(),
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


def _build_staff_dashboard_context(staff_user):
    prospects = Prospect.objects.filter(assigned_to=staff_user)
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
def finance_manager_list(request):
    finance_managers = User.objects.filter(role=User.ROLE_FINANCE_MANAGER).order_by("name", "email")
    return render(request, "leadgen/finance_manager_list.html", {"finance_managers": finance_managers})


@role_required(User.ROLE_SUPERVISOR)
def finance_manager_create(request):
    form = FinanceManagerCreateForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Finance manager account created.")
        return redirect("finance_manager_list")
    return render(request, "leadgen/finance_manager_form.html", {"form": form, "title": "Add finance manager"})


@role_required(User.ROLE_SUPERVISOR)
def finance_manager_update(request, user_id):
    finance_manager = _get_finance_manager_or_404(user_id)
    form = FinanceManagerUpdateForm(request.POST or None, instance=finance_manager)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Finance manager updated.")
        return redirect("finance_manager_list")
    return render(request, "leadgen/finance_manager_form.html", {"form": form, "title": "Edit finance manager"})


@role_required(User.ROLE_SUPERVISOR)
def finance_manager_delete(request, user_id):
    finance_manager = _get_finance_manager_or_404(user_id)
    if request.method == "POST":
        finance_manager.is_active = False
        finance_manager.save(update_fields=["is_active"])
        messages.success(request, "Finance manager deactivated.")
        return redirect("finance_manager_list")
    return render(request, "leadgen/confirm_delete.html", {"object": finance_manager, "title": "Deactivate finance manager"})


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
    context = _build_staff_dashboard_context(request.user)
    context["is_supervisor_view"] = False
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


def _contracts_queryset_for_user(user):
    queryset = ContractCollection.objects.select_related(
        "sales_manager",
        "source_sales_conversation",
    ).prefetch_related(
        "contacts",
        "files",
        "installments",
    )
    if user.is_sales_manager:
        queryset = queryset.filter(sales_manager=user)
    return queryset


def _configure_contract_form(form, user):
    if user.is_sales_manager:
        form.fields["sales_manager"].queryset = User.objects.filter(pk=user.pk)
        form.fields["sales_manager"].empty_label = None
        if not form.is_bound and not form.instance.sales_manager_id:
            form.initial["sales_manager"] = user.pk
    return form


def _contract_collection_for_user_or_404(user, contract_id):
    queryset = _contracts_queryset_for_user(user)
    contract_collection = get_object_or_404(queryset, pk=contract_id)
    if user.is_sales_manager and contract_collection.sales_manager_id != user.pk:
        raise Http404("You do not have access to this contract.")
    return contract_collection


def _uploaded_contract_files(request):
    return {
        "contract_files": request.FILES.getlist("contract_files"),
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
        },
    )


@roles_required(User.ROLE_SUPERVISOR, User.ROLE_SALES_MANAGER, User.ROLE_FINANCE_MANAGER)
def contracts_dashboard(request):
    contracts = _contracts_queryset_for_user(request.user).order_by("-updated_at")
    context = {
        "contracts": contracts,
        "active_count": contracts.count(),
        "pending_count": build_pending_collections(_contracts_queryset_for_user(request.user))["invoiced_pending"].count(),
    }
    return render(request, "leadgen/contracts_dashboard.html", context)


@roles_required(User.ROLE_SUPERVISOR, User.ROLE_SALES_MANAGER)
def contract_collection_create(request):
    form = ContractCollectionForm(request.POST or None, request.FILES or None)
    form = _configure_contract_form(form, request.user)
    if request.method == "POST" and form.is_valid():
        contract_collection = form.save(commit=False)
        if request.user.is_sales_manager:
            contract_collection.sales_manager = request.user
        contract_collection.created_by = request.user
        contract_collection.save()
        sync_contract_collection_data(contract_collection, form.cleaned_data, _uploaded_contract_files(request))
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
        },
    )


@roles_required(User.ROLE_SUPERVISOR, User.ROLE_SALES_MANAGER, User.ROLE_FINANCE_MANAGER)
def contract_collection_update(request, contract_id):
    contract_collection = _contract_collection_for_user_or_404(request.user, contract_id)
    can_edit_terms = request.user.is_supervisor or request.user.is_sales_manager
    can_edit_finance = request.user.is_finance_manager
    terms_form = ContractCollectionForm(instance=contract_collection)
    terms_form = _configure_contract_form(terms_form, request.user)
    finance_form = FinanceCollectionUpdateForm(contract_collection=contract_collection)

    if request.method == "POST" and can_edit_terms and request.POST.get("form_type") == "terms":
        terms_form = ContractCollectionForm(request.POST or None, request.FILES or None, instance=contract_collection)
        terms_form = _configure_contract_form(terms_form, request.user)
        finance_form = FinanceCollectionUpdateForm(contract_collection=contract_collection)
        if terms_form.is_valid():
            updated_contract = terms_form.save(commit=False)
            if request.user.is_sales_manager:
                updated_contract.sales_manager = request.user
            updated_contract.save()
            sync_contract_collection_data(updated_contract, terms_form.cleaned_data, _uploaded_contract_files(request))
            messages.success(request, "Contract details updated.")
            return redirect("contract_collection_update", contract_id=contract_collection.pk)

    if request.method == "POST" and can_edit_finance and request.POST.get("form_type") == "finance":
        finance_form = FinanceCollectionUpdateForm(request.POST or None, contract_collection=contract_collection)
        terms_form = ContractCollectionForm(instance=contract_collection)
        terms_form = _configure_contract_form(terms_form, request.user)
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
        },
    )


@roles_required(User.ROLE_SUPERVISOR, User.ROLE_SALES_MANAGER, User.ROLE_FINANCE_MANAGER)
def pending_collections_view(request):
    summary = build_pending_collections(_contracts_queryset_for_user(request.user))
    return render(request, "leadgen/pending_collections.html", summary)


@role_required(User.ROLE_SUPERVISOR)
def send_invoice_due_notifications_now(request):
    sent_count = send_due_invoice_notifications()
    messages.success(request, f"Invoice due notifications sent: {sent_count}")
    return redirect("contracts_dashboard")

# Create your views here.
