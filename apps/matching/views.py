from rest_framework import views, generics, status, permissions
from rest_framework.response import Response
from .engine import rank_facilities_for_query
from .serializers import (
    MatchingQuerySerializer, AntivenomSearchResultSerializer,
    AntivenomSearchAuditSerializer
)
from .models import AntivenomSearch
from apps.facilities.models import UserRole
from apps.facilities.permissions import (
    IsAuthenticatedWithOperationalRole, get_user_role, get_user_assigned_state
)
from apps.facilities.throttling import AnonSearchRateThrottle

class FindAntivenomAPIView(views.APIView):
    """
    Core search & matching API endpoint.
    Public users (PUBLIC_USER) may search for availability via GET or POST without modifying inventory.
    Anonymous callers are rate limited per client IP (scope: search_anon).
    """
    permission_classes = [permissions.AllowAny]
    throttle_classes = [AnonSearchRateThrottle]

    def post(self, request):
        serializer = MatchingQuerySerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        data = serializer.validated_data
        ranked = rank_facilities_for_query(
            state=data['state'],
            lga=data.get('lga', ''),
            user_lat=data.get('latitude'),
            user_lon=data.get('longitude'),
            limit=data.get('limit', 5),
            channel=data.get('channel', 'WEB')
        )

        results_data = AntivenomSearchResultSerializer(ranked, many=True).data
        return Response({
            'query': {
                'state': data['state'],
                'lga': data.get('lga', ''),
            },
            'count': len(results_data),
            'results': results_data
        })

    def get(self, request):
        state = request.query_params.get('state', '')
        lga = request.query_params.get('lga', '')
        lat = request.query_params.get('latitude')
        lon = request.query_params.get('longitude')

        try:
            lat_val = float(lat) if lat else None
            lon_val = float(lon) if lon else None
        except (TypeError, ValueError):
            return Response(
                {'error': 'latitude and longitude must be valid numbers.'},
                status=status.HTTP_400_BAD_REQUEST
            )
        if (lat_val is not None and not -90 <= lat_val <= 90) or (
            lon_val is not None and not -180 <= lon_val <= 180
        ):
            return Response(
                {'error': 'latitude/longitude out of range.'},
                status=status.HTTP_400_BAD_REQUEST
            )

        ranked = rank_facilities_for_query(
            state=state,
            lga=lga,
            user_lat=lat_val,
            user_lon=lon_val,
            limit=5,
            channel='WEB'
        )
        results_data = AntivenomSearchResultSerializer(ranked, many=True).data
        return Response({
            'query': {'state': state, 'lga': lga},
            'count': len(results_data),
            'results': results_data
        })

class SearchAuditListAPIView(generics.ListAPIView):
    """Search audit trail: SYSTEM_ADMIN (all) and STATE_OFFICIAL (own state) only."""
    serializer_class = AntivenomSearchAuditSerializer
    permission_classes = [IsAuthenticatedWithOperationalRole]

    def get_queryset(self):
        qs = AntivenomSearch.objects.all().select_related('selected_facility')
        role = get_user_role(self.request.user)
        if role == UserRole.SYSTEM_ADMIN:
            return qs[:100]
        if role == UserRole.STATE_OFFICIAL:
            assigned_state = get_user_assigned_state(self.request.user)
            if assigned_state:
                return qs.filter(state__iexact=assigned_state)[:100]
        return qs.none()

