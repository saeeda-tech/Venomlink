from django.contrib import admin
from .models import SmsLog

@admin.register(SmsLog)
class SmsLogAdmin(admin.ModelAdmin):
    list_display = ('recipient', 'facility', 'purpose', 'status', 'timestamp')
    list_filter = ('purpose', 'status', 'timestamp')
    search_fields = ('recipient', 'message')

