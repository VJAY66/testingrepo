from django.db import migrations, models

class Migration(migrations.Migration):
    dependencies = [
        ('users', '0013_achievement'),
    ]
    operations = [
        migrations.AddField(
            model_name='collectionitem',
            name='tag',
            field=models.CharField(blank=True, default='', help_text='Optional label for this bookmark', max_length=40),
        ),
    ]
