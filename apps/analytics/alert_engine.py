"""
Operational Alert Engine and Redistribution Intelligence for VenomLink.

Generates deterministic, deduplicated alerts for:
- POTENTIAL_STOCKOUT (0 vials STOCK_OUT, 1-2 vials CRITICAL, or high-confidence days_of_cover <= reorder_buffer)
- LOW_STOCK (3-4 vials or high-confidence days_of_cover <= reorder_buffer + 3)
- STALE_DATA (>48h without confirmation)
- EXPIRY_RISK (CRITICAL if expired, WARNING if expiring within 60 days)

Automatically resolves open alerts when inventory conditions recover.
"""

from django.conf import settings
from django.utils import timezone
from apps.facilities.models import Facility, ConsumptionConfidence
from .models import Alert, AlertType, AlertSeverity, AlertStatus
from .forecasting import calculate_facility_forecast
from apps.matching.engine import haversine_distance_km


def _upsert_single_open_alert(
    facility: Facility,
    alert_type: str,
    severity: str,
    message: str,
    current_stock: int,
    days_of_cover,
    daily_usage: float
):
    """
    Ensures at most one open (ACTIVE or ACKNOWLEDGED) alert exists per (facility, alert_type).
    Updates existing open alert in place without duplicating records.
    """
    open_alerts = list(
        Alert.objects.filter(
            facility=facility,
            alert_type=alert_type,
            status__in=[AlertStatus.ACTIVE, AlertStatus.ACKNOWLEDGED]
        ).order_by('-created_at', '-id')
    )
    if open_alerts:
        primary = open_alerts[0]
        primary.severity = severity
        primary.message = message
        primary.current_stock = current_stock
        primary.days_of_cover = days_of_cover
        primary.daily_usage = daily_usage
        primary.save(update_fields=[
            'severity', 'message', 'current_stock', 'days_of_cover', 'daily_usage'
        ])
        if len(open_alerts) > 1:
            dup_ids = [a.id for a in open_alerts[1:]]
            Alert.objects.filter(id__in=dup_ids).update(
                status=AlertStatus.RESOLVED,
                resolved_at=timezone.now()
            )
    else:
        Alert.objects.create(
            facility=facility,
            alert_type=alert_type,
            severity=severity,
            status=AlertStatus.ACTIVE,
            message=message,
            current_stock=current_stock,
            days_of_cover=days_of_cover,
            daily_usage=daily_usage,
            created_at=timezone.now()
        )


def _resolve_open_alerts(facility: Facility, alert_type: str):
    """Automatically resolves any ACTIVE or ACKNOWLEDGED alerts of the given type."""
    Alert.objects.filter(
        facility=facility,
        alert_type=alert_type,
        status__in=[AlertStatus.ACTIVE, AlertStatus.ACKNOWLEDGED]
    ).update(status=AlertStatus.RESOLVED, resolved_at=timezone.now())


def evaluate_facility_alerts(facility: Facility):
    """
    Evaluates operational health for a single facility using configurable thresholds
    and updates/resolves alerts deterministically.
    """
    cfg = getattr(settings, 'VENOMLINK_CONFIG', {})
    stock_out_thresh = cfg.get('STOCK_OUT_THRESHOLD', 0)
    crit_max_vials = cfg.get('CRITICAL_STOCK_MAX_VIALS', 2)
    low_max_vials = cfg.get('LOW_STOCK_MAX_VIALS', 4)
    exp_warn_days = cfg.get('EXPIRY_WARNING_DAYS', 60)

    forecast = calculate_facility_forecast(facility)
    physical_stock = forecast['current_stock']
    usable_stock = forecast['usable_stock']
    days_of_cover = forecast['days_of_cover']
    daily_usage = forecast['average_daily_usage']
    confidence = forecast['consumption_confidence']
    reorder_buffer = forecast['reorder_buffer_days']

    # 1. EVALUATE STOCK-OUT & CRITICAL INVENTORY (0 vials OR 1-2 vials OR high-confidence DoC <= buffer)
    is_stockout_or_critical = (
        usable_stock <= crit_max_vials
        or (
            confidence == ConsumptionConfidence.HIGH_CONFIDENCE
            and days_of_cover is not None
            and days_of_cover <= reorder_buffer
        )
    )

    if is_stockout_or_critical:
        if usable_stock <= stock_out_thresh:
            if physical_stock > 0 and facility.has_expired_stock:
                msg = (
                    f"{facility.name} has 0 usable vials ({physical_stock} vials on hand are EXPIRED: "
                    f"batch {facility.latest_expiry}). Immediate emergency resupply required."
                )
            else:
                msg = (
                    f"{facility.name} has exhausted antivenom reserves (0 vials remaining). "
                    f"Immediate emergency resupply required."
                )
        elif usable_stock <= crit_max_vials:
            doc_note = (
                f"Estimated cover: {days_of_cover} days ({daily_usage}/day)."
                if days_of_cover is not None
                else "Consumption history insufficient for days-of-cover forecast."
            )
            msg = (
                f"Critical antivenom shortage at {facility.name}: only {usable_stock} usable vial(s) "
                f"remaining (threshold <= {crit_max_vials} vials). {doc_note}"
            )
        else:
            msg = (
                f"{facility.name} projected to run out of antivenom within {days_of_cover} days "
                f"(below {reorder_buffer}-day replenishment threshold). "
                f"Current stock: {usable_stock} vials, average usage: {daily_usage}/day."
            )

        _upsert_single_open_alert(
            facility=facility,
            alert_type=AlertType.POTENTIAL_STOCKOUT,
            severity=AlertSeverity.CRITICAL,
            message=msg,
            current_stock=usable_stock,
            days_of_cover=days_of_cover,
            daily_usage=daily_usage
        )
    else:
        _resolve_open_alerts(facility, AlertType.POTENTIAL_STOCKOUT)

    # 2. EVALUATE LOW STOCK WARNING (3-4 vials OR high-confidence DoC <= reorder_buffer + 3)
    is_low_stock = (not is_stockout_or_critical) and (
        usable_stock <= low_max_vials
        or (
            confidence == ConsumptionConfidence.HIGH_CONFIDENCE
            and days_of_cover is not None
            and days_of_cover <= (reorder_buffer + 3)
        )
    )

    if is_low_stock:
        doc_note = (
            f"{days_of_cover} days of cover at {daily_usage} vials/day"
            if days_of_cover is not None
            else "usage history insufficient for forecast"
        )
        msg = (
            f"{facility.name} has low antivenom reserves: {usable_stock} vials remaining "
            f"({doc_note}). Schedule replenishment."
        )
        _upsert_single_open_alert(
            facility=facility,
            alert_type=AlertType.LOW_STOCK,
            severity=AlertSeverity.WARNING,
            message=msg,
            current_stock=usable_stock,
            days_of_cover=days_of_cover,
            daily_usage=daily_usage
        )
    else:
        _resolve_open_alerts(facility, AlertType.LOW_STOCK)

    # 3. EVALUATE STALE DATA
    if facility.is_stale:
        last = facility.last_confirmed_at
        age_hours = int((timezone.now() - last).total_seconds() // 3600) if last else 999
        time_desc = f"{age_hours} hours ago" if last else "no previous confirmation recorded"
        msg = (
            f"Stock data for {facility.name} is unconfirmed ({time_desc}). "
            f"Prompt facility reporter for physical count verification."
        )
        _upsert_single_open_alert(
            facility=facility,
            alert_type=AlertType.STALE_DATA,
            severity=AlertSeverity.WARNING,
            message=msg,
            current_stock=usable_stock,
            days_of_cover=days_of_cover,
            daily_usage=daily_usage
        )
    else:
        _resolve_open_alerts(facility, AlertType.STALE_DATA)

    # 4. EVALUATE BATCH EXPIRY RISK (Expired or Expiring Within Window)
    days_to_exp = facility.days_to_expiry
    if physical_stock > 0 and days_to_exp is not None:
        if days_to_exp < 0:
            msg = (
                f"EXPIRED BATCH ALERT: Antivenom batch at {facility.name} ({facility.latest_expiry}) "
                f"expired {abs(days_to_exp)} days ago ({physical_stock} vials on hand). "
                f"Quarantine immediately; excluded from usable stock."
            )
            _upsert_single_open_alert(
                facility=facility,
                alert_type=AlertType.EXPIRY_RISK,
                severity=AlertSeverity.CRITICAL,
                message=msg,
                current_stock=physical_stock,
                days_of_cover=days_of_cover,
                daily_usage=daily_usage
            )
        elif days_to_exp <= exp_warn_days:
            msg = (
                f"Nearest batch at {facility.name} ({facility.latest_expiry}) "
                f"expires in {days_to_exp} days. Review for priority clinical use or redistribution."
            )
            _upsert_single_open_alert(
                facility=facility,
                alert_type=AlertType.EXPIRY_RISK,
                severity=AlertSeverity.WARNING,
                message=msg,
                current_stock=usable_stock,
                days_of_cover=days_of_cover,
                daily_usage=daily_usage
            )
        else:
            _resolve_open_alerts(facility, AlertType.EXPIRY_RISK)
    else:
        _resolve_open_alerts(facility, AlertType.EXPIRY_RISK)


def evaluate_all_alerts():
    """Batch run across all facilities using prefetched transactions and cases."""
    facilities = Facility.objects.prefetch_related('inventory_transactions', 'cases').all()
    for fac in facilities:
        evaluate_facility_alerts(fac)


def find_redistribution_recommendations(state: str = None, max_distance_km: float = 120.0):
    """
    Identifies safe redistribution pairs:
    - Donor facility: Confirmed (non-stale), non-expired stock >= 10 vials, and either
      high/low confidence days of cover >= 8.0 or low historical demand.
    - Recipient facility: Usable stock <= 4 vials or high-confidence days of cover <= reorder buffer.
    - Distance <= max_distance_km.
    """
    facilities_qs = Facility.objects.filter(verified=True).prefetch_related('inventory_transactions', 'cases')
    if state:
        facilities_qs = facilities_qs.filter(state__iexact=state.strip())

    facilities = list(facilities_qs)

    donors = []
    recipients = []

    for fac in facilities:
        usable = fac.usable_stock
        daily_usage = fac.calculate_average_daily_usage(days=30)
        days_of_cover = fac.days_of_cover
        reorder_buffer = fac.resupply_lead_time_days + fac.safety_buffer_days

        # Donor must have non-expired, non-stale stock >= 10 vials
        can_donate = (
            usable >= 10
            and not fac.has_expired_stock
            and not fac.is_stale
            and (days_of_cover is None or days_of_cover >= 8.0)
        )
        if can_donate:
            reserved_vials = int(daily_usage * 7) if daily_usage > 0 else 6
            safe_surplus = max(2, usable - max(6, reserved_vials))
            donors.append({
                'facility': fac,
                'stock': usable,
                'daily_usage': daily_usage,
                'days_of_cover': days_of_cover,
                'days_of_cover_display': fac.days_of_cover_display,
                'safe_surplus': safe_surplus
            })

        # Recipient: critical/low stock or high-confidence days_of_cover <= reorder_buffer
        needs_stock = (
            usable <= 4
            or (
                fac.consumption_confidence == ConsumptionConfidence.HIGH_CONFIDENCE
                and days_of_cover is not None
                and days_of_cover <= reorder_buffer
            )
        )
        if needs_stock:
            target_level = max(6, int(daily_usage * 5)) if daily_usage > 0 else 6
            deficit = max(3, target_level - usable)
            recipients.append({
                'facility': fac,
                'stock': usable,
                'daily_usage': daily_usage,
                'days_of_cover': days_of_cover,
                'days_of_cover_display': fac.days_of_cover_display,
                'deficit': deficit
            })

    recommendations = []
    pair_keys = set()

    for rec in recipients:
        r_fac = rec['facility']
        best_donor_match = None
        min_dist = float('inf')

        for don in donors:
            d_fac = don['facility']
            if d_fac.id == r_fac.id:
                continue

            dist = haversine_distance_km(
                float(d_fac.latitude), float(d_fac.longitude),
                float(r_fac.latitude), float(r_fac.longitude)
            )

            if dist <= max_distance_km and dist < min_dist:
                min_dist = dist
                best_donor_match = don

        if best_donor_match:
            d_fac = best_donor_match['facility']
            pair_key = f"{d_fac.id}->{r_fac.id}"
            if pair_key not in pair_keys:
                pair_keys.add(pair_key)
                suggested_qty = min(best_donor_match['safe_surplus'], rec['deficit'], 6)
                if suggested_qty > 0:
                    recommendations.append({
                        'donor_facility_id': d_fac.id,
                        'donor_name': d_fac.name,
                        'donor_lga': d_fac.lga,
                        'donor_state': d_fac.state,
                        'donor_stock': best_donor_match['stock'],
                        'donor_expiry': d_fac.latest_expiry,
                        'donor_days_of_cover': best_donor_match['days_of_cover'],
                        'donor_days_of_cover_display': best_donor_match['days_of_cover_display'],
                        'recipient_facility_id': r_fac.id,
                        'recipient_name': r_fac.name,
                        'recipient_lga': r_fac.lga,
                        'recipient_state': r_fac.state,
                        'recipient_stock': rec['stock'],
                        'recipient_days_of_cover': rec['days_of_cover'],
                        'recipient_days_of_cover_display': rec['days_of_cover_display'],
                        'suggested_quantity': suggested_qty,
                        'distance_km': min_dist,
                        'urgency': 'CRITICAL' if rec['stock'] <= 2 else 'HIGH',
                        'notice': 'Review for possible redistribution. Antivenom transfers require medical logistics authorization.'
                    })

    recommendations.sort(key=lambda x: (0 if x['urgency'] == 'CRITICAL' else 1, x['distance_km']))
    return recommendations
