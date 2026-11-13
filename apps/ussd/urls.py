from django.urls import path
from .views import ussd_gateway_callback, UssdSimulationAPIView

app_name = 'ussd'

urlpatterns = [
    path('gateway/', ussd_gateway_callback, name='gateway-callback'),
    path('simulate/', UssdSimulationAPIView.as_view(), name='simulate'),
]
