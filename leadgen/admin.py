from django.contrib import admin

from .models import (
    CallImportBatch,
    CallLog,
    ContractCollection,
    ContractCollectionContact,
    ContractCollectionFile,
    ContractCollectionInstallment,
    Meeting,
    Prospect,
    ProspectStatusUpdate,
    SalesConversation,
    SalesConversationBrand,
    SalesConversationContact,
    SalesConversationFile,
    SystemSetting,
    User,
)


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
admin.site.register(SalesConversation)
admin.site.register(SalesConversationContact)
admin.site.register(SalesConversationBrand)
admin.site.register(SalesConversationFile)
admin.site.register(ContractCollection)
admin.site.register(ContractCollectionContact)
admin.site.register(ContractCollectionFile)
admin.site.register(ContractCollectionInstallment)

# Register your models here.
