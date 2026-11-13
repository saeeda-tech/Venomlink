"""
Search tracking and operational audit models for antivenom lookups.
Does not store personal identities or raw personal numbers.
"""

from django.db import models
from django.utils import timezone
from apps.facilities.models import Facility

class AntivenomSearch(models.Model):
    state = models.CharField(max_length=100, db_index=True)
    lga = models.CharField(max_length=100, db_index=True, blank=True)
    phone_hash = models.CharField(
        max_length=64,
        blank=True,
        help_text="Anonymized caller hash for aggregate demand pattern analysis"
    )
    timestamp = models.DateTimeField(default=timezone.now, db_index=True)
    results_found = models.PositiveIntegerField(default=0)
    successful_match = models.BooleanField(
        default=False,
        help_text="True if at least one available confirmed facility was presented"
    )
    selected_facility = models.ForeignKey(
        Facility,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='searches_matched'
    )
    channel = models.CharField(
        max_length=20,
        choices=[('USSD', 'USSD'), ('SMS', 'SMS'), ('WEB', 'Web Dashboard')],
        default='USSD'
    )

    class Meta:
        verbose_name = 'Antivenom Search Audit'
        verbose_name_plural = 'Antivenom Search Audits'
        ordering = ['-timestamp']

    def __str__(self):
        return f"Search in {self.lga}, {self.state} ({self.timestamp.strftime('%Y-%m-%d %H:%M')}) - {self.results_found} found"

