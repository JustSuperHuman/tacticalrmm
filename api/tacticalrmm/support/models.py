import secrets
import uuid
from datetime import timedelta

from django.conf import settings
from django.db import models
from django.utils import timezone
from django.utils.crypto import salted_hmac

# After this long without a check-in, the page stops waiting and says so.
HANDOFF_WAIT = timedelta(minutes=20)


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
    # Where the support app was last downloaded from, to match the computer that
    # connects next from the same address to this code's session.
    downloaded_from = models.GenericIPAddressField(null=True, blank=True)
    downloaded_at = models.DateTimeField(null=True, blank=True)

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
    """One row (pk=1): the name, help contacts and logo shown on the support pages,
    and the console's own settings."""

    name = models.CharField(max_length=100, blank=True)
    contact_email = models.EmailField(blank=True)
    contact_phone = models.CharField(max_length=40, blank=True)
    # Stored in the database so branding needs no media storage or web server changes.
    logo = models.BinaryField(blank=True, default=b"", editable=False)
    logo_type = models.CharField(max_length=40, blank=True, editable=False)
    # Small pictures of each connected computer's screen in the session list.
    screen_previews = models.BooleanField(default=True)
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

    @property
    def stalled(self):
        """No agent has checked in long after the install was sent."""
        return not self.agent_id and timezone.now() - self.requested_at > HANDOFF_WAIT

    def find_agent(self):
        """The new RMM agent, matched by computer name in the chosen site."""
        if self.agent_id or self.requested_at is None:
            return self.agent
        from agents.models import Agent

        # "Danas-MacBook.local" and "DANAS-MACBOOK" are the same computer.
        short = self.device_name.strip().split(".")[0]
        agent = (
            Agent.objects.filter(
                site_id=self.site_id,
                created_time__gte=self.requested_at - timedelta(minutes=10),
            )
            .filter(
                models.Q(hostname__iexact=short)
                | models.Q(hostname__istartswith=f"{short}.")
            )
            .exclude(supporthandoff__isnull=False)
            .order_by("-created_time")
            .first()
        )
        if agent:
            self.agent = agent
            self.save(update_fields=["agent"])
        return agent


class SupportCommand(models.Model):
    """A saved command in the toolbox, ready to run on support computers."""

    class Shell(models.IntegerChoices):
        # MeshCentral's runcommands types.
        POWERSHELL = 2, "PowerShell"
        CMD = 1, "Command Prompt"
        SHELL = 3, "Mac shell"

    name = models.CharField(max_length=100)
    shell = models.PositiveSmallIntegerField(choices=Shell.choices)
    command = models.TextField()
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name

    @property
    def windows(self):
        return self.shell != self.Shell.SHELL


class SupportSession(models.Model):
    """A support computer as technicians know it: its name, the code it came from,
    and its notes and command history."""

    device_id = models.CharField(max_length=128, unique=True)
    name = models.CharField(max_length=100, blank=True)
    code = models.ForeignKey(
        SupportAccessCode,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="sessions",
    )
    created_at = models.DateTimeField(auto_now_add=True)


class SupportNote(models.Model):
    session = models.ForeignKey(
        SupportSession, on_delete=models.CASCADE, related_name="notes"
    )
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL
    )
    text = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]


class SupportRun(models.Model):
    """A command run on a support computer, with what it printed."""

    session = models.ForeignKey(
        SupportSession, on_delete=models.CASCADE, related_name="runs"
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL
    )
    shell = models.PositiveSmallIntegerField()
    command = models.TextField()
    output = models.TextField(blank=True)
    # The support app didn't answer in time.
    no_reply = models.BooleanField(default=False)
    ran_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-ran_at"]


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
