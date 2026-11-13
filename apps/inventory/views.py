from rest_framework import views, generics, status, permissions
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response
from django.shortcuts import get_object_or_404

from apps.facilities.models import Facility, Reporter, UserRole
from apps.facilities.permissions import (
    IsAuthorizedForTransferWrite,
    IsAuthenticatedWithOperationalRole,
    get_user_role,
    get_user_assigned_state,
    get_user_facility_id,
    verify_facility_write_access,
    verify_reporter_pin_credentials,
)
from apps.facilities.throttling import PinAuthRateThrottle
from apps.notifications.sms_gateway import normalise_nigerian_phone
from .models import InventoryTransaction, SnakebiteCase
from .serializers import (
    InventoryTransactionSerializer, SnakebiteCaseSerializer,
    StockUpdateRequestSerializer, CaseReportRequestSerializer,
    RedistributionRequestSerializer
)
from .services import record_stock_update, record_case_treatment, record_redistribution_transfer


def _scope_audit_queryset(request, queryset):
    """
    Audit data (ledger / case registry) identifies staff and clinical activity, so it is
    restricted to operational roles and scoped: STATE_OFFICIAL -> own state,
    FACILITY_ADMIN / FACILITY_REPORTER -> own facility, SYSTEM_ADMIN -> everything.
    A scoped role with no assigned state/facility sees nothing (fail closed).
    """
    role = get_user_role(request.user)
    if role == UserRole.STATE_OFFICIAL:
        assigned_state = get_user_assigned_state(request.user)
        if not assigned_state:
            return queryset.none()
        return queryset.filter(facility__state__iexact=assigned_state)
    if role in (UserRole.FACILITY_ADMIN, UserRole.FACILITY_REPORTER):
        fac_id = get_user_facility_id(request.user)
        if not fac_id:
            return queryset.none()
        return queryset.filter(facility_id=fac_id)
    if role == UserRole.SYSTEM_ADMIN:
        return queryset
    return queryset.none()


class InventoryTransactionListAPIView(generics.ListAPIView):
    serializer_class = InventoryTransactionSerializer
    permission_classes = [IsAuthenticatedWithOperationalRole]

    def get_queryset(self):
        queryset = _scope_audit_queryset(
            self.request,
            InventoryTransaction.objects.all().select_related('facility', 'reporter')
        )
        facility_id = self.request.query_params.get('facility_id')
        tx_type = self.request.query_params.get('transaction_type')
        if facility_id:
            queryset = queryset.filter(facility_id=facility_id)
        if tx_type:
            queryset = queryset.filter(transaction_type=tx_type)
        return queryset[:100]


class SnakebiteCaseListAPIView(generics.ListAPIView):
    serializer_class = SnakebiteCaseSerializer
    permission_classes = [IsAuthenticatedWithOperationalRole]

    def get_queryset(self):
        queryset = _scope_audit_queryset(
            self.request,
            SnakebiteCase.objects.all().select_related('facility', 'reporter')
        )
        facility_id = self.request.query_params.get('facility_id')
        if facility_id:
            queryset = queryset.filter(facility_id=facility_id)
        return queryset[:100]


class StockUpdateAPIView(views.APIView):
    """
    Secured facility stock update endpoint.
    Requires either an authenticated user with write permission for the target facility,
    or verified facility reporter credentials (phone_number + pin) bound to that facility.
    Unauthenticated PIN attempts are IP-throttled and subject to per-reporter lockout.
    """
    permission_classes = [permissions.AllowAny]
    throttle_classes = [PinAuthRateThrottle]

    def post(self, request):
        serializer = StockUpdateRequestSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        facility = get_object_or_404(Facility, pk=serializer.validated_data['facility_id'])
        phone = serializer.validated_data.get('phone_number', '').strip()
        pin = serializer.validated_data.get('pin', '').strip()

        reporter = None
        if request.user and request.user.is_authenticated:
            if not verify_facility_write_access(request.user, facility):
                raise PermissionDenied("You are not authorized to update stock for this facility.")
            if phone:
                norm_phone = normalise_nigerian_phone(phone)
                reporter = (
                    Reporter.objects.filter(phone_number=norm_phone, active=True).first()
                    or Reporter.objects.filter(phone_number=phone, active=True).first()
                )
                if not reporter or reporter.facility_id != facility.id:
                    raise PermissionDenied("Specified reporter is not registered to this facility.")
            else:
                reporter = getattr(request.user, 'reporter_record', None)
        else:
            # Unauthenticated request must supply valid reporter phone + PIN for this facility
            reporter = verify_reporter_pin_credentials(phone, pin, facility)

        try:
            tx = record_stock_update(
                facility=facility,
                quantity=serializer.validated_data['quantity'],
                expiry_date=serializer.validated_data.get('expiry_date', ''),
                reporter=reporter,
                notes=serializer.validated_data.get('notes', '')
            )
            return Response(
                InventoryTransactionSerializer(tx).data,
                status=status.HTTP_201_CREATED
            )
        except ValueError as e:
            return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)


class CaseReportAPIView(views.APIView):
    """
    Secured snakebite clinical case reporting endpoint.
    Requires authenticated facility/state authority or verified reporter phone_number + pin.
    """
    permission_classes = [permissions.AllowAny]
    throttle_classes = [PinAuthRateThrottle]

    def post(self, request):
        serializer = CaseReportRequestSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        facility = get_object_or_404(Facility, pk=serializer.validated_data['facility_id'])
        phone = serializer.validated_data.get('phone_number', '').strip()
        pin = serializer.validated_data.get('pin', '').strip()

        reporter = None
        if request.user and request.user.is_authenticated:
            if not verify_facility_write_access(request.user, facility):
                raise PermissionDenied("You are not authorized to report cases for this facility.")
            if phone:
                norm_phone = normalise_nigerian_phone(phone)
                reporter = (
                    Reporter.objects.filter(phone_number=norm_phone, active=True).first()
                    or Reporter.objects.filter(phone_number=phone, active=True).first()
                )
                if not reporter or reporter.facility_id != facility.id:
                    raise PermissionDenied("Specified reporter is not registered to this facility.")
            else:
                reporter = getattr(request.user, 'reporter_record', None)
        else:
            reporter = verify_reporter_pin_credentials(phone, pin, facility)

        try:
            case = record_case_treatment(
                facility=facility,
                vials_used=serializer.validated_data['vials_used'],
                outcome=serializer.validated_data['outcome'],
                reporter=reporter,
                notes=serializer.validated_data.get('notes', '')
            )
            return Response(
                SnakebiteCaseSerializer(case).data,
                status=status.HTTP_201_CREATED
            )
        except ValueError as e:
            return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)


class RedistributionExecuteAPIView(views.APIView):
    """
    Execute an approved redistribution transfer between facilities.
    Restricted to SYSTEM_ADMIN and STATE_OFFICIAL.
    """
    permission_classes = [IsAuthorizedForTransferWrite]

    def post(self, request):
        serializer = RedistributionRequestSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        source = get_object_or_404(Facility, pk=serializer.validated_data['source_facility_id'])
        target = get_object_or_404(Facility, pk=serializer.validated_data['target_facility_id'])

        role = get_user_role(request.user)
        if role == UserRole.STATE_OFFICIAL:
            assigned_state = get_user_assigned_state(request.user)
            if not assigned_state or (
                source.state.lower() != assigned_state.lower() and
                target.state.lower() != assigned_state.lower()
            ):
                raise PermissionDenied(
                    f"State official for {assigned_state} cannot authorize transfers outside {assigned_state}."
                )

        try:
            tx_out, tx_in = record_redistribution_transfer(
                source_facility=source,
                target_facility=target,
                quantity=serializer.validated_data['quantity'],
                notes=serializer.validated_data.get('notes', '')
            )
            return Response({
                'message': f"Successfully transferred {serializer.validated_data['quantity']} vials from {source.name} to {target.name}.",
                'source_transaction': InventoryTransactionSerializer(tx_out).data,
                'target_transaction': InventoryTransactionSerializer(tx_in).data,
            }, status=status.HTTP_200_OK)
        except ValueError as e:
            return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)
