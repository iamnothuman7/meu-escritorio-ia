from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [("office", "0004_owneraccesscode")]
    operations = [migrations.DeleteModel(name="OwnerAccessCode")]
