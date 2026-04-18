from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("leadgen", "0011_supervisoraccessemail_access_level"),
    ]

    operations = [
        migrations.AlterField(
            model_name="meeting",
            name="status",
            field=models.CharField(
                choices=[
                    ("scheduled", "Scheduled"),
                    ("happened", "Meeting happened"),
                    ("did_not_happen", "Did not happen"),
                    ("rescheduled", "Rescheduled"),
                ],
                default="scheduled",
                max_length=20,
            ),
        ),
    ]
