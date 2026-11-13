from rest_framework import serializers
from .models import InventoryTransaction, SnakebiteCase


class InventoryTransactionSerializer(serializers.ModelSerializer):
    facility_name = serializers.CharField(source='facility.name', read_only=True)
    reporter_name = serializers.CharField(source='reporter.full_name', read_only=True)
    transaction_type_display = serializers.CharField(source='get_transaction_type_display', read_only=True)

    class Meta:
        model = InventoryTransaction
        fields = [
            'id', 'facility', 'facility_name', 'reporter', 'reporter_name',
            'transaction_type', 'transaction_type_display', 'quantity',
            'stock_after', 'expiry_date', 'batch_number', 'timestamp', 'notes'
        ]


class SnakebiteCaseSerializer(serializers.ModelSerializer):
    facility_name = serializers.CharField(source='facility.name', read_only=True)
    reporter_name = serializers.CharField(source='reporter.full_name', read_only=True)
    outcome_display = serializers.CharField(source='get_outcome_display', read_only=True)

    class Meta:
        model = SnakebiteCase
        fields = [
            'id', 'facility', 'facility_name', 'reporter', 'reporter_name',
            'date', 'vials_used', 'outcome', 'outcome_display', 'notes', 'created_at'
        ]


class StockUpdateRequestSerializer(serializers.Serializer):
    facility_id = serializers.IntegerField()
    quantity = serializers.IntegerField(min_value=0)
    expiry_date = serializers.CharField(required=False, allow_blank=True, max_length=10)
    phone_number = serializers.CharField(required=False, allow_blank=True, max_length=32)
    pin = serializers.CharField(required=False, allow_blank=True, max_length=16, write_only=True)
    notes = serializers.CharField(required=False, allow_blank=True)


class CaseReportRequestSerializer(serializers.Serializer):
    facility_id = serializers.IntegerField()
    vials_used = serializers.IntegerField(min_value=1)
    outcome = serializers.ChoiceField(
        choices=['RECOVERED', 'UNDER_TREATMENT', 'TRANSFERRED', 'FATALITY'],
        default='RECOVERED'
    )
    phone_number = serializers.CharField(required=False, allow_blank=True, max_length=32)
    pin = serializers.CharField(required=False, allow_blank=True, max_length=16, write_only=True)
    notes = serializers.CharField(required=False, allow_blank=True)


class RedistributionRequestSerializer(serializers.Serializer):
    source_facility_id = serializers.IntegerField()
    target_facility_id = serializers.IntegerField()
    quantity = serializers.IntegerField(min_value=1)
    notes = serializers.CharField(required=False, allow_blank=True)
