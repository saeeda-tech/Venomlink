"""
VenomLink Master URL Configuration.
"""

from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static

from venomlink.health import healthz, readyz

urlpatterns = [
    # Operational Health Probes
    path('healthz/', healthz, name='healthz'),
    path('readyz/', readyz, name='readyz'),

    # Administration
    path('admin/', admin.site.urls),

    # Web Dashboard (State Officials, Facility Admins)
    path('', include('apps.dashboard.urls', namespace='dashboard')),

    # REST APIs
    path('api/facilities/', include('apps.facilities.urls', namespace='facilities-api')),
    path('api/inventory/', include('apps.inventory.urls', namespace='inventory-api')),
    path('api/matching/', include('apps.matching.urls', namespace='matching-api')),
    path('api/ussd/', include('apps.ussd.urls', namespace='ussd-api')),
    path('api/notifications/', include('apps.notifications.urls', namespace='notifications-api')),
    path('api/analytics/', include('apps.analytics.urls', namespace='analytics-api')),
]

if settings.DEBUG:
    urlpatterns += static(settings.STATIC_URL, document_root=settings.STATICFILES_DIRS[0])

