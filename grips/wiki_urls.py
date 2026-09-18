from django.urls import path, re_path
from grips import wiki_views

app_name = "wiki"

urlpatterns = [
    path("", wiki_views.wiki_index, name="index"),
    path("activity/", wiki_views.wiki_activity, name="activity"),
    path("graph/", wiki_views.wiki_graph, name="graph"),
    path("research/reading/", wiki_views.wiki_reading_list, name="reading_list"),
    path("research/matrix/", wiki_views.wiki_synthesis_matrix, name="synthesis_matrix"),
    path("api/graph-data/", wiki_views.wiki_graph_data, name="graph_data"),
    path("api/query-links/", wiki_views.wiki_query_links, name="query_links"),
    re_path(r"^api/save/(?P<path>.*)$", wiki_views.wiki_save, name="save"),
    re_path(r"^history/(?P<path>.*)$", wiki_views.wiki_history, name="history"),
    re_path(r"^(?P<path>.*)/$", wiki_views.wiki_page, name="page"),
    re_path(r"^(?P<path>.*)$", wiki_views.wiki_page, name="page_raw"),
]
