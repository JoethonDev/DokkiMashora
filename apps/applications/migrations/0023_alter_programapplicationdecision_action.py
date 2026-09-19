from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("applications", "0022_programapplicationemaillog_centralization"),
    ]

    operations = [
        migrations.AlterField(
            model_name="programapplicationdecision",
            name="action",
            field=models.CharField(
                choices=[
                    ("account_activated", "Account Activated"),
                    ("payment_pending", "Payment Pending"),
                    ("invited_to_entrance", "Invited to Entrance"),
                    ("entrance_submitted", "Entrance Submitted"),
                    ("request_interview", "Request Interview"),
                    ("accept", "Accepted"),
                    ("reject", "Rejected"),
                    ("revert", "Reverted"),
                    ("note", "Note"),
                    ("request_documents", "Documents Requested"),
                    ("submit_documents", "Documents Submitted"),
                    ("request_survey", "Survey Requested"),
                    ("submit_survey", "Survey Submitted"),
                    ("activate", "Activated"),
                    ("payment_waiver_revoked", "Payment waiver revoked"),
                ],
                max_length=32,
            ),
        ),
    ]
