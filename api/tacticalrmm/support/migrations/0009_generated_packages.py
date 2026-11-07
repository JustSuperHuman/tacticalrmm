from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("support", "0008_windows7_installer")]

    operations = [
        migrations.AddField(
            model_name="supportinstaller",
            name="generated",
            field=models.BooleanField(default=False),
        ),
        migrations.CreateModel(
            name="SupportPayload",
            fields=[
                (
                    "sha256",
                    models.CharField(max_length=64, primary_key=True, serialize=False),
                ),
                ("size", models.PositiveIntegerField()),
                ("created_at", models.DateTimeField(auto_now_add=True)),
            ],
        ),
    ]
