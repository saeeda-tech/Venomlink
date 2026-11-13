from django.urls import path
from .views import SmsLogListAPIView

app_name = 'notifications'

urlpatterns = [
    path('sms-logs/', SmsLogListAPIView.as_view(), name='sms-logs'),
]
