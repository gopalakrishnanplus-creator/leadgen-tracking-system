from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("leadgen", "0028_contractcollection_contract_status"),
    ]

    operations = [
        migrations.AlterField(
            model_name="salesconversation",
            name="conversation_status",
            field=models.CharField(
                choices=[
                    ("engaged", "Engaged"),
                    ("not_engaged", "Not engaged"),
                    ("inbound_inquiries", "Inbound inquiries"),
                    ("to_be_revived", "To be revived"),
                    ("deeply_engaged", "Deeply engaged"),
                    ("in_negotiations", "In negotiations"),
                    ("in_contracting", "In contracting"),
                ],
                default="engaged",
                max_length=32,
            ),
        ),
    ]
