"""Seed synthetic accounts for the Scenario-Based Assessment E2E run.

Refuses to run unless DATABASE_URL points at a throwaway SQLite file whose path
contains "sba_e2e" - it can never touch the dev DB or production.

Accounts (password for all: Sba-E2E-pass1):
  sba.admin@e2e.test    admin
  sba.teacher@e2e.test  teacher   owns class "SBA E2E Class" (Student A, Student B)
  sba.teacher2@e2e.test teacher   owns class "SBA Other Class" (Student C)
  sba.a@e2e.test / sba.b@e2e.test / sba.c@e2e.test  students
"""
import os
import sys

URL = os.environ.get("DATABASE_URL", "")
if not (URL.startswith("sqlite:") and "sba_e2e" in URL):
    sys.exit(f"refusing to seed: DATABASE_URL must be a throwaway sqlite file containing 'sba_e2e' (got {URL!r})")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from app import create_app  # noqa: E402

PASSWORD = "Sba-E2E-pass1"


def main():
    app = create_app()
    with app.app_context():
        from app.models.database_models import User
        from app.models.lms_models import ClassEnrollment, SchoolClass
        from app.utils.db import get_db
        from app.utils.passwords import hash_password

        db = get_db()

        def user(key, role, name):
            email = f"sba.{key}@e2e.test"
            u = db.query(User).filter(User.useremail == email).first()
            if u is None:
                u = User(username=f"sba_{key}", useremail=email, password=hash_password(PASSWORD), role=role,
                         full_name=name, class_standard="11", medium="English", groq_api_key="")
                db.add(u)
                db.commit()
            return u

        admin = user("admin", "admin", "SBA Admin")
        t1 = user("teacher", "teacher", "SBA Teacher")
        t2 = user("teacher2", "teacher", "SBA Teacher Two")
        a, b, c = user("a", "student", "Student A"), user("b", "student", "Student B"), user("c", "student", "Student C")

        def klass(teacher, name, code, students):
            k = db.query(SchoolClass).filter(SchoolClass.join_code == code).first()
            if k is None:
                k = SchoolClass(teacher_id=teacher.id, name=name, join_code=code, grade_level="11", is_active=True)
                db.add(k)
                db.flush()
                for s in students:
                    db.add(ClassEnrollment(class_id=k.id, student_id=s.id, status="active"))
                db.commit()
            return k

        k1 = klass(t1, "SBA E2E Class", "SBAE2E1", [a, b])
        k2 = klass(t2, "SBA Other Class", "SBAE2E2", [c])
        print({"admin": admin.id, "teacher": t1.id, "teacher2": t2.id, "a": a.id, "b": b.id, "c": c.id,
               "class": k1.id, "class2": k2.id})


if __name__ == "__main__":
    main()
