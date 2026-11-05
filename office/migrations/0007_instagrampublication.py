import django.db.models.deletion
import django.utils.timezone
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("office", "0006_deliveryimage")]

    operations = [
        migrations.CreateModel(
            name="InstagramPublication",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("approved_hash", models.CharField(max_length=64)),
                ("image_sha256", models.CharField(max_length=64)),
                ("account_id", models.CharField(max_length=32)),
                ("account_username", models.CharField(max_length=100)),
                ("caption", models.TextField()),
                ("status", models.CharField(choices=[
                    ("queued", "Na fila"), ("uploading", "Preparando no Instagram"),
                    ("waiting", "Aguardando processamento do Instagram"), ("checking", "Verificando imagem"),
                    ("publishing", "Publicando"), ("published", "Publicado"), ("failed", "Falhou"),
                    ("uncertain", "Resultado não confirmado"), ("cancelled", "Cancelado"),
                ], default="queued", max_length=16)),
                ("container_id", models.CharField(blank=True, max_length=32)),
                ("media_id", models.CharField(blank=True, max_length=32)),
                ("error", models.CharField(blank=True, max_length=300)),
                ("next_check_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("started_at", models.DateTimeField(blank=True, null=True)),
                ("finished_at", models.DateTimeField(blank=True, null=True)),
                ("approval", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to="office.approval")),
                ("delivery", models.OneToOneField(on_delete=django.db.models.deletion.CASCADE,
                                                  related_name="instagram_publication", to="office.delivery")),
                ("workspace", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to="office.workspace")),
            ],
            options={"indexes": [models.Index(fields=["status", "next_check_at"], name="ig_publication_due_idx")]},
        ),
    ]
