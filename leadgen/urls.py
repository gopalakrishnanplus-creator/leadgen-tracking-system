from django.urls import path

from . import views


urlpatterns = [
    path("", views.home, name="home"),
    path("health/", views.healthcheck, name="healthcheck"),
    path("login/", views.login_page, name="login"),
    path("logout/", views.logout_view, name="logout"),
    path("supervisor/", views.supervisor_dashboard, name="supervisor_dashboard"),
    path("supervisor/staff/", views.staff_list, name="staff_list"),
    path("supervisor/staff/add/", views.staff_create, name="staff_create"),
    path("supervisor/staff/<int:user_id>/edit/", views.staff_update, name="staff_update"),
    path("supervisor/staff/<int:user_id>/delete/", views.staff_delete, name="staff_delete"),
    path("supervisor/sales-managers/", views.sales_manager_list, name="sales_manager_list"),
    path("supervisor/sales-managers/add/", views.sales_manager_create, name="sales_manager_create"),
    path("supervisor/sales-managers/<int:user_id>/edit/", views.sales_manager_update, name="sales_manager_update"),
    path("supervisor/sales-managers/<int:user_id>/delete/", views.sales_manager_delete, name="sales_manager_delete"),
    path("supervisor/prospects/review/", views.supervisor_prospect_review, name="supervisor_prospect_review"),
    path("supervisor/prospects/<int:prospect_id>/review/", views.review_prospect, name="review_prospect"),
    path("supervisor/imports/", views.import_batch_create, name="import_batch_create"),
    path("supervisor/meetings/", views.supervisor_meeting_list, name="supervisor_meeting_list"),
    path("supervisor/meetings/<int:meeting_id>/status/", views.update_meeting_status, name="update_meeting_status"),
    path("supervisor/settings/", views.system_settings_view, name="system_settings"),
    path("supervisor/reports/", views.supervisor_reports, name="supervisor_reports"),
    path("sales/", views.sales_pipeline_dashboard, name="sales_pipeline_dashboard"),
    path("sales/add/", views.sales_conversation_create, name="sales_conversation_create"),
    path("sales/<int:conversation_id>/", views.sales_conversation_update, name="sales_conversation_update"),
    path("staff/dashboard/", views.staff_dashboard, name="staff_dashboard"),
    path("staff/prospects/", views.staff_prospect_list, name="staff_prospect_list"),
    path("staff/prospects/add/", views.staff_prospect_create, name="staff_prospect_create"),
    path("staff/prospects/<int:prospect_id>/update-call/", views.update_call_outcome, name="update_call_outcome"),
    path("staff/meetings/", views.staff_meeting_list, name="staff_meeting_list"),
]
