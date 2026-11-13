from django.contrib import admin
from .models import Alert

@admin.register(Alert)
class AlertAdmin(admin.ModelAdmin):
    list_display = ('facility', 'alert_type', 'severity', 'status', 'current_stock', 'days_of_cover', 'created_at')
    list_filter = ('severity', 'alert_type', 'status', 'facility__state')
    search_fields = ('facility__name', 'message')

