"""
Single source of truth for VenomLink stock status classification and alert thresholds.

All status checks across Facility properties, Days-of-Cover forecasting, alert
generation, dashboard KPIs, map markers, and redistribution matching MUST call
`classify_facility_stock()` from this module so thresholds never diverge.

Rules (configurable via settings.VENOMLINK_CONFIG):
- Stock-out: usable_stock <= STOCK_OUT_THRESHOLD (default 0) or batch expired
- Critical:  usable_stock > 0 and days_of_cover <= CRITICAL_DAYS_OF_COVER_THRESHOLD (default 5.0)
- Low Stock: days_of_cover > 5.0 and days_of_cover <= WARNING_DAYS_OF_COVER_THRESHOLD (default 7.0)
- Normal:    days_of_cover > 7.0 (or no history and confirmed within freshness window)
- Stale:     UNCONFIRMED if confirmed > STALE_DATA_THRESHOLD_HOURS ago and not already Critical/Low/Stock-out

Treatment-Course Adequacy (independent of Days of Cover):
- `below_min_treatment_course` is True when 0 < usable_stock < MIN_TREATMENT_COURSE_VIALS.
- Note: MIN_TREATMENT_COURSE_VIALS is a configurable operational planning assumption
  that requires local clinical validation and never alters days_of_cover classification.
"""

from typing import Any, Dict, Optional
from django.conf import settings


def get_stock_threshold_config() -> Dict[str, Any]:
    cfg = getattr(settings, 'VENOMLINK_CONFIG', {})
    return {
        'stock_out_threshold': int(cfg.get('STOCK_OUT_THRESHOLD', 0)),
        'critical_days_of_cover': float(cfg.get('CRITICAL_DAYS_OF_COVER_THRESHOLD', 5.0)),
        'warning_days_of_cover': float(cfg.get('WARNING_DAYS_OF_COVER_THRESHOLD', 7.0)),
        'min_treatment_course_vials': int(cfg.get('MIN_TREATMENT_COURSE_VIALS', 5)),
        'expiry_warning_days': int(cfg.get('EXPIRY_WARNING_DAYS', 60)),
    }


def classify_facility_stock(
    usable_stock: int,
    has_expired_stock: bool,
    is_stale: bool,
    days_of_cover: Optional[float],
) -> Dict[str, Any]:
    """
    Deterministically classifies facility stock status and independent minimum
    treatment-course adequacy using settings-driven thresholds.
    """
    tcfg = get_stock_threshold_config()
    stock_out_thresh = tcfg['stock_out_threshold']
    critical_doc = tcfg['critical_days_of_cover']
    warning_doc = tcfg['warning_days_of_cover']
    min_course_vials = tcfg['min_treatment_course_vials']

    below_min_treatment_course = bool(0 < usable_stock < min_course_vials)

    if has_expired_stock or usable_stock <= stock_out_thresh:
        stock_status = 'STOCK_OUT'
    elif days_of_cover is not None:
        if days_of_cover <= critical_doc:
            stock_status = 'CRITICAL'
        elif days_of_cover <= warning_doc:
            stock_status = 'LOW_STOCK'
        elif is_stale:
            stock_status = 'UNCONFIRMED'
        else:
            stock_status = 'AVAILABLE'
    else:
        stock_status = 'UNCONFIRMED' if is_stale else 'AVAILABLE'

    return {
        'stock_status': stock_status,
        'below_min_treatment_course': below_min_treatment_course,
        'min_treatment_course_vials': min_course_vials,
        'critical_days_of_cover_threshold': critical_doc,
        'warning_days_of_cover_threshold': warning_doc,
        'stock_out_threshold': stock_out_thresh,
    }
