from django.urls import path
from .views import FindAntivenomAPIView, SearchAuditListAPIView

app_name = 'matching'

urlpatterns = [
    path('search/', FindAntivenomAPIView.as_view(), name='find-antivenom'),
    path('audits/', SearchAuditListAPIView.as_view(), name='search-audits'),
]
