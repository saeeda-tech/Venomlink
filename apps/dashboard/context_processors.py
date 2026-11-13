from django.utils import timezone
from apps.facilities.models import Facility, UserRole
from apps.facilities.permissions import get_user_role, get_user_assigned_state, is_demo_mode
from apps.analytics.models import Alert, AlertStatus


def venomlink_global_context(request):
    """Provides common context variables for government dashboard templates."""
    role = get_user_role(request.user)
    assigned_state = get_user_assigned_state(request.user) if role == UserRole.STATE_OFFICIAL else ''

    available_states_qs = Facility.objects.values_list('state', flat=True).distinct().order_by('state')
    if assigned_state:
        available_states = [assigned_state]
        selected_state = assigned_state
    else:
        available_states = list(available_states_qs) or ['Gombe', 'Plateau', 'Kaduna']
        selected_state = request.GET.get('state', '').strip()

    selected_period = request.GET.get('period', '30d').strip()
    if selected_period not in ('30d', '7d'):
        selected_period = '30d'

    alerts_qs = Alert.objects.filter(status__in=[AlertStatus.ACTIVE, AlertStatus.ACKNOWLEDGED])
    if selected_state:
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
        'demo_mode_notice': "Demonstration environment. Stock information shown is simulated and must not be used for clinical decisions.",
    }
