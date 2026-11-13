"""
VenomLink Master URL Configuration.
"""

from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static

from venomlink.health import healthz, readyz

admin.site.site_header = "VENOMLINK — Prototype (USPF Changemaker Challenge 2026)"
admin.site.site_title = "VENOMLINK Prototype Admin"
admin.site.index_title = "Prototype System Administration"

urlpatterns = [
    # Operational Health Probes
    path('healthz/', healthz, name='healthz'),
    path('readyz/', readyz, name='readyz'),

    # Authentication
    path('accounts/login/', auth_views.LoginView.as_view(), name='login'),
    path('accounts/logout/', auth_views.LogoutView.as_view(), name='logout'),
    path('accounts/password_change/', auth_views.PasswordChangeView.as_view(), name='password_change'),
    path('accounts/password_change/done/', auth_views.PasswordChangeDoneView.as_view(), name='password_change_done'),

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

