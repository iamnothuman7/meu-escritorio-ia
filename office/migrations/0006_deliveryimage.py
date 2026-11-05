from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone


class Migration(migrations.Migration):
    dependencies = [("office", "0005_remove_owneraccesscode")]

    operations = [
        migrations.CreateModel(
            name="DeliveryImage",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("mode", models.CharField(choices=[("brand", "Arte da marca"), ("openai", "OpenAI API")], default="brand", max_length=12)),
                ("status", models.CharField(choices=[("queued", "Na fila"), ("running", "Gerando imagem"),
                                                     ("ready", "Imagem pronta"), ("failed", "Imagem indisponível")],
                                            db_index=True, default="queued", max_length=12)),
                ("requested_version", models.PositiveIntegerField()),
                ("requested_hash", models.CharField(max_length=64)),
                ("key_fingerprint", models.CharField(blank=True, max_length=64)),
                ("automatic", models.BooleanField(default=False)),
                ("sha256", models.CharField(blank=True, max_length=64)),
                ("data", models.BinaryField(null=True)),
                ("error", models.CharField(blank=True, max_length=300)),
                ("requested_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("started_at", models.DateTimeField(blank=True, null=True)),
                ("attempted_at", models.DateTimeField(blank=True, db_index=True, null=True)),
                ("finished_at", models.DateTimeField(blank=True, null=True)),
                ("delivery", models.OneToOneField(on_delete=django.db.models.deletion.CASCADE,
                                                  related_name="generated_image", to="office.delivery")),
            ],
        ),
    ]
