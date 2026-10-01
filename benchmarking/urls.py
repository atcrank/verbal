from django.urls import path
from . import views

app_name = "benchmarking"

urlpatterns = [
    path("", views.studio_view, name="studio"),
    path("studio/", views.studio_view, name="studio_direct"),
    path("dashboard/<int:pk>/", views.investigation_dashboard, name="investigation_dashboard"),
    path("stream/<int:run_id>/", views.stream_benchmark_run, name="stream_benchmark_run"),
    path("stream/investigation/<int:investigation_id>/", views.stream_investigation_matrix, name="stream_investigation_matrix"),
    path("api/run/", views.run_benchmark_api, name="run_benchmark_api"),
    path("api/scenario/<int:scenario_id>/", views.scenario_detail_api, name="scenario_detail"),
    path("api/scenario/<int:scenario_id>/update/", views.update_scenario_api, name="update_scenario"),
    path("api/scenario/close/", views.close_inspector_api, name="close_inspector"),
    path("api/scenario-group/<int:group_id>/scenarios/", views.switch_scenario_group_api, name="switch_scenario_group"),
    path("api/scenario-group/<int:group_id>/review/", views.suite_review_modal_api, name="suite_review_modal"),
    path("api/scenario-group/<int:group_id>/add-scenario/", views.add_scenario_to_group_api, name="add_scenario_to_group"),
    path("api/diff/<int:result_id>/", views.inspect_result_diff, name="inspect_diff"),
    path("api/promote/<int:result_id>/", views.promote_to_gold_api, name="promote_gold"),
    path("api/curate/", views.curate_dataset_api, name="curate_dataset"),
    path("api/train/", views.train_adapter_api, name="train_adapter"),
    path("api/ab-eval/<int:adapter_id>/", views.ab_evaluation_api, name="ab_evaluation"),
    path("api/hardware/", views.hardware_profile_api, name="hardware_profile"),
    path("api/leaderboard/", views.leaderboard_api, name="leaderboard_api"),
    path("api/grouped-history/", views.grouped_history_api, name="grouped_history_api"),
    path("export/csv/<int:run_id>/", views.export_run_csv, name="export_run_csv"),
    path("export/dataframe/<int:investigation_id>/", views.export_investigation_dataframe, name="export_investigation_dataframe"),
]