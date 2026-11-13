from django.urls import path
from .views import (
    FacilityListAPIView, FacilityDetailAPIView,
    StateListAPIView, LgaListAPIView, ReporterListCreateAPIView
)

app_name = 'facilities'

urlpatterns = [
    path('', FacilityListAPIView.as_view(), name='facility-list'),
    path('<int:pk>/', FacilityDetailAPIView.as_view(), name='facility-detail'),
    path('states/', StateListAPIView.as_view(), name='state-list'),
    path('lgas/', LgaListAPIView.as_view(), name='lga-list'),
    path('reporters/', ReporterListCreateAPIView.as_view(), name='reporter-list'),
]
