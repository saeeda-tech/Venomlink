from rest_framework import generics, views, permissions
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response
from django.db.models import Count
from .models import Facility, Reporter, UserRole
from .serializers import FacilitySerializer, ReporterSerializer
from .permissions import (
    IsSystemOrStateAdminOrReadOnly,
    get_user_role,
    get_user_assigned_state,
    get_user_facility_id,
)
from .throttling import AnonSearchRateThrottle

# Fields a FACILITY_ADMIN may change on their own facility record. Identity, location,
# state/LGA and the `verified` flag are controlled by SYSTEM_ADMIN / STATE_OFFICIAL only.
FACILITY_ADMIN_EDITABLE_FIELDS = {'phone', 'resupply_lead_time_days', 'safety_buffer_days'}


def _can_see_unverified(user) -> bool:
    return get_user_role(user) in (UserRole.SYSTEM_ADMIN, UserRole.STATE_OFFICIAL)


class FacilityListAPIView(generics.ListCreateAPIView):
    serializer_class = FacilitySerializer
    permission_classes = [IsSystemOrStateAdminOrReadOnly]
    throttle_classes = [AnonSearchRateThrottle]

    def get_queryset(self):
        queryset = Facility.objects.all().prefetch_related('inventory_transactions', 'cases')
        if not _can_see_unverified(self.request.user):
            queryset = queryset.filter(verified=True)
        role = get_user_role(self.request.user)
        assigned_state = get_user_assigned_state(self.request.user)

        # Enforce state-level data isolation for STATE_OFFICIAL
        if role == UserRole.STATE_OFFICIAL and assigned_state:
            requested_state = self.request.query_params.get('state')
            if requested_state and requested_state.strip().lower() != assigned_state.lower():
                raise PermissionDenied(f"State official access is restricted to {assigned_state} State.")
            queryset = queryset.filter(state__iexact=assigned_state)
        else:
            state = self.request.query_params.get('state')
            if state:
                queryset = queryset.filter(state__iexact=state)

        lga = self.request.query_params.get('lga')
        status_filter = self.request.query_params.get('status')
        if lga:
            queryset = queryset.filter(lga__iexact=lga)

        facilities = list(queryset)
        if status_filter:
            facilities = [f for f in facilities if f.stock_status.lower() == status_filter.lower()]
        return facilities

    def perform_create(self, serializer):
        role = get_user_role(self.request.user)
        if role == UserRole.FACILITY_ADMIN:
            raise PermissionDenied("Facility administrators cannot register new facilities.")
        if role == UserRole.STATE_OFFICIAL:
            assigned_state = get_user_assigned_state(self.request.user)
            target_state = serializer.validated_data.get('state', '')
            if not assigned_state or assigned_state.lower() != target_state.strip().lower():
                raise PermissionDenied(f"State officials may only register facilities within {assigned_state} State.")
        serializer.save()


class FacilityDetailAPIView(generics.RetrieveUpdateAPIView):
    serializer_class = FacilitySerializer
    permission_classes = [IsSystemOrStateAdminOrReadOnly]
    throttle_classes = [AnonSearchRateThrottle]

    def get_queryset(self):
        queryset = Facility.objects.all().prefetch_related('inventory_transactions', 'cases')
        if not _can_see_unverified(self.request.user):
            queryset = queryset.filter(verified=True)
        return queryset

    def perform_update(self, serializer):
        role = get_user_role(self.request.user)
        if role == UserRole.FACILITY_ADMIN:
            forbidden = set(serializer.validated_data.keys()) - FACILITY_ADMIN_EDITABLE_FIELDS
            if forbidden:
                raise PermissionDenied(
                    "Facility administrators may only update: "
                    + ", ".join(sorted(FACILITY_ADMIN_EDITABLE_FIELDS)) + "."
                )
        serializer.save()


class StateListAPIView(views.APIView):
    permission_classes = [permissions.AllowAny]

    def get(self, request):
        queryset = Facility.objects.all()
        role = get_user_role(request.user)
        assigned_state = get_user_assigned_state(request.user)
        if role == UserRole.STATE_OFFICIAL and assigned_state:
            queryset = queryset.filter(state__iexact=assigned_state)
        states = (
            queryset.values('state')
            .annotate(facility_count=Count('id'))
            .order_by('state')
        )
        return Response(states)


class LgaListAPIView(views.APIView):
    permission_classes = [permissions.AllowAny]

    def get(self, request):
        state = request.query_params.get('state')
        queryset = Facility.objects.all()
        role = get_user_role(request.user)
        assigned_state = get_user_assigned_state(request.user)
        if role == UserRole.STATE_OFFICIAL and assigned_state:
            queryset = queryset.filter(state__iexact=assigned_state)
        elif state:
            queryset = queryset.filter(state__iexact=state)
        lgas = (
            queryset.values('state', 'lga')
            .annotate(facility_count=Count('id'))
            .order_by('state', 'lga')
        )
        return Response(lgas)


class ReporterListCreateAPIView(generics.ListCreateAPIView):
    serializer_class = ReporterSerializer
    permission_classes = [IsSystemOrStateAdminOrReadOnly]

    def get_queryset(self):
        queryset = Reporter.objects.all().select_related('facility')
        role = get_user_role(self.request.user)
        if role == UserRole.PUBLIC_USER:
            raise PermissionDenied("Authentication required to list facility reporters.")
        if role == UserRole.STATE_OFFICIAL:
            assigned_state = get_user_assigned_state(self.request.user)
            if assigned_state:
                queryset = queryset.filter(facility__state__iexact=assigned_state)
        elif role in (UserRole.FACILITY_ADMIN, UserRole.FACILITY_REPORTER):
            fac_id = get_user_facility_id(self.request.user)
            queryset = queryset.filter(facility_id=fac_id)
        return queryset

    def perform_create(self, serializer):
        role = get_user_role(self.request.user)
        target_facility = serializer.validated_data['facility']
        if role == UserRole.STATE_OFFICIAL:
            assigned_state = get_user_assigned_state(self.request.user)
            if not assigned_state or assigned_state.lower() != target_facility.state.lower():
                raise PermissionDenied("Cannot register a reporter outside your assigned state.")
        elif role == UserRole.FACILITY_ADMIN:
            fac_id = get_user_facility_id(self.request.user)
            if not fac_id or fac_id != target_facility.id:
                raise PermissionDenied("Facility administrators can only register reporters for their own facility.")
        serializer.save()
