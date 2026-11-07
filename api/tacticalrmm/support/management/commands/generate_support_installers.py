from .publish_meshcentral_installers import Command as GenerateCommand


class Command(GenerateCommand):
    help = "Generate and publish the Windows MSI and universal Mac support installers."
