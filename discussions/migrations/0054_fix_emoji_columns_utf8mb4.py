from django.db import migrations


class Migration(migrations.Migration):
    """
    Alter yes_label, no_label (Post) and stock_symbol, stock_name (StockPrediction)
    to utf8mb4 so emoji characters (🐂 🐻 etc.) can be stored.
    MySQL's 'utf8' charset only handles 3-byte sequences; emoji need 4 bytes.
    """

    dependencies = [
        ('discussions', '0053_stock_prediction'),
    ]

    operations = [
        migrations.RunSQL(
            sql=[
                "ALTER TABLE discussions_post MODIFY yes_label VARCHAR(50) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL DEFAULT 'Yes';",
                "ALTER TABLE discussions_post MODIFY no_label VARCHAR(50) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL DEFAULT 'No';",
                "ALTER TABLE discussions_stockprediction MODIFY stock_symbol VARCHAR(20) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL;",
                "ALTER TABLE discussions_stockprediction MODIFY stock_name VARCHAR(100) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL DEFAULT '';",
            ],
            reverse_sql=[
                "ALTER TABLE discussions_post MODIFY yes_label VARCHAR(50) CHARACTER SET utf8 COLLATE utf8_unicode_ci NOT NULL DEFAULT 'Yes';",
                "ALTER TABLE discussions_post MODIFY no_label VARCHAR(50) CHARACTER SET utf8 COLLATE utf8_unicode_ci NOT NULL DEFAULT 'No';",
                "ALTER TABLE discussions_stockprediction MODIFY stock_symbol VARCHAR(20) CHARACTER SET utf8 COLLATE utf8_unicode_ci NOT NULL;",
                "ALTER TABLE discussions_stockprediction MODIFY stock_name VARCHAR(100) CHARACTER SET utf8 COLLATE utf8_unicode_ci NOT NULL DEFAULT '';",
            ],
        ),
    ]
