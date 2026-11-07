from django.core.management.base import BaseCommand, CommandError

from support import generation
from support.mesh import MeshError
from support.models import SupportInstaller


class Command(BaseCommand):
    help = (
        "Generate support installer packages. Windows always uses the universal MSI. "
        "Existing signed packages are kept."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--platform",
            action="append",
            choices=SupportInstaller.Platform.values,
            help="Publish only this platform; repeat to select several. Defaults to all.",
        )
        parser.add_argument(
            "--replace-signed",
            action="store_true",
            help="Replace signed apps too.",
        )

    def handle(self, *args, **options):
        try:
            results = generation.publish(
                replace_signed=options["replace_signed"], platforms=options["platform"]
            )
        except (
            generation.BuildError,
            generation.meshapps.Unavailable,
            MeshError,
        ) as exc:
            raise CommandError(str(exc)) from exc
        for platform, result in results.items():
            self.stdout.write(f"{platform}: {result}")
        self.stdout.write(
            self.style.WARNING(
                "Generated packages are not code-signed yet. Publish a signed generated release to replace them."
            )
        )
