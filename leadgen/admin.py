from django.contrib import admin

from .models import CallImportBatch, CallLog, Meeting, Prospect, ProspectStatusUpdate, SystemSetting, User


@admin.register(User)
class UserAdmin(admin.ModelAdmin):
    list_display = ("name", "email", "role", "calling_number", "is_active")
    search_fields = ("name", "email", "calling_number")
    list_filter = ("role", "is_active")


@admin.register(Prospect)
class ProspectAdmin(admin.ModelAdmin):
    list_display = ("company_name", "contact_name", "assigned_to", "approval_status", "workflow_status")
    search_fields = ("company_name", "contact_name", "phone_number")
    list_filter = ("approval_status", "workflow_status", "latest_crm_status")


@admin.register(Meeting)
class MeetingAdmin(admin.ModelAdmin):
    list_display = ("prospect", "scheduled_by", "scheduled_for", "status")
    list_filter = ("status",)


admin.site.register(SystemSetting)
admin.site.register(CallImportBatch)
admin.site.register(CallLog)
admin.site.register(ProspectStatusUpdate)

# Register your models here.
