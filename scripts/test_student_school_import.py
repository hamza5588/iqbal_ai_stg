"""School import and admin reporting tests using disposable SQLite data."""
import importlib.util
import ast
import logging
from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect, text

path = Path(__file__).resolve().parents[1] / "scripts/import_student_schools.py"
spec = importlib.util.spec_from_file_location("school_import", path)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


@pytest.fixture
def engine():
    db = create_engine("sqlite:///:memory:")
    with db.begin() as conn:
        conn.execute(text("CREATE TABLE users (id INTEGER PRIMARY KEY, username TEXT, full_name TEXT, useremail TEXT, role TEXT)"))
        conn.execute(text("INSERT INTO users VALUES (1, 'student1', 'Student One', 'one@example.test', 'student'), (2, 'student2', 'Student Two', 'two@example.test', 'student'), (3, 'teacher', 'Teacher One', 'teacher@example.test', 'teacher')"))
    yield db
    db.dispose()


def row(**overrides):
    return {"student_name": "Student One", "email": " ONE@example.test ", "school_name": "School A", **overrides}


def test_dry_run_makes_no_schema_or_data_changes(engine):
    result = module.import_schools(engine, [row()])
    assert result["matched"] == 1 and not result["applied"]
    assert "school_name" not in {c["name"] for c in inspect(engine).get_columns("users")}


def test_apply_preserves_other_accounts_and_is_idempotent(engine, tmp_path):
    result = module.import_schools(engine, [row()], apply=True, backup_path=tmp_path / "before.json")
    assert result["changed"] == 1
    with engine.connect() as conn:
        assert conn.execute(text("SELECT school_name FROM users ORDER BY id")).scalars().all() == ["School A", None, None]
    assert module.import_schools(engine, [row()])["changed"] == 0
    assert (tmp_path / "before.json").exists()


@pytest.mark.parametrize("rows", [
    [row(), row()], [row(email="unknown@example.test")], [row(student_name="Wrong Name")],
    [row(school_name="")], [row(school_name="x" * 256)],
    [row(email="teacher@example.test", student_name="Teacher One")], [],
    [row(), row(email="two@example.test", student_name="Wrong Name")],
])
def test_invalid_import_never_adds_column(engine, tmp_path, rows):
    with pytest.raises(ValueError):
        module.import_schools(engine, rows, apply=True, backup_path=tmp_path / "before.json")
    assert "school_name" not in {c["name"] for c in inspect(engine).get_columns("users")}


def test_duplicate_database_email_rejected(engine):
    with engine.begin() as conn:
        conn.execute(text("UPDATE users SET useremail = 'ONE@example.test' WHERE id = 2"))
    with pytest.raises(ValueError, match="one exact email"):
        module.import_schools(engine, [row()])


def test_missing_backup_blocks_apply(engine):
    with pytest.raises(ValueError, match="backup"):
        module.import_schools(engine, [row()], apply=True)


def test_admin_list_and_detail_expose_school_and_search_it():
    """Exercise real route bodies and ORM queries without starting AI providers."""
    from flask import Flask, jsonify, request
    from sqlalchemy import or_
    from sqlalchemy.orm import Session

    root = path.parents[1]
    model_spec = importlib.util.spec_from_file_location("school_test_models", root / "app/models/database_models.py")
    models = importlib.util.module_from_spec(model_spec)
    model_spec.loader.exec_module(models)
    db_engine = create_engine("sqlite:///:memory:")
    models.Base.metadata.create_all(db_engine)
    with Session(db_engine) as db:
        for index, school in enumerate(["DIL Junior 1", None], start=1):
            db.add(models.User(id=index, username=f"student{index}", full_name=f"Student {index}",
                               useremail=f"student{index}@example.test", password="unused",
                               role="student", class_standard="9", medium="English",
                               groq_api_key="", school_name=school))
        db.commit()
        tree = ast.parse((root / "app/routes/admin_routes.py").read_text(encoding="utf-8"))
        nodes = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in {"_int_arg", "list_users", "get_user"}]
        for node in nodes:
            node.decorator_list = []
        namespace = {"get_db": lambda: db, "DBUser": models.User, "request": request,
                     "jsonify": jsonify, "or_": or_, "logger": logging.getLogger(__name__)}
        exec(compile(ast.Module(body=nodes, type_ignores=[]), "admin_routes.py", "exec"), namespace)
        app = Flask(__name__)
        with app.test_request_context("/admin/users?search=Junior%201"):
            data = namespace["list_users"]().get_json()
            assert data["total"] == 1
            assert data["users"][0]["school_name"] == "DIL Junior 1"
        with app.test_request_context("/admin/users/2"):
            assert namespace["get_user"](2).get_json()["user"]["school_name"] == ""
    db_engine.dispose()
