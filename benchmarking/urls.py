from django.urls import path
from . import views

app_name = "benchmarking"

urlpatterns = [
    path("", views.studio_view, name="studio"),
    path("studio/", views.studio_view, name="studio_direct"),
    path("dashboard/<int:pk>/", views.investigation_dashboard, name="investigation_dashboard"),
    path("stream/<int:run_id>/", views.stream_benchmark_run, name="stream_benchmark_run"),
    path("api/run/", views.run_benchmark_api, name="run_benchmark_api"),
    path("api/scenario/<int:scenario_id>/", views.scenario_detail_api, name="scenario_detail"),
    path("api/diff/<int:result_id>/", views.inspect_result_diff, name="inspect_diff"),
    path("api/promote/<int:result_id>/", views.promote_to_gold_api, name="promote_gold"),
    path("api/curate/", views.curate_dataset_api, name="curate_dataset"),
    path("export/csv/<int:run_id>/", views.export_run_csv, name="export_run_csv"),
]