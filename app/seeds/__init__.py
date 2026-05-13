"""
Seed commands for IqbalAI Phase 1 defaults.

Register with the app factory via register_seeds(app).
Run with: flask seed <command>
"""
import click


def register_seeds(app):
    """Register the 'seed' CLI group on the Flask app."""

    @app.cli.group()
    def seed():
        """Database seed commands."""

    from app.seeds import personalities, exam_types, platform_subjects, syllabus

    seed.add_command(personalities.seed_personalities)
    seed.add_command(exam_types.seed_exam_types)
    seed.add_command(platform_subjects.seed_platform_subjects)
    seed.add_command(syllabus.seed_syllabus)
