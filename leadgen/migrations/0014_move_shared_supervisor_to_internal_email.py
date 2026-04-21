from django.db import migrations


SHARED_SUPERVISOR_USER_EMAIL = "leadgen-supervisor@workspace.internal"


def move_shared_supervisor_to_internal_email(apps, schema_editor):
    User = apps.get_model("leadgen", "User")
    supervisor = User.objects.filter(role="supervisor").order_by("pk").first()
    if supervisor is None:
        return

    target_email = SHARED_SUPERVISOR_USER_EMAIL
    conflict = User.objects.exclude(pk=supervisor.pk).filter(email=target_email).exists()
    if conflict:
        target_email = f"leadgen-supervisor-{supervisor.pk}@workspace.internal"

    if supervisor.email == target_email and supervisor.username == target_email:
        return

    supervisor.email = target_email
    supervisor.username = target_email
    supervisor.save(update_fields=["email", "username"])


class Migration(migrations.Migration):

    dependencies = [
        ("leadgen", "0013_backfill_sales_pipeline_for_happened_meetings"),
    ]

    operations = [
        migrations.RunPython(
            move_shared_supervisor_to_internal_email,
            migrations.RunPython.noop,
        ),
    ]
