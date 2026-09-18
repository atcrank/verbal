from django.urls import path, re_path
from grips import wiki_views

app_name = "wiki"

urlpatterns = [
    path("", wiki_views.wiki_index, name="index"),
    path("activity/", wiki_views.wiki_activity, name="activity"),
    path("api/query-links/", wiki_views.wiki_query_links, name="query_links"),
    re_path(r"^api/save/(?P<path>.*)$", wiki_views.wiki_save, name="save"),
    re_path(r"^history/(?P<path>.*)$", wiki_views.wiki_history, name="history"),
    re_path(r"^(?P<path>.*)/$", wiki_views.wiki_page, name="page"),
    re_path(r"^(?P<path>.*)$", wiki_views.wiki_page, name="page_raw"),
]
