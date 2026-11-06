import secrets
import uuid
from datetime import timedelta

from django.conf import settings
from django.db import models
from django.utils import timezone
from django.utils.crypto import salted_hmac


def code_digest(code: str) -> str:
    return salted_hmac("support.access-code", code, algorithm="sha256").hexdigest()


def generate_code() -> str:
    return f"{secrets.randbelow(1_000_000_000):09d}"


class SupportAccessCode(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    digest = models.CharField(max_length=64, unique=True, editable=False)
    last_three = models.CharField(max_length=3, editable=False)
    label = models.CharField(max_length=100, blank=True)
    site = models.ForeignKey("clients.Site", on_delete=models.CASCADE)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(db_index=True)
    revoked_at = models.DateTimeField(null=True, blank=True)
    max_downloads = models.PositiveSmallIntegerField(default=5)
    download_count = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["-created_at"]

    @property
    def available(self):
        return (
            self.revoked_at is None
            and self.expires_at > timezone.now()
            and self.download_count < self.max_downloads
        )

    @property
    def status(self):
        if self.revoked_at:
            return "Revoked"
        if self.expires_at <= timezone.now():
            return "Expired"
        if self.download_count >= self.max_downloads:
            return "Used"
        return "Active"


class SupportBranding(models.Model):
    """One row (pk=1): the name, help contacts and logo shown on the support pages."""

    name = models.CharField(max_length=100, blank=True)
    contact_email = models.EmailField(blank=True)
    contact_phone = models.CharField(max_length=40, blank=True)
    # Stored in the database so branding needs no media storage or web server changes.
    logo = models.BinaryField(blank=True, default=b"", editable=False)
    logo_type = models.CharField(max_length=40, blank=True, editable=False)
    updated_at = models.DateTimeField(auto_now=True)

    @classmethod
    def load(cls, with_logo=False):
        rows = cls.objects.filter(pk=1)
        if not with_logo:
            rows = rows.defer("logo")
        return rows.first() or cls(pk=1)

    @property
    def has_logo(self):
        return bool(self.logo_type)


class SupportHandoff(models.Model):
    """A support computer being moved into monitoring with the full RMM agent."""

    device_id = models.CharField(max_length=128, db_index=True)
    device_name = models.CharField(max_length=255)
    site = models.ForeignKey("clients.Site", on_delete=models.CASCADE)
    agent_type = models.CharField(max_length=20)
    agent = models.ForeignKey(
        "agents.Agent", null=True, blank=True, on_delete=models.SET_NULL
    )
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL
    )
    requested_at = models.DateTimeField(default=timezone.now)
    # When the support app was removed after the agent checked in.
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-requested_at"]

    def find_agent(self):
        """The new RMM agent, matched by computer name in the chosen site."""
        if self.agent_id or self.requested_at is None:
            return self.agent
        from agents.models import Agent

        name = self.device_name.strip()
        agent = (
            Agent.objects.filter(
                site_id=self.site_id,
                created_time__gte=self.requested_at - timedelta(minutes=10),
            )
            .filter(
                models.Q(hostname__iexact=name)
                | models.Q(hostname__iexact=name.split(".")[0])
            )
            .order_by("-created_time")
            .first()
        )
        if agent:
            self.agent = agent
            self.save(update_fields=["agent"])
        return agent


class SupportInstaller(models.Model):
    class Platform(models.TextChoices):
        WINDOWS = "windows", "Windows"
        MACOS = "macos", "Mac"

    platform = models.CharField(
        max_length=10, choices=Platform.choices, primary_key=True
    )
    version = models.CharField(max_length=50)
    sha256 = models.CharField(max_length=64)
    size = models.PositiveIntegerField()
    signer = models.CharField(max_length=255)
    published_at = models.DateTimeField(auto_now=True)

    @property
    def filename(self):
        suffix = "exe" if self.platform == self.Platform.WINDOWS else "pkg"
        return f"TacticalSupport-{self.platform}.{suffix}"

    @property
    def storage_name(self):
        suffix = "exe" if self.platform == self.Platform.WINDOWS else "pkg"
        return f"{self.sha256}.{suffix}"
