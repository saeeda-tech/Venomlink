"""
Inventory management services for VenomLink.
Provides atomic, row-locked operations for stock updates, case logs,
redistribution transfers, and expiry validation.
"""

from calendar import monthrange
from datetime import date
import re
from typing import Optional

from django.db import transaction
from django.utils import timezone

from apps.facilities.models import Facility, Reporter
from .models import InventoryTransaction, TransactionType, SnakebiteCase, CaseOutcome

EXPIRY_REGEX = re.compile(r'^(0[1-9]|1[0-2])\/([0-9]{2})$')


def validate_expiry_format(expiry_str: str) -> bool:
    """
    Validates MM/YY format.
    Checks that month is between 01 and 12 and format is exact.
    """
    if not expiry_str:
        return True
    cleaned = expiry_str.strip()
    return bool(EXPIRY_REGEX.match(cleaned))


def parse_expiry_date(expiry_str: str) -> Optional[date]:
    """
    Parses MM/YY string to the last calendar day of the specified month, or None.
    """
    if not expiry_str or not validate_expiry_format(expiry_str):
        return None
    month, year = map(int, expiry_str.strip().split('/'))
    full_year = 2000 + year
    _, last_day = monthrange(full_year, month)
    return date(full_year, month, last_day)


def is_expiry_date_expired(expiry_str: str) -> bool:
    """
    Returns True if the MM/YY batch expiry date is strictly in the past
    (before the current local date).
    """
    parsed = parse_expiry_date(expiry_str)
    if not parsed:
        return False
    return parsed < timezone.now().date()


@transaction.atomic
def record_stock_update(
    facility: Facility,
    quantity: int,
    expiry_date: str = '',
    reporter: Reporter = None,
    transaction_type: str = TransactionType.STOCK_CONFIRMATION,
    notes: str = '',
    batch_number: str = ''
) -> InventoryTransaction:
    """
    Records an inventory stock update, receipt, or confirmation.
    Acquires a row-level lock on the Facility record to prevent lost updates.
    Rejects already-expired stock batches during receipt or positive stock adjustment.
    """
    if not isinstance(quantity, int) or isinstance(quantity, bool) or quantity < 0:
        raise ValueError("Stock quantity must be a non-negative whole number (≥ 0).")

    cleaned_expiry = expiry_date.strip() if expiry_date else ''
    if cleaned_expiry and not validate_expiry_format(cleaned_expiry):
        raise ValueError("Expiry date must match MM/YY format (e.g. 03/27).")

    # Reject already-expired batches when recording or confirming positive inventory
    if cleaned_expiry and quantity > 0 and is_expiry_date_expired(cleaned_expiry):
        parsed_exp = parse_expiry_date(cleaned_expiry)
        raise ValueError(
            f"Cannot accept or record expired antivenom batch ({cleaned_expiry}, "
            f"expired {parsed_exp.isoformat()})."
        )

    # Lock the facility row to serialize concurrent updates
    locked_facility = Facility.objects.select_for_update().get(pk=facility.pk)
    locked_facility._clear_runtime_cache()

    if not cleaned_expiry and locked_facility.latest_expiry and locked_facility.latest_expiry != "Not recorded":
        if quantity == 0 or not is_expiry_date_expired(locked_facility.latest_expiry):
            cleaned_expiry = locked_facility.latest_expiry
        elif quantity > 0:
            raise ValueError(
                f"Existing batch at {locked_facility.name} ({locked_facility.latest_expiry}) is expired. "
                f"Please provide a valid future batch expiry date (MM/YY)."
            )

    previous_stock = locked_facility.current_stock
    delta = quantity - previous_stock

    tx = InventoryTransaction.objects.create(
        facility=locked_facility,
        reporter=reporter,
        transaction_type=transaction_type,
        quantity=delta,
        stock_after=quantity,
        expiry_date=cleaned_expiry,
        batch_number=batch_number.strip(),
        timestamp=timezone.now(),
        notes=notes or f"Stock updated from {previous_stock} to {quantity} vials"
    )

    locked_facility._clear_runtime_cache()
    facility._clear_runtime_cache()

    from apps.analytics.alert_engine import evaluate_facility_alerts
    evaluate_facility_alerts(locked_facility)

    return tx


@transaction.atomic
def record_case_treatment(
    facility: Facility,
    vials_used: int,
    outcome: str = CaseOutcome.RECOVERED,
    reporter: Reporter = None,
    date=None,
    notes: str = ''
) -> SnakebiteCase:
    """
    Records an anonymized snakebite clinical treatment case and deducts vials.
    Acquires a row-level lock on the Facility record.
    Rejects deductions that exceed available physical stock or attempt to use expired stock,
    preventing negative inventory and audit delta mismatches.
    """
    if not isinstance(vials_used, int) or isinstance(vials_used, bool) or vials_used <= 0:
        raise ValueError("Vials used must be a whole number of at least 1.")

    locked_facility = Facility.objects.select_for_update().get(pk=facility.pk)
    locked_facility._clear_runtime_cache()

    if locked_facility.has_expired_stock:
        raise ValueError(
            f"Cannot administer antivenom from expired batch ({locked_facility.latest_expiry}) "
            f"at {locked_facility.name}."
        )

    current_stock = locked_facility.current_stock
    if vials_used > current_stock:
        raise ValueError(
            f"Insufficient stock at {locked_facility.name}: cannot deduct {vials_used} vial(s) "
            f"when only {current_stock} vial(s) are available."
        )

    if not date:
        date = timezone.now().date()

    case = SnakebiteCase.objects.create(
        facility=locked_facility,
        reporter=reporter,
        date=date,
        vials_used=vials_used,
        outcome=outcome,
        notes=notes
    )

    new_stock = current_stock - vials_used
    expiry = locked_facility.latest_expiry if locked_facility.latest_expiry != "Not recorded" else ''

    InventoryTransaction.objects.create(
        facility=locked_facility,
        reporter=reporter,
        transaction_type=TransactionType.USED,
        quantity=-vials_used,
        stock_after=new_stock,
        expiry_date=expiry,
        timestamp=timezone.now(),
        notes=f"Administered {vials_used} vial(s) for snakebite case #{case.id}"
    )

    locked_facility._clear_runtime_cache()
    facility._clear_runtime_cache()

    from apps.analytics.alert_engine import evaluate_facility_alerts
    evaluate_facility_alerts(locked_facility)

    return case


@transaction.atomic
def record_redistribution_transfer(
    source_facility: Facility,
    target_facility: Facility,
    quantity: int,
    reporter: Reporter = None,
    notes: str = ''
):
    """
    Executes an atomic antivenom stock redistribution transfer between two facilities.
    Locks both facilities in ascending primary-key order to prevent deadlocks.
    Validates source stock, prevents self-transfer, rejects expired stock transfers,
    and guarantees conservation of total inventory.
    """
    if not isinstance(quantity, int) or isinstance(quantity, bool) or quantity <= 0:
        raise ValueError("Transfer quantity must be a whole number greater than 0.")

    if source_facility.pk == target_facility.pk:
        raise ValueError("Cannot transfer stock from a facility to itself.")

    # Lock both facilities in deterministic ascending PK order to avoid deadlocks
    ordered_pks = sorted([source_facility.pk, target_facility.pk])
    locked_qs = Facility.objects.select_for_update().filter(pk__in=ordered_pks).order_by('pk')
    locked_map = {f.pk: f for f in locked_qs}

    locked_source = locked_map[source_facility.pk]
    locked_target = locked_map[target_facility.pk]
    locked_source._clear_runtime_cache()
    locked_target._clear_runtime_cache()

    if locked_source.has_expired_stock:
        raise ValueError(
            f"Cannot transfer expired antivenom batch ({locked_source.latest_expiry}) "
            f"from {locked_source.name}."
        )

    src_current = locked_source.current_stock
    if src_current < quantity:
        raise ValueError(
            f"Cannot transfer {quantity} vials: source facility ({locked_source.name}) "
            f"only has {src_current} available."
        )

    now = timezone.now()
    src_expiry = locked_source.latest_expiry if locked_source.latest_expiry != "Not recorded" else ''

    # If target facility currently holds an expired batch, quarantine it to 0 first
    # so expired vials are never merged into incoming valid stock.
    if locked_target.has_expired_stock:
        expired_qty = locked_target.current_stock
        expired_lot = locked_target.latest_expiry
        InventoryTransaction.objects.create(
            facility=locked_target,
            reporter=reporter,
            transaction_type=TransactionType.EXPIRED,
            quantity=-expired_qty,
            stock_after=0,
            expiry_date=expired_lot,
            timestamp=now,
            notes=f"Auto-quarantined {expired_qty} expired vial(s) (batch {expired_lot}) prior to transfer receipt."
        )
        locked_target._clear_runtime_cache()

    tgt_current = locked_target.usable_stock
    src_new_stock = src_current - quantity
    tgt_new_stock = tgt_current + quantity

    tgt_existing_expiry = (
        locked_target.latest_expiry
        if (tgt_current > 0 and locked_target.latest_expiry != "Not recorded" and not is_expiry_date_expired(locked_target.latest_expiry))
        else ''
    )

    # Preserve nearest valid expiry date when combining two non-expired batches
    if src_expiry and tgt_existing_expiry:
        src_parsed = parse_expiry_date(src_expiry)
        tgt_parsed = parse_expiry_date(tgt_existing_expiry)
        if src_parsed and tgt_parsed:
            effective_tgt_expiry = src_expiry if src_parsed <= tgt_parsed else tgt_existing_expiry
        else:
            effective_tgt_expiry = src_expiry
    else:
        effective_tgt_expiry = src_expiry or tgt_existing_expiry

    tx_out = InventoryTransaction.objects.create(
        facility=locked_source,
        reporter=reporter,
        transaction_type=TransactionType.TRANSFER_OUT,
        quantity=-quantity,
        stock_after=src_new_stock,
        expiry_date=src_expiry,
        timestamp=now,
        notes=f"Transferred {quantity} vial(s) (batch {src_expiry or 'N/A'}) to {locked_target.name}. {notes}".strip()
    )

    tx_in = InventoryTransaction.objects.create(
        facility=locked_target,
        reporter=reporter,
        transaction_type=TransactionType.TRANSFER_IN,
        quantity=quantity,
        stock_after=tgt_new_stock,
        expiry_date=effective_tgt_expiry,
        timestamp=now,
        notes=(
            f"Received {quantity} vial(s) (batch {src_expiry or 'N/A'}) transferred from {locked_source.name}. "
            f"{notes}"
        ).strip()
    )

    locked_source._clear_runtime_cache()
    locked_target._clear_runtime_cache()
    source_facility._clear_runtime_cache()
    target_facility._clear_runtime_cache()

    from apps.analytics.alert_engine import evaluate_facility_alerts
    evaluate_facility_alerts(locked_source)
    evaluate_facility_alerts(locked_target)

    return tx_out, tx_in
