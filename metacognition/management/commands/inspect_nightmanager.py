import json
from django.core.management.base import BaseCommand
from metacognition.reporting import audit_nightmanager_performance, format_performance_report_markdown


class Command(BaseCommand):
    help = "Generates a comprehensive diagnostic and performance audit report for the NightManager."

    def add_arguments(self, parser):
        parser.add_argument(
            '--days',
            type=int,
            default=7,
            help="Number of past days to inspect (default: 7)."
        )
        parser.add_argument(
            '--json',
            action='store_true',
            help="Output raw JSON instead of formatted Markdown."
        )
        parser.add_argument(
            '--save-report',
            type=str,
            default=None,
            help="Optional file path to save the generated report."
        )

    def handle(self, *args, **options):
        days = options['days']
        as_json = options['json']
        save_path = options['save_report']

        report = audit_nightmanager_performance(since_days=days)

        if as_json:
            output = json.dumps(report, indent=2)
        else:
            output = format_performance_report_markdown(report)

        if save_path:
            with open(save_path, 'w', encoding='utf-8') as f:
                f.write(output)
            self.stdout.write(self.style.SUCCESS(f"Report saved to {save_path}"))
        else:
            self.stdout.write(output)
