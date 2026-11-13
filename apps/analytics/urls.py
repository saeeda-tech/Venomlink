from django.urls import path
from .views import (
    AlertListAPIView, AlertAcknowledgeAPIView, AlertResolveAPIView,
    RedistributionSuggestionsAPIView, EvaluateAlertsTriggerAPIView
)

app_name = 'analytics'

urlpatterns = [
    path('alerts/', AlertListAPIView.as_view(), name='alert-list'),
    path('alerts/<int:pk>/ack/', AlertAcknowledgeAPIView.as_view(), name='alert-ack'),
    path('alerts/<int:pk>/acknowledge/', AlertAcknowledgeAPIView.as_view(), name='alert-acknowledge'),
    path('alerts/<int:pk>/resolve/', AlertResolveAPIView.as_view(), name='alert-resolve'),
    path('redistribution/', RedistributionSuggestionsAPIView.as_view(), name='redistribution-list'),
    path('evaluate/', EvaluateAlertsTriggerAPIView.as_view(), name='evaluate-trigger'),
]
