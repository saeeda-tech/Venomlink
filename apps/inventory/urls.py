from django.urls import path
from .views import (
    InventoryTransactionListAPIView, SnakebiteCaseListAPIView,
    StockUpdateAPIView, CaseReportAPIView, RedistributionExecuteAPIView
)

app_name = 'inventory'

urlpatterns = [
    path('transactions/', InventoryTransactionListAPIView.as_view(), name='transaction-list'),
    path('cases/', SnakebiteCaseListAPIView.as_view(), name='case-list'),
    path('update-stock/', StockUpdateAPIView.as_view(), name='update-stock'),
    path('report-case/', CaseReportAPIView.as_view(), name='report-case'),
    path('transfer/', RedistributionExecuteAPIView.as_view(), name='transfer-execute'),
]
