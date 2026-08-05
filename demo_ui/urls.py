from django.urls import path
from . import views

app_name = 'demo_ui'
urlpatterns = [
    path('', views.index, name='index'),
    path('search/', views.search_knowledge_base, name='search_knowledge_base'),
    path('conversation/<uuid:conversation_id>/', views.get_conversation, name='get_conversation'),
    path('send/', views.send_message, name='send_message'),
    path('upload/', views.upload_document, name='upload_document'),
    path('documents/', views.list_documents, name='list_documents'),
    path('conversation/<uuid:conversation_id>/download/<path:filename>', views.download_file, name='download_file'),
    path('grips-explorer/', views.grips_explorer_tab, name='grips_explorer_tab'),
    path('grips-explorer/children/<int:concept_id>/', views.grips_concept_children, name='grips_concept_children'),
    path('grips-explorer/fill-stub/<int:concept_id>/', views.fill_grips_stub, name='fill_grips_stub'),
]