from decimal import Decimal

from django.core.validators import MinValueValidator
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("reports", "0067_archive_administrative_records_storage"),
    ]

    operations = [
        migrations.AlterField(
            model_name="platformsettings",
            name="archive_storage_block_price",
            field=models.DecimalField(
                decimal_places=2,
                default=149,
                help_text="سعر كل وحدة زيادة مساحة تخزين للأرشيف.",
                max_digits=10,
                validators=[MinValueValidator(0)],
                verbose_name="سعر باقة زيادة التخزين",
            ),
        ),
    ]
