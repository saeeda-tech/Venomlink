from datetime import timedelta
from django.db import models
from django.conf import settings
from django.utils import timezone
from apps.facilities.models import Reporter


class UssdSessionState(models.Model):
    """
    Tracks active and completed USSD sessions to enforce:
    - Session expiration (default 180 seconds)
    - Caller phone number binding (prevents session hijacking)
    - Authenticated reporter state across turns
    - Replay protection on terminated sessions
    Note: Raw input text and PINs are never persisted in this table.
    """
    session_id = models.CharField(max_length=128, unique=True, db_index=True)
    phone_number = models.CharField(max_length=30)
    service_code = models.CharField(max_length=30, default='*384*20#')
    authenticated_reporter = models.ForeignKey(
        Reporter, on_delete=models.SET_NULL, null=True, blank=True, related_name='ussd_sessions'
    )
    is_authenticated = models.BooleanField(default=False)
    is_terminated = models.BooleanField(default=False)
    step_count = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    expires_at = models.DateTimeField()

    class Meta:
        ordering = ['-created_at']

    def save(self, *args, **kwargs):
        if not self.expires_at:
            timeout_sec = getattr(settings, 'VENOMLINK_CONFIG', {}).get('USSD_SESSION_TIMEOUT_SECONDS', 180)
            self.expires_at = timezone.now() + timedelta(seconds=timeout_sec)
        super().save(*args, **kwargs)

    @property
    def is_expired(self) -> bool:
        return timezone.now() > self.expires_at

    def refresh_expiry(self):
        timeout_sec = getattr(settings, 'VENOMLINK_CONFIG', {}).get('USSD_SESSION_TIMEOUT_SECONDS', 180)
        self.expires_at = timezone.now() + timedelta(seconds=timeout_sec)
