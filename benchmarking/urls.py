from django.urls import path
from . import views

urlpatterns = [
    path('dashboard/<int:pk>/', views.investigation_dashboard, name='investigation_dashboard'),
]