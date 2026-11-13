"""
Inventory Transactions and Snakebite Case Registry models for VenomLink.

Strictly preserves an immutable audit trail of stock movements, adjustments,
expiries, and anonymized clinical case consumptions.
"""

from django.db import models
from django.utils import timezone
from apps.facilities.models import Facility, Reporter


class TransactionType(models.TextChoices):
    RECEIVED = 'RECEIVED', 'Stock Received / Resupply'
    USED = 'USED', 'Vials Administered / Used'
    ADJUSTMENT = 'ADJUSTMENT', 'Audit / Physical Count Adjustment'
    EXPIRED = 'EXPIRED', 'Expired Batch Quarantined / Removed'
    TRANSFER_IN = 'TRANSFER_IN', 'Transfer In from Another Facility'
    TRANSFER_OUT = 'TRANSFER_OUT', 'Transfer Out to Another Facility'
    STOCK_CONFIRMATION = 'STOCK_CONFIRMATION', 'Stock Count Confirmation (USSD / Web)'


class InventoryTransaction(models.Model):
    facility = models.ForeignKey(
        Facility,
        on_delete=models.CASCADE,
        related_name='inventory_transactions'
    )
    reporter = models.ForeignKey(
        Reporter,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='transactions'
    )
    transaction_type = models.CharField(
        max_length=30,
        choices=TransactionType.choices,
        default=TransactionType.STOCK_CONFIRMATION
    )
    # Quantity change in this transaction (exact delta: stock_after - previous_stock)
    quantity = models.IntegerField(
        help_text="Quantity delta applied in this transaction"
    )
    # The resulting total stock after this transaction was processed
    stock_after = models.PositiveIntegerField(
        default=0,
        help_text="Total physical stock remaining at facility after this event"
    )
    expiry_date = models.CharField(
        max_length=10,
        blank=True,
        help_text="Nearest batch expiry in MM/YY format (e.g. 03/27)"
    )
    batch_number = models.CharField(
        max_length=50,
        blank=True,
        help_text="Manufacturer batch / lot number"
    )
    timestamp = models.DateTimeField(
        default=timezone.now,
        db_index=True
    )
    notes = models.TextField(blank=True)

    class Meta:
        # Deterministic ordering: timestamp descending, then primary key descending
        ordering = ['-timestamp', '-id']
        indexes = [
            models.Index(fields=['facility', '-timestamp', '-id']),
            models.Index(fields=['transaction_type', 'timestamp']),
        ]

    def __str__(self):
        return (
            f"[{self.get_transaction_type_display()}] {self.facility.name} "
            f"| Stock: {self.stock_after} vials ({self.timestamp.strftime('%Y-%m-%d %H:%M')})"
        )


class CaseOutcome(models.TextChoices):
    RECOVERED = 'RECOVERED', 'Patient Discharged / Recovered'
    UNDER_TREATMENT = 'UNDER_TREATMENT', 'Currently Under Treatment'
    TRANSFERRED = 'TRANSFERRED', 'Referred / Transferred to Higher Facility'
    FATALITY = 'FATALITY', 'Fatality'


class SnakebiteCase(models.Model):
    """
    Anonymized clinical case consumption data.
    Stores NO patient names, personal addresses, or personal phone numbers.
    Used strictly for epidemiological surveillance and antivenom consumption rate calculation.
    """
    facility = models.ForeignKey(
        Facility,
        on_delete=models.CASCADE,
        related_name='cases'
    )
    reporter = models.ForeignKey(
        Reporter,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='reported_cases'
    )
    date = models.DateField(default=timezone.now, db_index=True)
    vials_used = models.PositiveIntegerField(
        default=1,
        help_text="Number of antivenom vials administered for this case"
    )
    outcome = models.CharField(
        max_length=30,
        choices=CaseOutcome.choices,
        default=CaseOutcome.RECOVERED
    )
    notes = models.TextField(
        blank=True,
        help_text="Anonymized operational notes (e.g. carpet viper bite, severe envenomation)"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Snakebite Case (Anonymized)'
        verbose_name_plural = 'Snakebite Cases (Anonymized)'
        ordering = ['-date', '-id']
        indexes = [
            models.Index(fields=['facility', '-date']),
        ]

    def __str__(self):
        return (
            f"Case #{self.id} at {self.facility.name}: {self.vials_used} vial(s) "
            f"on {self.date} ({self.get_outcome_display()})"
        )
