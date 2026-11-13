from rest_framework import generics, permissions
from .models import SmsLog
from .serializers import SmsLogSerializer
from apps.facilities.models import UserRole
from apps.facilities.permissions import (
    DemoModeOrAuthenticated, get_user_role, get_user_assigned_state, get_user_facility_id
)


class SmsLogListAPIView(generics.ListAPIView):
    """
    SMS delivery log. Anonymous read is allowed only in demo mode (for the USSD simulator
    inbox preview). Otherwise authenticated and scoped; recipient numbers are masked for
    anyone other than SYSTEM_ADMIN / STATE_OFFICIAL.
    """
    serializer_class = SmsLogSerializer
    permission_classes = [DemoModeOrAuthenticated]

    def get_queryset(self):
        qs = SmsLog.objects.all().select_related('facility').order_by('-timestamp', '-id')
        role = get_user_role(self.request.user)
        if role == UserRole.STATE_OFFICIAL:
            assigned_state = get_user_assigned_state(self.request.user)
            if not assigned_state:
                return qs.none()
            qs = qs.filter(facility__state__iexact=assigned_state)
        elif role in (UserRole.FACILITY_ADMIN, UserRole.FACILITY_REPORTER):
            fac_id = get_user_facility_id(self.request.user)
            if not fac_id:
                return qs.none()
            qs = qs.filter(facility_id=fac_id)
        elif role == UserRole.PUBLIC_USER and self.request.user.is_authenticated:
            return qs.none()
        return qs[:100]
