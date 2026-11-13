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
from django.utils import timezone
from apps.facilities.models import Facility, ConsumptionConfidence
from .thresholds import classify_facility_stock, get_stock_threshold_config


def calculate_facility_forecast(facility: Facility, window_days: int = 30) -> dict:
    """
    Computes stock intelligence metrics for a health facility without arbitrary fallbacks.
    Status flags are derived strictly via the shared threshold classifier.
    """
    tcfg = get_stock_threshold_config()
    stock_out_thresh = tcfg['stock_out_threshold']

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

    classification = classify_facility_stock(
        usable_stock=usable_stock,
        has_expired_stock=facility.has_expired_stock,
        is_stale=facility.is_stale,
        days_of_cover=days_of_cover,
    )
    stock_status = classification['stock_status']
    is_critical = stock_status in ('STOCK_OUT', 'CRITICAL')
    is_warning = stock_status == 'LOW_STOCK'

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
        'stock_status': stock_status,
        'below_min_treatment_course': classification['below_min_treatment_course'],
        'min_treatment_course_vials': classification['min_treatment_course_vials'],
        'critical_days_of_cover_threshold': classification['critical_days_of_cover_threshold'],
        'warning_days_of_cover_threshold': classification['warning_days_of_cover_threshold'],
        'is_critical_stockout_risk': is_critical,
        'is_low_stock_warning': is_warning,
    }
