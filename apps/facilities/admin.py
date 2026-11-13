from django.contrib import admin
from .models import Facility, Reporter, UserProfile


@admin.register(Facility)
class FacilityAdmin(admin.ModelAdmin):
    list_display = ('name', 'facility_type', 'state', 'lga', 'phone', 'verified', 'created_at')
    list_filter = ('state', 'facility_type', 'verified')
    search_fields = ('name', 'lga', 'state', 'phone')


@admin.register(Reporter)
class ReporterAdmin(admin.ModelAdmin):
    list_display = ('phone_number', 'facility', 'full_name', 'role', 'active', 'failed_pin_attempts', 'locked_until', 'created_at')
    list_filter = ('role', 'active', 'facility__state')
    search_fields = ('phone_number', 'full_name', 'facility__name')
    exclude = ('pin_hash',)


@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    list_display = ('user', 'role', 'assigned_state', 'facility', 'created_at')
    list_filter = ('role', 'assigned_state')
    search_fields = ('user__username', 'user__email', 'assigned_state', 'facility__name')
