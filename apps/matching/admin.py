from django.contrib import admin
from .models import AntivenomSearch

@admin.register(AntivenomSearch)
class AntivenomSearchAdmin(admin.ModelAdmin):
    list_display = ('state', 'lga', 'results_found', 'successful_match', 'selected_facility', 'channel', 'timestamp')
    list_filter = ('channel', 'successful_match', 'state')
    search_fields = ('state', 'lga')

