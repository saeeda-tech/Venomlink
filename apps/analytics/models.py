"""
Alerts and Stock Intelligence models for VenomLink.
"""

from django.db import models
from django.utils import timezone
from apps.facilities.models import Facility

class AlertType(models.TextChoices):
    POTENTIAL_STOCKOUT = 'POTENTIAL_STOCKOUT', 'Potential Stock-Out'
    LOW_STOCK = 'LOW_STOCK', 'Low Stock'
    STALE_DATA = 'STALE_DATA', 'Stale Stock Data'
    EXPIRY_RISK = 'EXPIRY_RISK', 'Batch Expiry Risk'

class AlertSeverity(models.TextChoices):
    CRITICAL = 'CRITICAL', 'Critical'
    WARNING = 'WARNING', 'Warning'
    INFO = 'INFO', 'Informational'

class AlertStatus(models.TextChoices):
    ACTIVE = 'ACTIVE', 'Active'
    ACKNOWLEDGED = 'ACKNOWLEDGED', 'Acknowledged'
    RESOLVED = 'RESOLVED', 'Resolved'

class Alert(models.Model):
    facility = models.ForeignKey(
        Facility,
        on_delete=models.CASCADE,
        related_name='alerts'
    )
    alert_type = models.CharField(
        max_length=40,
        choices=AlertType.choices,
        db_index=True
    )
    severity = models.CharField(
        max_length=20,
        choices=AlertSeverity.choices,
        default=AlertSeverity.WARNING,
        db_index=True
    )
    message = models.TextField()
    current_stock = models.IntegerField(default=0)
    days_of_cover = models.FloatField(null=True, blank=True, default=None)
    daily_usage = models.FloatField(default=0.0)
    created_at = models.DateTimeField(default=timezone.now, db_index=True)
    resolved_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(
        max_length=20,
        choices=AlertStatus.choices,
        default=AlertStatus.ACTIVE,
        db_index=True
    )

    class Meta:
        verbose_name = 'Operational Alert'
        verbose_name_plural = 'Operational Alerts'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['facility', 'alert_type', 'status']),
            models.Index(fields=['severity', 'status']),
        ]

    def __str__(self):
        return f"[{self.get_severity_display()}] {self.facility.name}: {self.message}"

    @property
    def severity_badge_color(self) -> str:
        if self.severity == AlertSeverity.CRITICAL:
            return '#B71C1C'  # Semantic Red
        elif self.severity == AlertSeverity.WARNING:
            return '#D97706'  # Semantic Amber
        return '#546E7A'      # Neutral Slate

