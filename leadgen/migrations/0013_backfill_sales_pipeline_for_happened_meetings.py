from django.db import migrations


def backfill_sales_pipeline_for_happened_meetings(apps, schema_editor):
    User = apps.get_model("leadgen", "User")
    Meeting = apps.get_model("leadgen", "Meeting")
    Prospect = apps.get_model("leadgen", "Prospect")
    SalesConversation = apps.get_model("leadgen", "SalesConversation")
    SalesConversationContact = apps.get_model("leadgen", "SalesConversationContact")

    manager_ids = list(
        User.objects.filter(role="sales_manager", is_active=True)
        .order_by("name", "email")
        .values_list("pk", flat=True)[:2]
    )
    default_sales_manager_id = manager_ids[0] if len(manager_ids) == 1 else None

    meetings = (
        Meeting.objects.filter(status="happened", sales_conversation__isnull=True)
        .select_related("prospect")
        .order_by("pk")
    )

    for meeting in meetings.iterator():
        prospect = getattr(meeting, "prospect", None) or Prospect.objects.get(pk=meeting.prospect_id)
        sales_conversation = SalesConversation.objects.create(
            source_meeting_id=meeting.pk,
            company_name=prospect.company_name,
            assigned_sales_manager_id=default_sales_manager_id,
            created_by_id=meeting.scheduled_by_id,
        )
        SalesConversationContact.objects.create(
            sales_conversation_id=sales_conversation.pk,
            position=1,
            name=prospect.contact_name,
            email=meeting.prospect_email or prospect.prospect_email or "",
            whatsapp_number="",
        )


class Migration(migrations.Migration):

    dependencies = [
        ("leadgen", "0012_alter_meeting_status"),
    ]

    operations = [
        migrations.RunPython(
            backfill_sales_pipeline_for_happened_meetings,
            migrations.RunPython.noop,
        ),
    ]
