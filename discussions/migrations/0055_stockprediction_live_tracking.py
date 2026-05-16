from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('discussions', '0054_fix_emoji_columns_utf8mb4'),
    ]

    operations = [
        # Extend direction max_length to accommodate 'up'/'down' (4 chars) — existing max is 10, fine
        # Add new fields
        migrations.AddField(
            model_name='stockprediction',
            name='predicted_change_pct',
            field=models.DecimalField(blank=True, decimal_places=2, help_text='Predicted % change from entry price', max_digits=8, null=True),
        ),
        migrations.AddField(
            model_name='stockprediction',
            name='live_price',
            field=models.DecimalField(blank=True, decimal_places=6, help_text='Most recently fetched live price', max_digits=14, null=True),
        ),
        migrations.AddField(
            model_name='stockprediction',
            name='price_updated_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='stockprediction',
            name='points_earned',
            field=models.DecimalField(blank=True, decimal_places=2, max_digits=6, null=True),
        ),
        # entry_price and resolved_price need more decimal places for crypto
        migrations.RunSQL(
            sql=[
                "ALTER TABLE discussions_stockprediction MODIFY entry_price DECIMAL(14,6) NULL;",
                "ALTER TABLE discussions_stockprediction MODIFY resolved_price DECIMAL(14,6) NULL;",
                "ALTER TABLE discussions_stockprediction MODIFY target_price DECIMAL(12,2) NOT NULL DEFAULT 0;",
            ],
            reverse_sql=[
                "ALTER TABLE discussions_stockprediction MODIFY entry_price DECIMAL(12,2) NULL;",
                "ALTER TABLE discussions_stockprediction MODIFY resolved_price DECIMAL(12,2) NULL;",
            ],
        ),
        # Make target_price default to 0 (was required, now optional for %-based predictions)
        migrations.AlterField(
            model_name='stockprediction',
            name='target_price',
            field=models.DecimalField(decimal_places=2, default=0, max_digits=12),
        ),
    ]
