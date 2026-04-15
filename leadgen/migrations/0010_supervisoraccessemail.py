from django.db import migrations, models


def seed_supervisor_access_emails(apps, schema_editor):
    SupervisorAccessEmail = apps.get_model("leadgen", "SupervisorAccessEmail")
    for email in [
        "bhavesh.kataria@inditech.co.in",
        "gopala.krishnan@inditech.co.in",
        "gkinchina@gmail.com",
        "leesaamit@gmail.com",
        "vamshi.alle@inditech.co.in",
    ]:
        SupervisorAccessEmail.objects.update_or_create(
            email=email,
            defaults={"is_active": True},
        )


class Migration(migrations.Migration):

    dependencies = [
        ("leadgen", "0009_contractcollectioninstallment_contract_summary_and_more"),
    ]

    operations = [
        migrations.CreateModel(
            name="SupervisorAccessEmail",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("email", models.EmailField(max_length=254, unique=True)),
                ("is_active", models.BooleanField(default=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "ordering": ["email"],
            },
        ),
        migrations.RunPython(seed_supervisor_access_emails, migrations.RunPython.noop),
    ]
