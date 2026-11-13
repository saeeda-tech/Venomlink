from rest_framework import serializers
from .models import AntivenomSearch

class MatchingQuerySerializer(serializers.Serializer):
    state = serializers.CharField(required=True, max_length=100)
    lga = serializers.CharField(required=False, allow_blank=True, max_length=100, default='')
    latitude = serializers.FloatField(required=False, allow_null=True, default=None)
    longitude = serializers.FloatField(required=False, allow_null=True, default=None)
    limit = serializers.IntegerField(required=False, default=5, min_value=1, max_value=20)
    channel = serializers.ChoiceField(choices=['USSD', 'SMS', 'WEB'], default='WEB')

class AntivenomSearchResultSerializer(serializers.Serializer):
    facility_id = serializers.IntegerField()
    name = serializers.CharField()
    facility_type = serializers.CharField()
    state = serializers.CharField()
    lga = serializers.CharField()
    phone = serializers.CharField(allow_null=True, allow_blank=True)
    phone_display = serializers.CharField(required=False)
    is_demo = serializers.BooleanField(required=False)
    stock = serializers.IntegerField()
    latest_expiry = serializers.CharField()
    expiry_status = serializers.CharField(required=False)
    last_confirmed_label = serializers.CharField()
    freshness_status = serializers.CharField()
    is_stale = serializers.BooleanField()
    stale_warning = serializers.CharField(allow_blank=True)
    distance_km = serializers.FloatField()
    is_same_lga = serializers.BooleanField()
    ranking_score = serializers.FloatField()
    scoring_breakdown = serializers.DictField()

class AntivenomSearchAuditSerializer(serializers.ModelSerializer):
    facility_name = serializers.CharField(source='selected_facility.name', read_only=True)

    class Meta:
        model = AntivenomSearch
        fields = [
            'id', 'state', 'lga', 'timestamp', 'results_found',
            'successful_match', 'facility_name', 'channel'
        ]

