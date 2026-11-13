"""Create the database-backed student reporting tables.

This project uses ``Base.metadata.create_all`` for new tables.  Keeping the
table list here gives deployments an explicit, repeatable migration entry
point and avoids touching existing student or assessment rows.
"""

from app.models.database_models import Base
from app.models import lms_models  # noqa: F401 - registers the reporting models


TABLE_NAMES = (
    "student_study_group_assignments",
    "student_study_group_audit",
    "assessment_report_classifications",
)


def run_migration(engine) -> None:
    for table_name in TABLE_NAMES:
        Base.metadata.tables[table_name].create(bind=engine, checkfirst=True)
