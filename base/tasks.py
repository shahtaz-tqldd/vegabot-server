from celery import shared_task
from django.utils import timezone

from accounts.tasks import delete_expired_accounts

@shared_task(name="base.tasks.run_daily_maintenance")
def run_daily_maintenance():
    """Single daily coordinator for billing and platform maintenance.

    Add future daily jobs here. Weekly jobs run on Monday in the configured
    Django/Celery timezone.
    """
    now = timezone.now()
    result = {
        "run_at": now.isoformat(),
        "daily": {
            "expired_accounts_deleted": delete_expired_accounts(),
        },
        "weekly": None,
    }
    return result
