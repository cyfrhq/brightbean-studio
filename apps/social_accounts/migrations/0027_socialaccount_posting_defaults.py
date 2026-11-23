from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("social_accounts", "0026_alter_analyticsplatformconfig_platform_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="socialaccount",
            name="posting_defaults",
            field=models.JSONField(blank=True, default=dict),
        ),
    ]
