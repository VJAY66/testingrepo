from django.core.management.base import BaseCommand
from django.utils import timezone
from discussions.models import StockPrediction
from frontend.views import _fetch_live_price


class Command(BaseCommand):
    help = 'Refresh live prices for all active stock predictions (run every 6 hours via cron)'

    def handle(self, *args, **options):
        active = StockPrediction.objects.filter(status=StockPrediction.STATUS_ACTIVE).select_related('post')
        total = active.count()
        updated = 0
        failed = 0

        self.stdout.write(f"Updating prices for {total} active prediction(s)…")

        for sp in active:
            price = _fetch_live_price(sp.stock_symbol)
            if price:
                sp.live_price = price
                sp.price_updated_at = timezone.now()
                sp.save(update_fields=['live_price', 'price_updated_at'])
                updated += 1
            else:
                failed += 1
                self.stdout.write(
                    self.style.WARNING(f"  Could not fetch price for {sp.stock_symbol} (id={sp.post_id})")
                )

        self.stdout.write(self.style.SUCCESS(
            f"Done — {updated} updated, {failed} failed out of {total} total."
        ))
