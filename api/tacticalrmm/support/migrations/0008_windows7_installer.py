from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("support", "0007_unsigned_installers")]

    operations = [
        migrations.AlterField(
            model_name="supportinstaller",
            name="platform",
            field=models.CharField(
                choices=[
                    ("windows", "Windows"),
                    ("windows7", "Windows 7"),
                    ("macos", "Mac"),
                ],
                max_length=10,
                primary_key=True,
                serialize=False,
            ),
        ),
    ]
