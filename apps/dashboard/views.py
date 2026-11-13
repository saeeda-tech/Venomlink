"""
Web Dashboard views for VenomLink.
Designed for State Health Officials, Primary Healthcare Boards, and Facility Admins.
Follows strict public-service, operational design standards and RBAC controls.
"""

from django.shortcuts import render, get_object_or_404, redirect
from django.http import HttpResponseForbidden, Http404
from django.db.models import Q, Case, When
from django.utils import timezone
from django.contrib import messages
from datetime import timedelta
import json

from apps.facilities.models import Facility, Reporter, UserRole
from apps.facilities.permissions import (
    get_user_role,
    get_user_assigned_state,
    is_demo_mode,
    verify_facility_write_access,
    verify_reporter_pin_credentials,
)
from apps.facilities.throttling import (
    AnonSearchRateThrottle, PinAuthRateThrottle, rate_limited_response
)
from apps.inventory.models import InventoryTransaction
from apps.inventory.services import (
    record_stock_update,
    record_case_treatment,
    record_redistribution_transfer,
)
from apps.analytics.models import Alert, AlertSeverity, AlertStatus, AlertType
from apps.analytics.alert_engine import find_redistribution_recommendations
from apps.analytics.views import _check_alert_object_permission


def _resolve_effective_state(request) -> str:
    """Enforces assigned state for STATE_OFFICIAL, or returns requested state filter."""
    role = get_user_role(request.user)
    if role == UserRole.STATE_OFFICIAL:
        assigned = get_user_assigned_state(request.user)
        return assigned or ''
    return request.GET.get('state', '').strip()


def _resolve_period_days(request) -> int:
    period = request.GET.get('period', '30d').strip()
    return 7 if period == '7d' else 30


def overview_view(request):
    """
    Overview page — Primary command view for State Health Officials.
    Includes 4 restrained indicators, Leaflet operational map, and quick alerts summary.
    """
    state_filter = _resolve_effective_state(request)
    period_days = _resolve_period_days(request)

    facilities_qs = Facility.objects.filter(verified=True).prefetch_related('inventory_transactions', 'cases')
    if state_filter:
        facilities_qs = facilities_qs.filter(state__iexact=state_filter)

    facilities = list(facilities_qs)

    # Calculate restrained summary indicators
    total_facilities = len(facilities)
    total_stock_vials = sum(f.usable_stock for f in facilities)

    requiring_attention = sum(
        1 for f in facilities
        if f.stock_status in ('STOCK_OUT', 'CRITICAL', 'LOW_STOCK', 'LOW') or f.has_expired_stock
    )
    data_not_confirmed = sum(1 for f in facilities if f.freshness_status in ('STALE', 'UNCONFIRMED'))

    # Active alerts in scope
    alerts_qs = Alert.objects.filter(
        status__in=[AlertStatus.ACTIVE, AlertStatus.ACKNOWLEDGED]
    ).select_related('facility')
    if state_filter:
        alerts_qs = alerts_qs.filter(facility__state__iexact=state_filter)
    recent_alerts = alerts_qs.order_by('-created_at', '-id')[:5]

    # Map markers data payload
    map_facilities = []
    for f in facilities:
        metrics = f.get_consumption_metrics(days=period_days)
        doc_val = (
            0.0 if f.usable_stock == 0
            else (round(f.usable_stock / metrics['average_daily_usage'], 1) if metrics['average_daily_usage'] > 0 else None)
        )
        map_facilities.append({
            'id': f.id,
            'name': f.name,
            'facility_type': f.get_facility_type_display(),
            'state': f.state,
            'lga': f.lga,
            'lat': float(f.latitude),
            'lon': float(f.longitude),
            'stock': f.current_stock,
            'usable_stock': f.usable_stock,
            'latest_expiry': f.latest_expiry,
            'expiry_status': f.expiry_status,
            'expiry_status_label': f.expiry_status_label,
            'last_confirmed': f.freshness_label,
            'freshness_status': f.freshness_status,
            'status': f.stock_status,
            'status_label': f.stock_status_label,
            'color': f.stock_status_color,
            'phone': f.phone,
            'days_of_cover': doc_val,
            'days_of_cover_display': f.days_of_cover_display,
            'consumption_confidence': metrics['confidence_label'],
        })

    # Recent transactions audit within selected period window
    cutoff = timezone.now() - timedelta(days=period_days)
    tx_qs = InventoryTransaction.objects.filter(timestamp__gte=cutoff).select_related('facility', 'reporter')
    if state_filter:
        tx_qs = tx_qs.filter(facility__state__iexact=state_filter)
    recent_transactions = tx_qs.order_by('-timestamp', '-id')[:8]

    context = {
        'total_facilities': total_facilities,
        'total_stock_vials': total_stock_vials,
        'requiring_attention': requiring_attention,
        'data_not_confirmed': data_not_confirmed,
        'map_facilities_json': json.dumps(map_facilities),
        'recent_alerts': recent_alerts,
        'recent_transactions': recent_transactions,
        'state_filter': state_filter,
        'period_days': period_days,
    }
    return render(request, 'dashboard/overview.html', context)


def alerts_view(request):
    """
    Operational Alerts Table View.
    Columns: Severity | Facility | Alert | Current Status | Last Updated | Action
    Supports POST actions for acknowledging and resolving alerts by authorised officials.
    """
    state_filter = _resolve_effective_state(request)
    severity_filter = request.GET.get('severity', '').strip()
    type_filter = request.GET.get('type', '').strip()
    status_filter = request.GET.get('status', 'OPEN').strip().upper()

    if request.method == 'POST':
        alert_id = request.POST.get('alert_id')
        action = request.POST.get('action', '').strip()
        alert = get_object_or_404(Alert.objects.select_related('facility'), pk=alert_id)

        allowed, reason = _check_alert_object_permission(request.user, alert)
        if not allowed:
            messages.error(request, reason or "Unauthorised to modify alerts.")
            return HttpResponseForbidden(reason or "Unauthorised to modify alerts.")

        if action == 'acknowledge':
            alert.status = AlertStatus.ACKNOWLEDGED
            alert.save(update_fields=['status'])
            messages.success(request, f"Alert #{alert.id} for {alert.facility.name} acknowledged.")
        elif action == 'resolve':
            alert.status = AlertStatus.RESOLVED
            alert.resolved_at = timezone.now()
            alert.save(update_fields=['status', 'resolved_at'])
            messages.success(request, f"Alert #{alert.id} for {alert.facility.name} resolved.")
        return redirect(request.get_full_path())

    if status_filter == 'RESOLVED':
        alerts_qs = Alert.objects.filter(status=AlertStatus.RESOLVED).select_related('facility')
    else:
        alerts_qs = Alert.objects.filter(
            status__in=[AlertStatus.ACTIVE, AlertStatus.ACKNOWLEDGED]
        ).select_related('facility')

    if state_filter:
        alerts_qs = alerts_qs.filter(facility__state__iexact=state_filter)
    if severity_filter:
        alerts_qs = alerts_qs.filter(severity=severity_filter.upper())
    if type_filter:
        alerts_qs = alerts_qs.filter(alert_type=type_filter.upper())

    alerts = alerts_qs.order_by(
        Case(
            When(severity='CRITICAL', then=0),
            When(severity='WARNING', then=1),
            default=2
        ),
        '-created_at',
        '-id'
    )

    context = {
        'alerts': alerts,
        'state_filter': state_filter,
        'severity_filter': severity_filter,
        'type_filter': type_filter,
        'status_filter': status_filter,
        'severity_choices': AlertSeverity.choices,
        'type_choices': AlertType.choices,
    }
    return render(request, 'dashboard/alerts.html', context)


def facilities_view(request):
    """
    Searchable Facilities directory with filtering by LGA, Stock status, and Data freshness.
    Uses prefetched inventory_transactions and cases to prevent N+1 queries.
    Anonymous searches are rate limited per client IP.
    """
    limited = rate_limited_response(request, AnonSearchRateThrottle)
    if limited is not None:
        return limited
    state_filter = _resolve_effective_state(request)
    period_days = _resolve_period_days(request)
    lga_filter = request.GET.get('lga', '').strip()
    status_filter = request.GET.get('status', '').strip().upper()
    freshness_filter = request.GET.get('freshness', '').strip().upper()
    search_query = request.GET.get('q', '').strip()

    facilities_qs = Facility.objects.filter(verified=True).prefetch_related('inventory_transactions', 'cases')

    if state_filter:
        facilities_qs = facilities_qs.filter(state__iexact=state_filter)
    if lga_filter:
        facilities_qs = facilities_qs.filter(lga__iexact=lga_filter)
    if search_query:
        facilities_qs = facilities_qs.filter(
            Q(name__icontains=search_query) |
            Q(lga__icontains=search_query) |
            Q(phone__icontains=search_query)
        )

    facilities = list(facilities_qs)

    # Filter on computed properties (accepting both 'LOW' and 'LOW_STOCK' aliases)
    if status_filter:
        if status_filter in ('LOW', 'LOW_STOCK'):
            facilities = [f for f in facilities if f.stock_status in ('LOW', 'LOW_STOCK')]
        elif status_filter == 'CRITICAL':
            facilities = [f for f in facilities if f.stock_status in ('CRITICAL', 'STOCK_OUT')]
        else:
            facilities = [f for f in facilities if f.stock_status == status_filter]
    if freshness_filter:
        facilities = [f for f in facilities if f.freshness_status == freshness_filter]

    for f in facilities:
        metrics = f.get_consumption_metrics(days=period_days)
        f.period_daily_usage = metrics['average_daily_usage']
        f.period_confidence_label = metrics['confidence_label']
        if f.usable_stock == 0:
            f.period_doc = "0.0 days"
        elif metrics['average_daily_usage'] > 0:
            f.period_doc = f"{round(f.usable_stock / metrics['average_daily_usage'], 1)} days"
        else:
            f.period_doc = "Unavailable (insufficient history)"

    available_lgas = (
        Facility.objects.filter(state__iexact=state_filter) if state_filter
        else Facility.objects.all()
    ).values_list('lga', flat=True).distinct().order_by('lga')

    context = {
        'facilities': facilities,
        'state_filter': state_filter,
        'lga_filter': lga_filter,
        'status_filter': status_filter,
        'freshness_filter': freshness_filter,
        'search_query': search_query,
        'available_lgas': available_lgas,
        'period_days': period_days,
    }
    return render(request, 'dashboard/facilities.html', context)


def facility_detail_view(request, pk):
    """
    Facility Detail Page.
    Enforces state isolation for STATE_OFFICIAL and requires authenticated RBAC
    or verified Reporter Phone + Staff PIN for inventory modifications.
    """
    facility = get_object_or_404(
        Facility.objects.prefetch_related('inventory_transactions', 'cases', 'alerts', 'reporters'),
        pk=pk
    )

    role = get_user_role(request.user)
    if role == UserRole.STATE_OFFICIAL:
        assigned_state = get_user_assigned_state(request.user)
        if not assigned_state or facility.state.lower() != assigned_state.lower():
            return HttpResponseForbidden("State officials may only access facilities in their assigned state.")

    if request.method == 'POST':
        action = request.POST.get('action')
        reporter_phone = request.POST.get('reporter_phone', '').strip()
        staff_pin = request.POST.get('staff_pin', '').strip()

        reporter = None
        allowed = verify_facility_write_access(request.user, facility)
        if not allowed:
            if reporter_phone and staff_pin:
                limited = rate_limited_response(request, PinAuthRateThrottle)
                if limited is not None:
                    return limited
                try:
                    reporter = verify_reporter_pin_credentials(
                        phone_number=reporter_phone,
                        pin=staff_pin,
                        target_facility=facility
                    )
                    allowed = True
                except Exception as pin_exc:
                    err_msg = str(getattr(pin_exc, 'detail', pin_exc))
                    messages.error(request, err_msg)
                    return HttpResponseForbidden(err_msg)
            else:
                err_msg = "Authentication required: Sign in as an authorised official or provide registered Reporter Phone and Staff PIN."
                messages.error(request, err_msg)
                return HttpResponseForbidden(err_msg)

        if action == 'update_stock':
            qty = request.POST.get('quantity')
            expiry = request.POST.get('expiry_date', '').strip()
            notes = request.POST.get('notes', '').strip()
            try:
                quantity = int(qty)
                record_stock_update(
                    facility=facility,
                    quantity=quantity,
                    expiry_date=expiry,
                    reporter=reporter,
                    notes=notes or "Updated via Web Management Dashboard"
                )
                messages.success(request, f"Stock updated successfully to {quantity} vials.")
                return redirect('dashboard:facility-detail', pk=facility.id)
            except (ValueError, TypeError) as e:
                messages.error(request, str(e))

        elif action == 'report_case':
            vials = request.POST.get('vials_used')
            outcome = request.POST.get('outcome', 'RECOVERED')
            notes = request.POST.get('notes', '').strip()
            try:
                vials_used = int(vials)
                record_case_treatment(
                    facility=facility,
                    vials_used=vials_used,
                    outcome=outcome,
                    reporter=reporter,
                    notes=notes or "Reported via Web Management Dashboard"
                )
                messages.success(request, f"Snakebite case reported. {vials_used} vial(s) deducted from stock.")
                return redirect('dashboard:facility-detail', pk=facility.id)
            except (ValueError, TypeError) as e:
                messages.error(request, str(e))

    transactions = list(facility.inventory_transactions.order_by('-timestamp', '-id')[:20])
    cases = list(facility.cases.order_by('-date', '-id')[:15])
    active_alerts = facility.alerts.filter(status__in=[AlertStatus.ACTIVE, AlertStatus.ACKNOWLEDGED])
    primary_reporter = facility.reporters.filter(active=True).first()

    # Build chronological timeline of recent activity
    timeline = []
    for tx in transactions:
        timeline.append({
            'date': tx.timestamp,
            'type': 'TRANSACTION',
            'title': f"{tx.get_transaction_type_display()}: {tx.quantity:+d} vials",
            'detail': f"Resulting stock: {tx.stock_after} vials (Expiry: {tx.expiry_date or 'N/A'}). {tx.notes}",
            'badge': 'info'
        })
    for c in cases:
        timeline.append({
            'date': timezone.make_aware(timezone.datetime.combine(c.date, timezone.datetime.min.time())),
            'type': 'CASE',
            'title': f"Snakebite case treated: {c.vials_used} vial(s)",
            'detail': f"Outcome: {c.get_outcome_display()}. {c.notes}",
            'badge': 'case'
        })
    timeline.sort(key=lambda x: x['date'], reverse=True)

    chart_points = []
    for tx in reversed(transactions[:10]):
        chart_points.append({
            'label': tx.timestamp.strftime('%d %b %H:%M'),
            'stock': tx.stock_after
        })

    context = {
        'facility': facility,
        'transactions': transactions,
        'cases': cases,
        'timeline': timeline[:15],
        'chart_points_json': json.dumps(chart_points),
        'active_alerts': active_alerts,
        'primary_reporter': primary_reporter,
    }
    return render(request, 'dashboard/facility_detail.html', context)


def redistribution_view(request):
    """
    Potential Redistribution suggestions page.
    Requires SYSTEM_ADMIN or STATE_OFFICIAL authorization to execute transfers.
    """
    state_filter = _resolve_effective_state(request)

    if request.method == 'POST':
        role = get_user_role(request.user)
        if role not in (UserRole.SYSTEM_ADMIN, UserRole.STATE_OFFICIAL):
            err_msg = "Unauthorised: Only State Health Officials or System Administrators may execute redistribution transfers."
            messages.error(request, err_msg)
            return HttpResponseForbidden(err_msg)

        src_id = request.POST.get('source_facility_id')
        tgt_id = request.POST.get('target_facility_id')
        qty = request.POST.get('quantity')
        notes = request.POST.get('notes', '').strip()

        try:
            source = get_object_or_404(Facility, pk=src_id)
            target = get_object_or_404(Facility, pk=tgt_id)

            if role == UserRole.STATE_OFFICIAL:
                assigned_state = get_user_assigned_state(request.user)
                if not assigned_state or (
                    source.state.lower() != assigned_state.lower()
                    and target.state.lower() != assigned_state.lower()
                ):
                    return HttpResponseForbidden("State officials may only execute transfers involving their assigned state.")

            quantity = int(qty)
            record_redistribution_transfer(
                source_facility=source,
                target_facility=target,
                quantity=quantity,
                notes=notes or "Authorized via State Redistribution Board"
            )
            messages.success(
                request,
                f"Redistribution transfer completed: {quantity} vials transferred from {source.name} to {target.name}."
            )
            return redirect('dashboard:redistribution')
        except (ValueError, TypeError) as e:
            messages.error(request, str(e))

    recommendations = find_redistribution_recommendations(state=state_filter)

    context = {
        'recommendations': recommendations,
        'state_filter': state_filter,
    }
    return render(request, 'dashboard/redistribution.html', context)


def ussd_simulator_view(request):
    """
    Interactive USSD Handset Simulator (demo mode only; 404 otherwise).
    """
    if not is_demo_mode():
        raise Http404("USSD simulator is disabled outside demo mode.")
    facilities = Facility.objects.filter(verified=True).order_by('name')
    reporters = Reporter.objects.filter(active=True).select_related('facility')

    context = {
        'facilities': facilities,
        'reporters': reporters,
    }
    return render(request, 'dashboard/ussd_simulator.html', context)


def demo_runner_view(request):
    """
    Interactive 90-Second Guided Demo Flow (demo mode only; 404 otherwise).
    The page embeds demo credentials, so it must never be reachable in a pilot/production deployment.
    """
    if not is_demo_mode():
        raise Http404("Demo runner is disabled outside demo mode.")
    facilities = Facility.objects.filter(verified=True).prefetch_related('reporters')
    kaltungo = facilities.filter(name__icontains='kaltungo').first() or facilities.first()
    demo_reporter = kaltungo.reporters.filter(active=True).first() if kaltungo else None

    context = {
        'demo_facility': kaltungo,
        'demo_reporter': demo_reporter,
        'facilities': facilities,
    }
    return render(request, 'dashboard/demo_runner.html', context)
