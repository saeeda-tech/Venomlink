from rest_framework import serializers
from .models import Alert


class AlertSerializer(serializers.ModelSerializer):
    facility_name = serializers.CharField(source='facility.name', read_only=True)
    facility_state = serializers.CharField(source='facility.state', read_only=True)
    facility_lga = serializers.CharField(source='facility.lga', read_only=True)
    alert_type_display = serializers.CharField(source='get_alert_type_display', read_only=True)
    severity_display = serializers.CharField(source='get_severity_display', read_only=True)
    severity_color = serializers.CharField(source='severity_badge_color', read_only=True)

    class Meta:
        model = Alert
        fields = [
            'id', 'facility', 'facility_name', 'facility_state', 'facility_lga',
            'alert_type', 'alert_type_display', 'severity', 'severity_display',
            'severity_color', 'message', 'current_stock', 'days_of_cover',
            'daily_usage', 'created_at', 'resolved_at', 'status'
        ]
