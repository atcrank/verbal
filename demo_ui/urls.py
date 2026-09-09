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
    path('documents/<int:document_id>/ingest/', views.trigger_document_ingestion, name='trigger_document_ingestion'),
    path('branch/<uuid:log_id>/', views.branch_conversation, name='branch_conversation'),
    path('context/preview/', views.preview_context_item, name='preview_context_item'),
    path('context/tokens/', views.calculate_context_tokens, name='calculate_context_tokens'),
    path('conversation/<uuid:conversation_id>/download/<path:filename>', views.download_file, name='download_file'),
    path('grips-explorer/', views.grips_explorer_tab, name='grips_explorer_tab'),
    path('grips-explorer/children/<int:concept_id>/', views.grips_concept_children, name='grips_concept_children'),
    path('grips-explorer/fill-stub/<int:concept_id>/', views.fill_grips_stub, name='fill_grips_stub'),
]