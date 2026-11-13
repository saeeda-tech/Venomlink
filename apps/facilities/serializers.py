from rest_framework import serializers
from .models import Facility, Reporter
from apps.notifications.sms_gateway import normalise_nigerian_phone


class ReporterSerializer(serializers.ModelSerializer):
    facility_name = serializers.CharField(source='facility.name', read_only=True)
    pin = serializers.CharField(write_only=True, required=False, min_length=4, max_length=16)

    class Meta:
        model = Reporter
        fields = [
            'id', 'phone_number', 'facility', 'facility_name',
            'full_name', 'role', 'active', 'pin', 'created_at'
        ]

    def validate_phone_number(self, value):
        return normalise_nigerian_phone(value)

    def create(self, validated_data):
        raw_pin = validated_data.pop('pin', None)
        reporter = Reporter(**validated_data)
        if raw_pin:
            reporter.set_pin(raw_pin)
        reporter.save()
        return reporter

    def update(self, instance, validated_data):
        raw_pin = validated_data.pop('pin', None)
        for attr, val in validated_data.items():
            setattr(instance, attr, val)
        if raw_pin:
            instance.set_pin(raw_pin)
        instance.save()
        return instance


class FacilitySerializer(serializers.ModelSerializer):
    current_stock = serializers.IntegerField(read_only=True)
    usable_stock = serializers.IntegerField(read_only=True)
    latest_expiry = serializers.CharField(read_only=True)
    expiry_status = serializers.CharField(read_only=True)
    expiry_status_label = serializers.CharField(read_only=True)
    has_expired_stock = serializers.BooleanField(read_only=True)
    last_confirmed_at = serializers.DateTimeField(read_only=True)
    freshness_status = serializers.CharField(read_only=True)
    freshness_label = serializers.CharField(read_only=True)
    days_of_cover = serializers.FloatField(read_only=True, allow_null=True)
    days_of_cover_display = serializers.CharField(read_only=True)
    consumption_confidence = serializers.CharField(read_only=True)
    consumption_confidence_label = serializers.CharField(read_only=True)
    phone = serializers.CharField(allow_null=True, allow_blank=True, required=False)
    phone_display = serializers.CharField(read_only=True)
    is_demo = serializers.BooleanField(read_only=True)
    below_min_treatment_course = serializers.BooleanField(read_only=True)
    stock_status = serializers.CharField(read_only=True)
    stock_status_label = serializers.CharField(read_only=True)
    stock_status_color = serializers.CharField(read_only=True)
    facility_type_display = serializers.CharField(source='get_facility_type_display', read_only=True)

    class Meta:
        model = Facility
        fields = [
            'id', 'name', 'facility_type', 'facility_type_display', 'state', 'lga',
            'latitude', 'longitude', 'phone', 'phone_display', 'is_demo', 'verified',
            'resupply_lead_time_days', 'safety_buffer_days', 'created_at',
            'current_stock', 'usable_stock', 'latest_expiry', 'expiry_status',
            'expiry_status_label', 'has_expired_stock', 'last_confirmed_at',
            'freshness_status', 'freshness_label', 'days_of_cover',
            'days_of_cover_display', 'consumption_confidence', 'consumption_confidence_label',
            'stock_status', 'stock_status_label', 'stock_status_color',
            'below_min_treatment_course',
        ]
