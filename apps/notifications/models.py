"""
SMS logging and notification tracking models.
"""

from django.db import models
from django.utils import timezone
from apps.facilities.models import Facility

class SmsPurpose(models.TextChoices):
    SEARCH_RESULT = 'SEARCH_RESULT', 'Antivenom Search Result Dispatch'
    LOW_STOCK_ALERT = 'LOW_STOCK_ALERT', 'Low Stock Warning'
    STOCKOUT_ALERT = 'STOCKOUT_ALERT', 'Potential Stock-Out Alert'
    CONFIRMATION_REMINDER = 'CONFIRMATION_REMINDER', 'Daily Stock Confirmation Reminder'

class SmsStatus(models.TextChoices):
    DELIVERED = 'DELIVERED', 'Delivered'
    QUEUED = 'QUEUED', 'Queued'
    FAILED = 'FAILED', 'Failed'
    SIMULATED = 'SIMULATED', 'Simulated (Dev / Demo)'

class SmsLog(models.Model):
    recipient = models.CharField(max_length=32, db_index=True)
    message = models.TextField()
    facility = models.ForeignKey(
        Facility,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='sms_dispatches'
    )
    purpose = models.CharField(
        max_length=40,
        choices=SmsPurpose.choices,
        default=SmsPurpose.SEARCH_RESULT
    )
    status = models.CharField(
        max_length=20,
        choices=SmsStatus.choices,
        default=SmsStatus.SIMULATED
    )
    gateway_id = models.CharField(max_length=100, blank=True)
    timestamp = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        verbose_name = 'SMS Notification'
        verbose_name_plural = 'SMS Notifications'
        ordering = ['-timestamp']

    def __str__(self):
        return f"SMS to {self.recipient} [{self.get_purpose_display()}] at {self.timestamp.strftime('%H:%M')}"

