from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("leadgen", "0026_marketingemailcampaign_completed_at_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="cashflowsnapshot",
            name="opening_bank_balance",
            field=models.DecimalField(decimal_places=2, default=0, max_digits=14),
        ),
    ]
