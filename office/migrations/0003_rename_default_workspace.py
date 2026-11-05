from django.db import migrations


def rename_default_workspace(apps, schema_editor):
    Workspace = apps.get_model("office", "Workspace")
    Workspace.objects.using(schema_editor.connection.alias).filter(
        name="Nabi Office", segment="", description="", logo="", brand_color="#7C3AED",
    ).update(name="Nabio Office")


class Migration(migrations.Migration):
    dependencies = [("office", "0002_alter_workspace_brand_color")]
    operations = [migrations.RunPython(rename_default_workspace, migrations.RunPython.noop)]
