from django.core.management.base import BaseCommand
from django.contrib.auth.models import User
from django.core.mail import EmailMultiAlternatives
from django.utils import timezone
from django.db.models import Count, Q

from discussions.models import Post, PostAction, Challenge
from users.models import Achievement, DEFAULT_NOTIFICATION_PREFS

import datetime


class Command(BaseCommand):
    help = "Send weekly digest emails to active PickASide users."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Print digest output instead of sending emails.",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        now = timezone.now()
        week_ago = now - datetime.timedelta(days=7)
        three_days_ago = now - datetime.timedelta(days=3)

        # 1. Active users who joined more than 3 days ago
        users = User.objects.filter(
            is_active=True,
            date_joined__lt=three_days_ago,
        ).select_related("profile")

        # 2. Top 5 posts of the week by like count (PostAction with action='like')
        top_posts = (
            Post.objects.filter(created_at__gte=week_ago)
            .annotate(like_count=Count("actions", filter=Q(actions__action="like")))
            .order_by("-like_count")[:5]
        )

        # 3. Active challenges
        active_challenges = list(Challenge.objects.filter(is_active=True))

        sent_count = 0
        skipped_count = 0

        for user in users:
            # Check notification preferences — key is 'weekly_digest', default email=True
            try:
                prefs = user.profile.notification_prefs or {}
            except Exception:
                prefs = {}

            weekly_digest_pref = prefs.get(
                "weekly_digest",
                DEFAULT_NOTIFICATION_PREFS.get("weekly_digest", {"email": True}),
            )
            email_enabled = weekly_digest_pref.get("email", True)

            if not email_enabled:
                skipped_count += 1
                self.stdout.write(
                    f"  Skipping {user.username} — digest emails disabled."
                )
                continue

            if not user.email:
                skipped_count += 1
                self.stdout.write(
                    f"  Skipping {user.username} — no email address."
                )
                continue

            # 4. User-specific data
            new_achievements = list(
                Achievement.objects.filter(user=user, awarded_at__gte=week_ago)
            )

            try:
                profile = user.profile
                streak = profile.streak_days
                reputation = profile.reputation_score
            except Exception:
                streak = 0
                reputation = 0

            subject = "Your PickASide Weekly Digest \U0001f525"
            text_body, html_body = self._build_digest(
                user, top_posts, active_challenges, new_achievements, streak, reputation
            )

            if dry_run:
                self.stdout.write(self.style.SUCCESS(f"\n{'=' * 60}"))
                self.stdout.write(f"TO: {user.email}  ({user.username})")
                self.stdout.write(f"SUBJECT: {subject}")
                self.stdout.write(text_body)
                sent_count += 1
            else:
                try:
                    msg = EmailMultiAlternatives(
                        subject=subject,
                        body=text_body,
                        to=[user.email],
                    )
                    msg.attach_alternative(html_body, "text/html")
                    msg.send()
                    self.stdout.write(f"  Sent digest to {user.username} <{user.email}>")
                    sent_count += 1
                except Exception as exc:
                    self.stdout.write(
                        self.style.ERROR(
                            f"  Failed to send to {user.username}: {exc}"
                        )
                    )

        mode = "[DRY RUN] " if dry_run else ""
        self.stdout.write(
            self.style.SUCCESS(
                f"\n{mode}Done. Sent: {sent_count}, Skipped: {skipped_count}."
            )
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _build_digest(
        self, user, top_posts, active_challenges, new_achievements, streak, reputation
    ):
        """Return (plain_text, html) tuple for the digest email."""
        first_name = user.first_name or user.username

        # ---- plain text ----
        lines = [
            f"Hi {first_name},",
            "",
            "Here's what's been happening on PickASide this week!",
            "",
            "=== TOP POSTS THIS WEEK ===",
        ]
        if top_posts:
            for i, post in enumerate(top_posts, 1):
                likes = getattr(post, "like_count", 0)
                lines.append(f"  {i}. {post.title}  ({likes} likes)")
        else:
            lines.append("  No posts yet this week.")

        lines += [
            "",
            "=== ACTIVE CHALLENGES ===",
        ]
        if active_challenges:
            for challenge in active_challenges:
                ends = challenge.ends_at.strftime("%b %d, %Y") if challenge.ends_at else "No end date"
                lines.append(f"  - {challenge.title}  (ends {ends})")
                if challenge.description:
                    lines.append(f"    {challenge.description[:120]}")
        else:
            lines.append("  No active challenges right now.")

        lines += [
            "",
            "=== YOUR NEW ACHIEVEMENTS THIS WEEK ===",
        ]
        if new_achievements:
            for ach in new_achievements:
                lines.append(f"  - {ach.code}")
        else:
            lines.append("  No new achievements this week — keep going!")

        lines += [
            "",
            f"Your streak: {streak} day(s)  |  Reputation: {reputation}",
            "",
            "Keep the conversation going!",
            "— The PickASide Team",
            "",
            "To unsubscribe from digest emails, update your notification preferences in your profile settings.",
        ]
        text_body = "\n".join(lines)

        # ---- HTML ----
        html_lines = [
            "<!DOCTYPE html>",
            '<html lang="en">',
            "<head>",
            '  <meta charset="UTF-8">',
            '  <meta name="viewport" content="width=device-width, initial-scale=1.0">',
            "  <title>PickASide Weekly Digest</title>",
            "  <style>",
            "    body { font-family: Arial, sans-serif; background: #f4f4f4; margin: 0; padding: 0; }",
            "    .container { max-width: 600px; margin: 30px auto; background: #ffffff; border-radius: 8px; overflow: hidden; box-shadow: 0 2px 8px rgba(0,0,0,0.1); }",
            "    .header { background: #4f46e5; color: #ffffff; padding: 24px 32px; }",
            "    .header h1 { margin: 0; font-size: 22px; }",
            "    .header p { margin: 4px 0 0; font-size: 14px; opacity: 0.85; }",
            "    .body { padding: 24px 32px; color: #333333; }",
            "    .section-title { font-size: 16px; font-weight: bold; color: #4f46e5; border-bottom: 2px solid #e5e7eb; padding-bottom: 6px; margin-top: 24px; margin-bottom: 12px; }",
            "    .post-item { margin-bottom: 8px; padding: 10px 12px; background: #f9fafb; border-left: 4px solid #4f46e5; border-radius: 4px; }",
            "    .post-item .title { font-weight: bold; }",
            "    .post-item .meta { font-size: 12px; color: #6b7280; }",
            "    .challenge-item { margin-bottom: 10px; padding: 10px 12px; background: #fef3c7; border-left: 4px solid #f59e0b; border-radius: 4px; }",
            "    .challenge-item .title { font-weight: bold; color: #92400e; }",
            "    .challenge-item .desc { font-size: 13px; color: #78350f; margin-top: 4px; }",
            "    .achievement-item { display: inline-block; margin: 4px; padding: 4px 12px; background: #d1fae5; color: #065f46; border-radius: 999px; font-size: 13px; font-weight: bold; }",
            "    .stats { margin-top: 20px; padding: 16px; background: #eff6ff; border-radius: 6px; display: flex; gap: 24px; }",
            "    .stat { text-align: center; }",
            "    .stat .value { font-size: 24px; font-weight: bold; color: #4f46e5; }",
            "    .stat .label { font-size: 12px; color: #6b7280; }",
            "    .footer { background: #f9fafb; padding: 16px 32px; font-size: 12px; color: #9ca3af; text-align: center; border-top: 1px solid #e5e7eb; }",
            "    .empty { color: #9ca3af; font-style: italic; }",
            "  </style>",
            "</head>",
            "<body>",
            '  <div class="container">',
            '    <div class="header">',
            f'      <h1>Your PickASide Weekly Digest \U0001f525</h1>',
            f'      <p>Hey {self._escape(first_name)}, here\'s what happened this week!</p>',
            "    </div>",
            '    <div class="body">',
            '      <div class="section-title">Top Posts This Week</div>',
        ]

        if top_posts:
            for i, post in enumerate(top_posts, 1):
                likes = getattr(post, "like_count", 0)
                html_lines.append(
                    f'      <div class="post-item">'
                    f'<div class="title">{i}. {self._escape(post.title)}</div>'
                    f'<div class="meta">{likes} like{"s" if likes != 1 else ""}</div>'
                    f"</div>"
                )
        else:
            html_lines.append('      <p class="empty">No posts this week yet — be the first!</p>')

        html_lines.append('      <div class="section-title">Active Challenges</div>')

        if active_challenges:
            for challenge in active_challenges:
                ends = challenge.ends_at.strftime("%b %d, %Y") if challenge.ends_at else "No end date"
                desc_snippet = (challenge.description[:120] + "...") if len(challenge.description) > 120 else challenge.description
                html_lines.append(
                    f'      <div class="challenge-item">'
                    f'<div class="title">{self._escape(challenge.title)}</div>'
                    f'<div class="meta" style="font-size:12px;color:#92400e;">Ends {self._escape(ends)}</div>'
                    + (f'<div class="desc">{self._escape(desc_snippet)}</div>' if desc_snippet else "")
                    + f"</div>"
                )
        else:
            html_lines.append('      <p class="empty">No active challenges right now.</p>')

        html_lines.append('      <div class="section-title">Your New Achievements</div>')

        if new_achievements:
            html_lines.append("      <div>")
            for ach in new_achievements:
                html_lines.append(f'        <span class="achievement-item">{self._escape(ach.code)}</span>')
            html_lines.append("      </div>")
        else:
            html_lines.append('      <p class="empty">No new achievements this week — keep going!</p>')

        html_lines += [
            '      <div class="section-title">Your Stats</div>',
            '      <div class="stats">',
            '        <div class="stat">',
            f'          <div class="value">{streak}</div>',
            '          <div class="label">Day Streak</div>',
            "        </div>",
            '        <div class="stat">',
            f'          <div class="value">{reputation}</div>',
            '          <div class="label">Reputation</div>',
            "        </div>",
            "      </div>",
            "    </div>",
            '    <div class="footer">',
            "      You're receiving this because weekly digest is enabled on your account.<br>",
            "      To unsubscribe, update your notification preferences in your profile settings.",
            "    </div>",
            "  </div>",
            "</body>",
            "</html>",
        ]

        html_body = "\n".join(html_lines)
        return text_body, html_body

    @staticmethod
    def _escape(text):
        """Minimal HTML escaping for safe email rendering."""
        return (
            str(text)
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;")
        )
