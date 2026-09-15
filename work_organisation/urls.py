from django.urls import path
from . import views

app_name = 'work_organisation'

urlpatterns = [
    path('', views.work_dashboard, name='dashboard'),
    path('session/<int:session_id>/', views.session_mindmap, name='session_mindmap'),
]
