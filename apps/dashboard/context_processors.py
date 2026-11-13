from django.utils import timezone
from apps.facilities.models import Facility, UserRole
from apps.facilities.permissions import (
    get_user_role, get_user_assigned_state, get_user_facility_id, is_demo_mode
)
from apps.analytics.models import Alert, AlertStatus


def venomlink_global_context(request):
    """Provides common context variables for VenomLink dashboard templates."""
    role = get_user_role(request.user)
    assigned_state = get_user_assigned_state(request.user) if role == UserRole.STATE_OFFICIAL else ''
    fac_id = get_user_facility_id(request.user) if role in (UserRole.FACILITY_ADMIN, UserRole.FACILITY_REPORTER) else None

    available_states_qs = Facility.objects.values_list('state', flat=True).distinct().order_by('state')
    if role == UserRole.STATE_OFFICIAL:
        available_states = [assigned_state] if assigned_state else []
        selected_state = assigned_state
    elif role in (UserRole.FACILITY_ADMIN, UserRole.FACILITY_REPORTER):
        fac_state = (
            Facility.objects.filter(pk=fac_id).values_list('state', flat=True).first()
            if fac_id else ''
        )
        available_states = [fac_state] if fac_state else []
        selected_state = fac_state or ''
    else:
        available_states = list(available_states_qs) or ['Gombe', 'Plateau', 'Kaduna']
        selected_state = request.GET.get('state', '').strip()

    selected_period = request.GET.get('period', '30d').strip()
    if selected_period not in ('30d', '7d'):
        selected_period = '30d'

    alerts_qs = Alert.objects.filter(status__in=[AlertStatus.ACTIVE, AlertStatus.ACKNOWLEDGED])
    if role == UserRole.STATE_OFFICIAL:
        alerts_qs = alerts_qs.filter(facility__state__iexact=assigned_state) if assigned_state else alerts_qs.none()
    elif role in (UserRole.FACILITY_ADMIN, UserRole.FACILITY_REPORTER):
        alerts_qs = alerts_qs.filter(facility_id=fac_id) if fac_id else alerts_qs.none()
    elif selected_state:
        alerts_qs = alerts_qs.filter(facility__state__iexact=selected_state)

    active_alerts_count = alerts_qs.count()
    critical_alerts_count = alerts_qs.filter(severity='CRITICAL').count()

    return {
        'global_states': available_states,
        'global_active_alerts_count': active_alerts_count,
        'global_critical_alerts_count': critical_alerts_count,
        'current_time_str': timezone.now().strftime('%d %b %Y, %I:%M %p'),
        'selected_state': selected_state,
        'selected_period': selected_period,
        'user_role': role,
        'user_assigned_state': assigned_state,
        'demo_mode': is_demo_mode(),
        'demo_mode_notice': (
            "Prototype Demonstration Mode (USPF Changemaker Challenge 2026) — "
            "Public dashboard exploration is enabled for evaluation only. "
            "All facility records and stock figures shown are simulated and must not be used for clinical decisions."
        ),
    }
