from django.urls import path
from .views import (
    overview_view, alerts_view, facilities_view, facility_detail_view,
    redistribution_view, ussd_simulator_view, demo_runner_view, demo_runner_action_view
)

app_name = 'dashboard'

urlpatterns = [
    path('', overview_view, name='overview'),
    path('alerts/', alerts_view, name='alerts'),
    path('facilities/', facilities_view, name='facilities'),
    path('facilities/<int:pk>/', facility_detail_view, name='facility-detail'),
    path('redistribution/', redistribution_view, name='redistribution'),
    path('ussd-simulator/', ussd_simulator_view, name='ussd-simulator'),
    path('demo-flow/', demo_runner_view, name='demo-runner'),
    path('demo-flow/action/', demo_runner_action_view, name='demo-runner-action'),
]

