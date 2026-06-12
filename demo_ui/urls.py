from django.urls import path
from . import views

app_name = 'demo_ui'
urlpatterns = [
    path('', views.index, name='index'),
    path('search/', views.search_knowledge_base, name='search_knowledge_base'),
    path('conversation/<uuid:conversation_id>/', views.get_conversation, name='get_conversation'),
    path('send/', views.send_message, name='send_message'),
 ]