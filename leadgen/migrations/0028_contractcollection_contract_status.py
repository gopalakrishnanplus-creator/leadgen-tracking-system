from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("leadgen", "0027_cashflowsnapshot_opening_bank_balance"),
    ]

    operations = [
        migrations.AddField(
            model_name="contractcollection",
            name="contract_status",
            field=models.CharField(
                choices=[("signed", "Signed"), ("projected", "Projected")],
                default="signed",
                max_length=16,
            ),
        ),
    ]
