"""
Operational Alert Engine and Redistribution Intelligence for VenomLink.

Generates deterministic, deduplicated alerts from the single-source-of-truth classifier:
- POTENTIAL_STOCKOUT (0 usable vials, expired batch, or days_of_cover <= 5.0)
- LOW_STOCK (5.0 < days_of_cover <= 7.0)
- LOW_TREATMENT_READINESS (independent check: usable vials < MIN_TREATMENT_COURSE_VIALS)
- STALE_DATA (>48h without confirmation)
- EXPIRY_RISK (CRITICAL if expired, WARNING if expiring within 60 days)

Automatically resolves open alerts when inventory conditions recover.
"""

from django.utils import timezone
from apps.facilities.models import Facility
from .models import Alert, AlertType, AlertSeverity, AlertStatus
from .forecasting import calculate_facility_forecast
from .thresholds import classify_facility_stock, get_stock_threshold_config
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
    Evaluates operational health for a single facility using the single-source-of-truth
    threshold classifier and updates/resolves alerts deterministically.
    """
    tcfg = get_stock_threshold_config()
    stock_out_thresh = tcfg['stock_out_threshold']
    critical_doc = tcfg['critical_days_of_cover']
    warning_doc = tcfg['warning_days_of_cover']
    min_course_vials = tcfg['min_treatment_course_vials']
    exp_warn_days = tcfg['expiry_warning_days']

    forecast = calculate_facility_forecast(facility)
    physical_stock = forecast['current_stock']
    usable_stock = forecast['usable_stock']
    days_of_cover = forecast['days_of_cover']
    daily_usage = forecast['average_daily_usage']
    stock_status = forecast['stock_status']
    below_min_course = forecast['below_min_treatment_course']

    # 1. EVALUATE STOCK-OUT & CRITICAL DAYS OF COVER (0 usable vials or days_of_cover <= 5.0)
    if stock_status in ('STOCK_OUT', 'CRITICAL'):
        if usable_stock <= stock_out_thresh or facility.has_expired_stock:
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
        else:
            msg = (
                f"{facility.name}: estimated {days_of_cover} days of cover "
                f"(at or below the {critical_doc:.1f}-day critical threshold). "
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

    # 2. EVALUATE LOW STOCK WARNING (5.0 < days_of_cover <= 7.0)
    if stock_status == 'LOW_STOCK':
        msg = (
            f"{facility.name}: {usable_stock} usable vials remaining "
            f"({days_of_cover} days of cover at {daily_usage} vials/day; "
            f"within >{critical_doc:.1f} to {warning_doc:.1f}-day warning window). Schedule replenishment."
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

    # 2b. INDEPENDENT TREATMENT-COURSE ADEQUACY ALERT (usable vials < MIN_TREATMENT_COURSE_VIALS)
    if below_min_course:
        doc_context = (
            f"while days of cover is {days_of_cover} days"
            if days_of_cover is not None
            else "usage history insufficient for days-of-cover forecast"
        )
        msg = (
            f"Below minimum treatment course planning assumption at {facility.name}: "
            f"{usable_stock} usable vial(s) remaining (assumption < {min_course_vials} vials; "
            f"requires local clinical validation), {doc_context}."
        )
        readiness_severity = (
            AlertSeverity.CRITICAL
            if (usable_stock <= 2 and days_of_cover is None)
            else AlertSeverity.WARNING
        )
        _upsert_single_open_alert(
            facility=facility,
            alert_type=AlertType.LOW_TREATMENT_READINESS,
            severity=readiness_severity,
            message=msg,
            current_stock=usable_stock,
            days_of_cover=days_of_cover,
            daily_usage=daily_usage
        )
    else:
        _resolve_open_alerts(facility, AlertType.LOW_TREATMENT_READINESS)

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
    Identifies safe redistribution pairs using the shared stock threshold rules:
    - Donor facility: Confirmed (non-stale), non-expired stock >= 10 vials, and
      days_of_cover > WARNING_DAYS_OF_COVER_THRESHOLD (or no usage history).
    - Recipient facility: STOCK_OUT / CRITICAL / LOW_STOCK (days_of_cover <= 7.0 or 0 usable)
      or below minimum treatment course with <= 2 usable vials.
    - Distance <= max_distance_km.
    """
    tcfg = get_stock_threshold_config()
    warning_doc = tcfg['warning_days_of_cover']

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
        classification = classify_facility_stock(
            usable_stock=usable,
            has_expired_stock=fac.has_expired_stock,
            is_stale=fac.is_stale,
            days_of_cover=days_of_cover,
        )
        status = classification['stock_status']

        # Donor must have non-expired, non-stale stock >= 10 vials and > warning_doc cover
        can_donate = (
            usable >= 10
            and not fac.has_expired_stock
            and not fac.is_stale
            and (days_of_cover is None or days_of_cover > warning_doc)
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

        needs_stock = (
            status in ('STOCK_OUT', 'CRITICAL', 'LOW_STOCK')
            or (classification['below_min_treatment_course'] and usable <= 2)
        )
        if needs_stock:
            target_level = max(6, int(daily_usage * 7)) if daily_usage > 0 else 6
            deficit = max(3, target_level - usable)
            recipients.append({
                'facility': fac,
                'stock': usable,
                'stock_status': status,
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
                        'recipient_stock_status': rec['stock_status'],
                        'suggested_quantity': suggested_qty,
                        'distance_km': min_dist,
                        'urgency': 'CRITICAL' if (rec['stock_status'] in ('STOCK_OUT', 'CRITICAL') or rec['stock'] <= 2) else 'HIGH',
                        'notice': 'Review for possible redistribution. Antivenom transfers require medical logistics authorization.'
                    })

    recommendations.sort(key=lambda x: (0 if x['urgency'] == 'CRITICAL' else 1, x['distance_km']))
    return recommendations
