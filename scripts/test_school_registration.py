"""Exercise production account routes/model with SQLite, without AI or email services."""
import ast
import importlib.util
import logging
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from flask import Flask, jsonify, redirect, render_template, request, session
from sqlalchemy import and_, create_engine, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

ROOT = Path(__file__).resolve().parents[1]


def load_file(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def functions(path, names, namespace):
    tree = ast.parse((ROOT / path).read_text(encoding='utf-8'))
    nodes = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in names]
    assert len(nodes) == len(names)
    for node in nodes:
        node.decorator_list = []
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(path), 'exec'), namespace)


@pytest.fixture
def env(monkeypatch):
    models = load_file('registration_models', 'app/models/database_models.py')
    passwords = load_file('registration_passwords', 'app/utils/passwords.py')
    schools = load_file('registration_schools', 'app/utils/schools.py')
    grades = load_file('registration_grades', 'app/services/lms/grade_utils.py')
    monkeypatch.setitem(sys.modules, 'app.utils.passwords', passwords)
    monkeypatch.setitem(sys.modules, 'app.services.lms.grade_utils', grades)
    engine = create_engine('sqlite:///:memory:')
    models.Base.metadata.create_all(engine)
    with Session(engine) as db:
        ns = dict(get_db=lambda: db, DBUser=models.User, DBEmailVerificationToken=models.EmailVerificationToken,
                  logger=logging.getLogger(__name__), request=request, jsonify=jsonify, session=session,
                  redirect=redirect, render_template=render_template, and_=and_, or_=or_, datetime=datetime,
                  IntegrityError=IntegrityError, password_problem=passwords.password_problem,
                  hash_password=passwords.hash_password, school_name_problem=schools.school_name_problem,
                  _EMAIL_RE=re.compile(r'^[^@\s]+@[^@\s]+\.[^@\s]+$'),
                  _NON_GRADE_STANDARDS={'', 'n/a', 'college', 'university', 'other'},
                  SUBSCRIPTION_TIERS=('free', 'pro', 'pro_plus'), SELF_REGISTER_ROLES=('student', 'teacher'),
                  get_default_route_by_role=lambda role: '/dashboard')
        tree = ast.parse((ROOT / 'app/models/models.py').read_text(encoding='utf-8'))
        model = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'UserModel')
        model.body = [n for n in model.body if isinstance(n, ast.FunctionDef) and n.name == 'create_user']
        exec(compile(ast.Module(body=[model], type_ignores=[]), 'models.py', 'exec'), ns)
        functions('app/routes/admin_routes.py', {'_text', '_username_problem', '_email_problem', '_name_problem',
                  '_class_standard_problem', '_is_last_admin', 'create_user', 'update_user'}, ns)
        functions('app/routes/auth.py', {'register'}, ns)
        app = Flask(__name__, template_folder=str(ROOT / 'templates'))
        app.secret_key = 'disposable-test-only'
        app.add_url_rule('/admin/users', view_func=ns['create_user'], methods=['POST'])
        app.add_url_rule('/admin/users/<int:user_id>', view_func=ns['update_user'], methods=['PUT'])
        app.add_url_rule('/register', endpoint='auth.register', view_func=ns['register'], methods=['GET', 'POST'])
        # Links referenced by the production registration template.
        app.add_url_rule('/login', endpoint='auth.login', view_func=lambda: '')
        app.add_url_rule('/register_email', endpoint='auth.register_email', view_func=lambda: '')
        yield SimpleNamespace(db=db, models=models, client=app.test_client(), ns=ns, passwords=passwords)
    engine.dispose()


def account(**changes):
    return dict(username='school_test', full_name='School Test', useremail='school@example.test',
                password='Test-password-2026', role='student', medium='English', class_standard='9',
                school_name='  DIL Junior 11  ', **changes)


@pytest.mark.parametrize('role', ['student', 'teacher', 'admin'])
def test_admin_creates_each_role_with_trimmed_school(env, role):
    data = account(); data['role'] = role
    result = env.client.post('/admin/users', json=data)
    assert result.status_code == 200
    user = env.db.get(env.models.User, result.json['user_id'])
    assert user.school_name == 'DIL Junior 11'
    assert env.passwords.verify_password(user.password, data['password'])[0]


@pytest.mark.parametrize('school', [None, '', '   ', 123, ['School'], 'x' * 256])
def test_admin_invalid_school_creates_no_account(env, school):
    data = account(); data['school_name'] = school
    response = env.client.post('/admin/users', json=data)
    assert response.status_code == 400 and 'School name' in response.json['error']
    assert env.db.query(env.models.User).count() == 0


def test_admin_missing_school_creates_no_account(env):
    data = account(); del data['school_name']
    assert env.client.post('/admin/users', json=data).status_code == 400
    assert env.db.query(env.models.User).count() == 0


def test_edit_school_and_reject_atomic_invalid_update(env):
    user_id = env.client.post('/admin/users', json=account()).json['user_id']
    assert env.client.put(f'/admin/users/{user_id}', json={'school_name': '  DIL Amina '}).status_code == 200
    user = env.db.get(env.models.User, user_id)
    assert user.school_name == 'DIL Amina'
    for bad in ['', 'x' * 256, 123]:
        response = env.client.put(f'/admin/users/{user_id}', json={'school_name': bad, 'full_name': 'Changed'})
        assert response.status_code == 400
        env.db.refresh(user)
        assert user.school_name == 'DIL Amina' and user.full_name == 'School Test'


def test_legacy_account_can_be_edited_then_get_school(env):
    data = account(); del data['school_name']
    user_id = env.ns['UserModel'].create_user(**data)
    assert env.client.put(f'/admin/users/{user_id}', json={'school_name': '', 'full_name': 'Legacy'}).status_code == 200
    assert env.client.put(f'/admin/users/{user_id}', json={'school_name': 'New School'}).status_code == 200
    assert env.db.get(env.models.User, user_id).school_name == 'New School'


def verified_email(env):
    env.db.add(env.models.EmailVerificationToken(email='school@example.test', token='test-only',
               expires_at=datetime.utcnow() + timedelta(hours=1), used=False))
    env.db.commit()


@pytest.mark.parametrize('school', ['', '  ', 'x' * 256])
def test_public_registration_requires_school_and_preserves_email(env, school):
    verified_email(env)
    data = account(); data['school_name'] = school
    response = env.client.post('/register', data=data)
    html = response.get_data(as_text=True)
    assert 'School name' in html and 'value="school@example.test"' in html
    assert env.db.query(env.models.User).count() == 0
    assert env.db.query(env.models.EmailVerificationToken).one().used is False


@pytest.mark.parametrize('role', ['student', 'teacher'])
def test_public_registration_saves_school_and_uses_token(env, role):
    verified_email(env)
    data = account(); data['role'] = role
    response = env.client.post('/register', data=data)
    assert response.status_code == 302
    assert env.db.query(env.models.User).one().school_name == 'DIL Junior 11'
    assert env.db.query(env.models.EmailVerificationToken).one().used is True


def test_public_form_has_required_school_and_workbook_suggestions(env):
    html = env.client.get('/register').get_data(as_text=True)
    assert 'name="school_name" required maxlength="255"' in html
    assert 'DIL Amina' in html and 'DIL Junior 11' in html
