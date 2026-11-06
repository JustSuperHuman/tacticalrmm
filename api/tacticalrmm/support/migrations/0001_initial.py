import uuid

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    initial = True
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("clients", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="SupportInstaller",
            fields=[
                (
                    "platform",
                    models.CharField(
                        choices=[("windows", "Windows"), ("macos", "Mac")],
                        max_length=10,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                ("version", models.CharField(max_length=50)),
                ("sha256", models.CharField(max_length=64)),
                ("size", models.PositiveIntegerField()),
                ("signer", models.CharField(max_length=255)),
                ("published_at", models.DateTimeField(auto_now=True)),
            ],
        ),
        migrations.CreateModel(
            name="SupportAccessCode",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                (
                    "digest",
                    models.CharField(editable=False, max_length=64, unique=True),
                ),
                ("last_three", models.CharField(editable=False, max_length=3)),
                ("label", models.CharField(blank=True, max_length=100)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("expires_at", models.DateTimeField(db_index=True)),
                ("revoked_at", models.DateTimeField(blank=True, null=True)),
                ("max_downloads", models.PositiveSmallIntegerField(default=5)),
                ("download_count", models.PositiveSmallIntegerField(default=0)),
                (
                    "created_by",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "site",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE, to="clients.site"
                    ),
                ),
            ],
            options={"ordering": ["-created_at"]},
        ),
    ]
