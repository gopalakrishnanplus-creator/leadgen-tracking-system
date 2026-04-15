from django.db import migrations, models


def assign_supervisor_access_levels(apps, schema_editor):
    SupervisorAccessEmail = apps.get_model("leadgen", "SupervisorAccessEmail")
    SupervisorAccessEmail.objects.filter(
        email="gopala.krishnan@inditech.co.in"
    ).update(access_level="system_admin")
    SupervisorAccessEmail.objects.exclude(
        email="gopala.krishnan@inditech.co.in"
    ).update(access_level="leadgen_supervisor")


class Migration(migrations.Migration):

    dependencies = [
        ("leadgen", "0010_supervisoraccessemail"),
    ]

    operations = [
        migrations.AddField(
            model_name="supervisoraccessemail",
            name="access_level",
            field=models.CharField(
                choices=[
                    ("system_admin", "System admin"),
                    ("leadgen_supervisor", "Lead gen supervisor"),
                ],
                default="leadgen_supervisor",
                max_length=32,
            ),
        ),
        migrations.RunPython(assign_supervisor_access_levels, migrations.RunPython.noop),
    ]
