from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('discussions', '0055_stockprediction_live_tracking'),
    ]

    operations = [
        migrations.AddField(
            model_name='stockprediction',
            name='currency',
            field=models.CharField(
                choices=[
                    ('USD', '$ USD — US Dollar'),
                    ('INR', '₹ INR — Indian Rupee'),
                    ('EUR', '€ EUR — Euro'),
                    ('GBP', '£ GBP — British Pound'),
                    ('JPY', '¥ JPY — Japanese Yen'),
                    ('CNY', '¥ CNY — Chinese Yuan'),
                    ('AUD', 'A$ AUD — Australian Dollar'),
                    ('CAD', 'C$ CAD — Canadian Dollar'),
                    ('SGD', 'S$ SGD — Singapore Dollar'),
                    ('HKD', 'HK$ HKD — Hong Kong Dollar'),
                    ('BRL', 'R$ BRL — Brazilian Real'),
                    ('KRW', '₩ KRW — South Korean Won'),
                    ('TRY', '₺ TRY — Turkish Lira'),
                    ('MXN', 'MX$ MXN — Mexican Peso'),
                    ('BTC', '₿ BTC — Bitcoin'),
                    ('ETH', 'Ξ ETH — Ethereum'),
                ],
                default='USD',
                max_length=6,
            ),
        ),
    ]
