# Generated for WS17: Layered Tool Governance & Lockdown Architecture

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("metacognition", "0022_reasoningstep_include_state_tree"),
    ]

    operations = [
        migrations.AddField(
            model_name="tooldefinition",
            name="capability_category",
            field=models.CharField(
                choices=[
                    ("READ_ONLY", "Read-Only (Knowledge & DB Inspection)"),
                    ("STATE_MUTATION", "State Mutation (Deterministic Writes)"),
                    ("CODE_EXECUTION", "Code Execution (Model-Written Scripts)"),
                    ("META_GOVERNANCE", "Meta-Governance (Self-Modification)"),
                ],
                default="READ_ONLY",
                help_text="WS17 capability classification",
                max_length=30,
            ),
        ),
        migrations.AddField(
            model_name="tooldefinition",
            name="required_clearance",
            field=models.CharField(
                choices=[
                    ("STANDARD", "Standard User"),
                    ("TRUSTED", "Trusted Operator / Analyst"),
                    ("ADMIN", "System Administrator"),
                ],
                default="STANDARD",
                help_text="Minimum user clearance required to execute this tool",
                max_length=20,
            ),
        ),
    ]
