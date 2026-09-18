from django.core.management.base import BaseCommand
from grips.wiki_service import cleanup_defective_concepts


class Command(BaseCommand):
    help = "Finds and prunes defective concepts, corrupt titles, and empty domains from Grips and Git OKF workspace."

    def add_arguments(self, parser):
        parser.add_argument(
            '--apply',
            action='store_true',
            help='Perform actual deletion in PostgreSQL and Git workspace (defaults to dry-run).'
        )

    def handle(self, *args, **options):
        dry_run = not options['apply']
        mode_str = "DRY RUN (no changes applied)" if dry_run else "APPLYING CHANGES"
        self.stdout.write(self.style.NOTICE(f"Starting Grips Wiki Cleanup [{mode_str}]..."))

        report = cleanup_defective_concepts(dry_run=dry_run)

        self.stdout.write(f"\nDefective concepts found: {len(report['deleted_concepts'])}")
        for c in report['deleted_concepts']:
            self.stdout.write(f"  - [{c['id']}] slug='{c['slug']}' title='{c['title']}' (domain: {c['domain']})")

        self.stdout.write(f"\nDefective domains found: {len(report['deleted_domains'])}")
        for d in report['deleted_domains']:
            self.stdout.write(f"  - [{d['id']}] name='{d['name']}'")

        if not dry_run:
            self.stdout.write(f"\nDeleted workspace files: {len(report['deleted_files'])}")
            for f in report['deleted_files']:
                self.stdout.write(f"  - {f}")
            if report['git_commit']:
                self.stdout.write(self.style.SUCCESS(f"\nGit commit created: {report['git_commit']}"))
            self.stdout.write(self.style.SUCCESS("\nCleanup completed successfully."))
        else:
            self.stdout.write(self.style.WARNING("\nDry run completed. Run with --apply to execute cleanup."))
