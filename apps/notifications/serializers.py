from rest_framework import serializers
from .models import SmsLog
from apps.facilities.models import UserRole
from apps.facilities.permissions import get_user_role


class SmsLogSerializer(serializers.ModelSerializer):
    facility_name = serializers.CharField(source='facility.name', read_only=True)
    purpose_display = serializers.CharField(source='get_purpose_display', read_only=True)
    recipient = serializers.SerializerMethodField()

    class Meta:
        model = SmsLog
        fields = [
            'id', 'recipient', 'message', 'facility', 'facility_name',
            'purpose', 'purpose_display', 'status', 'timestamp'
        ]

    def get_recipient(self, obj) -> str:
        request = self.context.get('request')
        if request and get_user_role(request.user) in (UserRole.SYSTEM_ADMIN, UserRole.STATE_OFFICIAL):
            return obj.recipient
        raw = str(obj.recipient or '')
        if len(raw) > 7:
            return f"{raw[:6]}****{raw[-3:]}"
        return "****"
