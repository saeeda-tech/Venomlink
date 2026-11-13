"""
Days of Cover and Antivenom Consumption Forecasting for VenomLink.

Follows the formula:
Days of Cover = Usable Stock ÷ Average Daily Usage

Never invents arbitrary consumption rates when historical data is missing.
Returns explicit data quality / confidence indicators:
- HIGH_CONFIDENCE
- LOW_CONFIDENCE
- INSUFFICIENT_DATA
"""

from datetime import timedelta
from django.conf import settings
from django.utils import timezone
from apps.facilities.models import Facility, ConsumptionConfidence


def calculate_facility_forecast(facility: Facility, window_days: int = 30) -> dict:
    """
    Computes stock intelligence metrics for a health facility without arbitrary fallbacks.
    """
    cfg = getattr(settings, 'VENOMLINK_CONFIG', {})
    stock_out_thresh = cfg.get('STOCK_OUT_THRESHOLD', 0)
    crit_max_vials = cfg.get('CRITICAL_STOCK_MAX_VIALS', 2)
    low_max_vials = cfg.get('LOW_STOCK_MAX_VIALS', 4)

    physical_stock = facility.current_stock
    usable_stock = facility.usable_stock
    metrics = facility.get_consumption_metrics(days=window_days)

    daily_usage = metrics['average_daily_usage']
    confidence = metrics['confidence']
    lead_time = facility.resupply_lead_time_days
    safety_buffer = facility.safety_buffer_days
    reorder_buffer_days = lead_time + safety_buffer

    if usable_stock <= stock_out_thresh:
        days_of_cover = 0.0
        projected_depletion_date = timezone.now().date().isoformat()
    elif daily_usage <= 0 or confidence == ConsumptionConfidence.INSUFFICIENT_DATA:
        days_of_cover = None
        projected_depletion_date = None
    else:
        days_of_cover = round(usable_stock / daily_usage, 1)
        depletion_days = min(3650, int(days_of_cover))
        projected_depletion_date = (timezone.now().date() + timedelta(days=depletion_days)).isoformat()

    # Evaluate critical and warning risk using independent vial thresholds first,
    # then days-of-cover when consumption history has HIGH_CONFIDENCE.
    is_critical = (
        usable_stock <= crit_max_vials
        or (
            confidence == ConsumptionConfidence.HIGH_CONFIDENCE
            and days_of_cover is not None
            and days_of_cover <= reorder_buffer_days
        )
    )
    is_warning = (not is_critical) and (
        usable_stock <= low_max_vials
        or (
            confidence == ConsumptionConfidence.HIGH_CONFIDENCE
            and days_of_cover is not None
            and days_of_cover <= (reorder_buffer_days + 3)
        )
    )

    return {
        'facility_id': facility.id,
        'facility_name': facility.name,
        'current_stock': physical_stock,
        'usable_stock': usable_stock,
        'expiry_status': facility.expiry_status,
        'has_expired_stock': facility.has_expired_stock,
        'average_daily_usage': daily_usage,
        'consumption_confidence': confidence,
        'consumption_confidence_label': metrics['confidence_label'],
        'event_count': metrics['event_count'],
        'observation_window_days': metrics['window_days'],
        'days_of_cover': days_of_cover,
        'days_of_cover_display': facility.days_of_cover_display,
        'lead_time_days': lead_time,
        'safety_buffer_days': safety_buffer,
        'reorder_buffer_days': reorder_buffer_days,
        'projected_depletion_date': projected_depletion_date,
        'is_critical_stockout_risk': is_critical,
        'is_low_stock_warning': is_warning,
    }
