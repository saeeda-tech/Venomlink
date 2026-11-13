from rest_framework import generics, views, status, permissions
from rest_framework.response import Response
from django.shortcuts import get_object_or_404
from django.utils import timezone
from .models import Alert, AlertStatus
from .serializers import AlertSerializer
from .alert_engine import find_redistribution_recommendations, evaluate_all_alerts
from apps.facilities.models import UserRole
from apps.facilities.permissions import (
    DemoModeOrOperationalRole,
    DemoModeOrStateOrSystemOfficial,
    IsAuthorizedForAlertManagement,
    get_user_role,
    get_user_assigned_state,
    get_user_facility_id,
    is_demo_mode,
)


class AlertListAPIView(generics.ListAPIView):
    serializer_class = AlertSerializer
    permission_classes = [DemoModeOrOperationalRole]

    def get_queryset(self):
        status_param = self.request.query_params.get('status')
        if status_param:
            queryset = Alert.objects.filter(status=status_param.upper()).select_related('facility')
        else:
            queryset = Alert.objects.filter(
                status__in=[AlertStatus.ACTIVE, AlertStatus.ACKNOWLEDGED]
            ).select_related('facility')

        role = get_user_role(self.request.user)
        state = self.request.query_params.get('state')
        severity = self.request.query_params.get('severity')
        alert_type = self.request.query_params.get('alert_type')

        # Enforce strict fail-closed isolation by role
        if role == UserRole.STATE_OFFICIAL:
            assigned_state = get_user_assigned_state(self.request.user)
            if not assigned_state:
                return queryset.none()
            if state and state.strip().lower() != assigned_state.lower():
                return queryset.none()
            queryset = queryset.filter(facility__state__iexact=assigned_state)
        elif role in (UserRole.FACILITY_ADMIN, UserRole.FACILITY_REPORTER):
            fac_id = get_user_facility_id(self.request.user)
            if not fac_id:
                return queryset.none()
            queryset = queryset.filter(facility_id=fac_id)
        elif role == UserRole.PUBLIC_USER and not is_demo_mode():
            return queryset.none()
        elif state:
            queryset = queryset.filter(facility__state__iexact=state)

        if severity:
            queryset = queryset.filter(severity=severity.upper())
        if alert_type:
            queryset = queryset.filter(alert_type=alert_type.upper())
        return queryset


def _check_alert_object_permission(user, alert: Alert):
    """Verifies state/facility scope for alert acknowledgement or resolution."""
    role = get_user_role(user)
    if role == UserRole.SYSTEM_ADMIN:
        return True, ""
    if role == UserRole.STATE_OFFICIAL:
        assigned_state = get_user_assigned_state(user)
        if assigned_state and alert.facility.state.lower() == assigned_state.lower():
            return True, ""
        return False, "State officials may only manage alerts within their assigned state."
    if role == UserRole.FACILITY_ADMIN:
        fac_id = get_user_facility_id(user)
        if fac_id and fac_id == alert.facility_id:
            return True, ""
        return False, "Facility administrators may only manage alerts for their own facility."
    return False, "Unauthorised role for alert management."


class AlertAcknowledgeAPIView(views.APIView):
    permission_classes = [IsAuthorizedForAlertManagement]

    def post(self, request, pk):
        alert = get_object_or_404(Alert.objects.select_related('facility'), pk=pk)
        allowed, reason = _check_alert_object_permission(request.user, alert)
        if not allowed:
            return Response({'error': reason}, status=status.HTTP_403_FORBIDDEN)

        alert.status = AlertStatus.ACKNOWLEDGED
        alert.save(update_fields=['status'])
        return Response({'status': 'acknowledged', 'alert_id': alert.id})


class AlertResolveAPIView(views.APIView):
    permission_classes = [IsAuthorizedForAlertManagement]

    def post(self, request, pk):
        alert = get_object_or_404(Alert.objects.select_related('facility'), pk=pk)
        allowed, reason = _check_alert_object_permission(request.user, alert)
        if not allowed:
            return Response({'error': reason}, status=status.HTTP_403_FORBIDDEN)

        alert.status = AlertStatus.RESOLVED
        alert.resolved_at = timezone.now()
        alert.save(update_fields=['status', 'resolved_at'])
        return Response({'status': 'resolved', 'alert_id': alert.id})


class RedistributionSuggestionsAPIView(views.APIView):
    permission_classes = [DemoModeOrStateOrSystemOfficial]

    def get(self, request):
        state = request.query_params.get('state')
        role = get_user_role(request.user)
        if role == UserRole.STATE_OFFICIAL:
            assigned_state = get_user_assigned_state(request.user)
            if not assigned_state:
                return Response({
                    'state': state or '',
                    'total_recommendations': 0,
                    'recommendations': []
                })
            if state and state.strip().lower() != assigned_state.lower():
                return Response({
                    'state': state,
                    'total_recommendations': 0,
                    'recommendations': []
                })
            state = assigned_state

        recommendations = find_redistribution_recommendations(state=state)
        return Response({
            'state': state or 'All States',
            'total_recommendations': len(recommendations),
            'recommendations': recommendations
        })


class EvaluateAlertsTriggerAPIView(views.APIView):
    permission_classes = [IsAuthorizedForAlertManagement]

    def post(self, request):
        evaluate_all_alerts()
        active_count = Alert.objects.filter(status=AlertStatus.ACTIVE).count()
        return Response({
            'status': 'success',
            'message': 'Alert evaluation completed across all facilities.',
            'active_alerts': active_count
        })
