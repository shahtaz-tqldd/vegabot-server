from django.db import migrations, models


def move_google_users_to_password(apps, schema_editor):
    """Google login is gone; former Google users sign in with a password
    (set through the password-reset flow)."""
    User = apps.get_model("accounts", "User")
    User.objects.filter(provider="google").update(provider="password")


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(
            move_google_users_to_password,
            migrations.RunPython.noop,
        ),
        migrations.RemoveField(model_name="user", name="firebase_uid"),
        migrations.RemoveField(model_name="user", name="firebase_id_token"),
        migrations.RemoveField(model_name="user", name="google_access_token"),
        migrations.AlterField(
            model_name="user",
            name="provider",
            field=models.CharField(
                choices=[("password", "Password")],
                default="password",
                max_length=20,
                verbose_name="Auth provider",
            ),
        ),
    ]
