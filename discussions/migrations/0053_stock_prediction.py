import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('discussions', '0052_post_yes_label_no_label'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        # post_type on Post
        migrations.AddField(
            model_name='post',
            name='post_type',
            field=models.CharField(
                choices=[('discussion', 'Discussion'), ('stock_prediction', 'Stock Prediction')],
                db_index=True,
                default='discussion',
                max_length=20,
            ),
        ),
        # confidence_score on Comment
        migrations.AddField(
            model_name='comment',
            name='confidence_score',
            field=models.PositiveSmallIntegerField(
                blank=True,
                null=True,
                help_text='Confidence 1-10, used for stock predictions only',
            ),
        ),
        # StockPrediction model
        migrations.CreateModel(
            name='StockPrediction',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('stock_symbol', models.CharField(db_index=True, max_length=20)),
                ('stock_name', models.CharField(blank=True, default='', max_length=100)),
                ('target_price', models.DecimalField(decimal_places=2, max_digits=12)),
                ('target_date', models.DateField(db_index=True)),
                ('direction', models.CharField(
                    choices=[('above', 'Above (will exceed target)'), ('below', 'Below (will fall under target)')],
                    max_length=10,
                )),
                ('entry_price', models.DecimalField(
                    blank=True, decimal_places=2, max_digits=12, null=True,
                    help_text='Stock price when prediction was created',
                )),
                ('status', models.CharField(
                    choices=[('active', 'Active'), ('resolved', 'Resolved'), ('expired', 'Expired')],
                    db_index=True, default='active', max_length=20,
                )),
                ('outcome', models.CharField(
                    blank=True, default='',
                    choices=[('', 'Pending'), ('bullish_correct', 'Bullish Correct'), ('bearish_correct', 'Bearish Correct')],
                    max_length=20,
                )),
                ('resolved_price', models.DecimalField(blank=True, decimal_places=2, max_digits=12, null=True)),
                ('resolved_at', models.DateTimeField(blank=True, null=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('post', models.OneToOneField(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='stock_prediction',
                    to='discussions.post',
                )),
                ('resolved_by', models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='resolved_predictions',
                    to=settings.AUTH_USER_MODEL,
                )),
            ],
            options={'ordering': ['-created_at']},
        ),
        migrations.AddIndex(
            model_name='stockprediction',
            index=models.Index(fields=['stock_symbol', 'status'], name='stock_pred_symbol_status_idx'),
        ),
        migrations.AddIndex(
            model_name='stockprediction',
            index=models.Index(fields=['target_date', 'status'], name='stock_pred_date_status_idx'),
        ),
    ]
