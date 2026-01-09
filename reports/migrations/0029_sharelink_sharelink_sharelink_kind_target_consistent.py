"""Legacy migration placeholder.

This project ended up with two different migrations attempting to introduce
the `ShareLink` model. The canonical migration is
`0030_sharelink_sharelink_sharelink_kind_target_consistent`.

To keep the migration graph consistent across environments (and avoid
duplicate model/table creation), this migration is intentionally a no-op.
"""

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("reports", "0028_payment_requested_plan"),
    ]

    operations = []
