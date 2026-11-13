from django.contrib import admin
from .models import InventoryTransaction, SnakebiteCase

@admin.register(InventoryTransaction)
class InventoryTransactionAdmin(admin.ModelAdmin):
    list_display = ('facility', 'transaction_type', 'quantity', 'stock_after', 'expiry_date', 'timestamp')
    list_filter = ('transaction_type', 'timestamp', 'facility__state')
    search_fields = ('facility__name', 'notes', 'batch_number')
    date_hierarchy = 'timestamp'

@admin.register(SnakebiteCase)
class SnakebiteCaseAdmin(admin.ModelAdmin):
    list_display = ('facility', 'date', 'vials_used', 'outcome', 'created_at')
    list_filter = ('outcome', 'date', 'facility__state')
    search_fields = ('facility__name', 'notes')
    date_hierarchy = 'date'

