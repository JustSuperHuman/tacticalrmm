from django.core.management.base import BaseCommand, CommandError

from support import meshapps
from support.mesh import MeshError


class Command(BaseCommand):
    help = (
        "Publish the support apps straight from MeshCentral, unsigned, until signed "
        "ones are ready. Signed apps already published are kept."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--replace-signed",
            action="store_true",
            help="Replace signed apps too.",
        )

    def handle(self, *args, **options):
        try:
            results = meshapps.publish(replace_signed=options["replace_signed"])
        except (meshapps.Unavailable, MeshError) as exc:
            raise CommandError(str(exc)) from exc
        for platform, result in results.items():
            self.stdout.write(f"{platform}: {result}")
        self.stdout.write(
            self.style.WARNING(
                "These apps aren't code-signed. Windows and macOS warn people before "
                "they run. Publish signed apps to replace them."
            )
        )
