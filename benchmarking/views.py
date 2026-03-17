from django.shortcuts import render, get_object_or_404
from django.contrib.admin.views.decorators import staff_member_required
from .models import Investigation, BenchmarkRun, BenchmarkResult


@staff_member_required
def investigation_dashboard(request, pk):
    investigation = get_object_or_404(Investigation, pk=pk)
    experiments = investigation.experiments.all()

    # Gather the LATEST run for each experiment
    runs = []
    for exp in experiments:
        latest_run = BenchmarkRun.objects.filter(experiment=exp).order_by('-timestamp').first()
        if latest_run:
            runs.append(latest_run)
        # Find differing configuration keys to highlight in the dashboard
    all_keys = set()
    for run in runs:
        if run.configuration_snapshot:
            all_keys.update(run.configuration_snapshot.keys())

    differing_keys = set()
    if len(runs) > 1:
        for key in all_keys:
            # Convert to string to make unhashable types comparable
            values = [str(run.configuration_snapshot.get(key)) if run.configuration_snapshot else "None" for run in
                      runs]
            if len(set(values)) > 1:
                differing_keys.add(key)
    else:
        # If there's only one run, show everything since there's nothing to diff against
        differing_keys = all_keys

    for run in runs:
        if run.configuration_snapshot:
            run.filtered_config = {k: v for k, v in run.configuration_snapshot.items() if k in differing_keys}
        else:
            run.filtered_config = {}

    # Pivot data for the detailed table:
    # Row: Scenario
    # Columns: Runs
    # Cell: Result

    # 1. Get all scenarios involved (union of all runs)
    scenarios = set()
    run_results_map = {}  # {run_id: {scenario_id: result}}

    for run in runs:
        results = BenchmarkResult.objects.filter(run=run).select_related('scenario')
        run_results_map[run.id] = {}
        for res in results:
            scenarios.add(res.scenario)
            run_results_map[run.id][res.scenario.id] = res

    # Sort scenarios by question
    sorted_scenarios = sorted(list(scenarios), key=lambda s: s.question)

    # Build rows
    table_rows = []
    for scen in sorted_scenarios:
        row = {'scenario': scen, 'cells': []}
        for run in runs:
            row['cells'].append(run_results_map[run.id].get(scen.id))
        table_rows.append(row)

    context = {
        'investigation': investigation,
        'runs': runs,
        'table_rows': table_rows,
    }
    return render(request, 'benchmarking/dashboard.html', context)


from django.shortcuts import render

# Create your views here.
