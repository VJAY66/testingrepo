from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('discussions', '0043_live_debate_rooms'),
    ]

    operations = [
        migrations.AddField(
            model_name='debate',
            name='counter_topic',
            field=models.CharField(blank=True, default='', help_text='Alternative topic proposed by the challenged user', max_length=200),
        ),
        migrations.AddField(
            model_name='debate',
            name='counter_side',
            field=models.CharField(blank=True, default='', help_text='Side the challenger wants in the counter-proposal', max_length=10),
        ),
        migrations.AlterField(
            model_name='debate',
            name='status',
            field=models.CharField(
                choices=[
                    ('pending', 'Pending'),
                    ('countered', 'Counter Proposed'),
                    ('accepted', 'Accepted'),
                    ('rejected', 'Rejected'),
                    ('completed', 'Completed'),
                ],
                default='pending',
                max_length=20,
            ),
        ),
    ]
