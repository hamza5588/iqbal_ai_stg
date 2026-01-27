# RBAC Usage Examples

This document provides examples of how to use the Role-Based Access Control (RBAC) system in IqbalAI.

## Overview

The RBAC system supports three roles:
- **Student**: Can view lessons
- **Teacher**: Can manage their own lessons and upload PDFs
- **Admin**: Can access everything (all users, all lessons, all records)

## Role Definitions

### Student Permissions
- View lessons (public lessons)
- View own profile
- Edit own profile
- Create conversations
- View own conversations

### Teacher Permissions
- All student permissions, plus:
- View own lessons
- Create lessons
- Edit own lessons
- Delete own lessons
- Upload PDF documents
- View own documents
- Delete own documents

### Admin Permissions
- All teacher permissions, plus:
- View all lessons
- Edit any lesson
- Delete any lesson
- View all documents
- Delete any document
- View all users
- Edit any user
- Delete any user
- Manage user roles
- View teacher records
- View student records
- View all records
- View all conversations

## Using RBAC in Routes

### Example 1: Using Role Decorators

```python
from flask import Blueprint, jsonify
from app.rbac.decorators import role_required, admin_only, teacher_only
from app.rbac.roles import Role

bp = Blueprint('example', __name__)

# Require specific role
@bp.route('/student-only')
@role_required(Role.STUDENT)
def student_route():
    return jsonify({'message': 'Student access granted'})

# Require teacher role
@bp.route('/teacher-only')
@teacher_only
def teacher_route():
    return jsonify({'message': 'Teacher access granted'})

# Require admin role
@bp.route('/admin-only')
@admin_only
def admin_route():
    return jsonify({'message': 'Admin access granted'})
```

### Example 2: Using Permission Decorators

```python
from app.rbac.decorators import permission_required
from app.rbac.permissions import Permissions

@bp.route('/upload-pdf')
@permission_required(Permissions.UPLOAD_PDF)
def upload_pdf():
    return jsonify({'message': 'PDF upload access granted'})

@bp.route('/view-all-users')
@permission_required(Permissions.VIEW_ALL_USERS)
def view_all_users():
    return jsonify({'message': 'View all users access granted'})
```

### Example 3: Using Utility Functions in Routes

```python
from app.rbac.utils import (
    get_user_role,
    is_admin,
    can_view_all_lessons,
    can_manage_lesson
)

@bp.route('/lessons')
def get_lessons():
    user_id = session.get('user_id')
    
    # Check if user is admin
    if is_admin(user_id):
        # Return all lessons
        lessons = get_all_lessons()
    elif can_view_all_lessons(user_id):
        # Admin can view all
        lessons = get_all_lessons()
    else:
        # Regular user - return their lessons or public lessons
        lessons = get_user_lessons(user_id)
    
    return jsonify({'lessons': lessons})

@bp.route('/lessons/<int:lesson_id>/edit')
def edit_lesson(lesson_id):
    user_id = session.get('user_id')
    
    # Check if user can manage this lesson
    if not can_manage_lesson(lesson_id, user_id):
        return jsonify({'error': 'Permission denied'}), 403
    
    # Proceed with edit
    return jsonify({'message': 'Edit lesson'})
```

## Using RBAC in Templates (Jinja2)

The RBAC template helpers are automatically registered in Flask templates. You can use them like this:

### Example 1: Conditional UI Elements

```html
<!-- Show "View Lessons" button for students -->
{% if can_view_lesson() %}
<button onclick="viewLessons()">View Lessons</button>
{% endif %}

<!-- Show "My Lessons" button for teachers -->
{% if can_view_my_lessons() %}
<button onclick="viewMyLessons()">My Lessons</button>
{% endif %}

<!-- Show "Upload PDF" button for teachers -->
{% if can_upload_pdf() %}
<button onclick="uploadPDF()">Upload PDF</button>
{% endif %}

<!-- Show admin panel for admins -->
{% if is_admin() %}
<div class="admin-panel">
    <h2>Admin Panel</h2>
    {% if can_view_all_users() %}
    <button onclick="viewAllUsers()">View All Users</button>
    {% endif %}
    {% if can_view_teacher_records() %}
    <button onclick="viewTeacherRecords()">Teacher Records</button>
    {% endif %}
    {% if can_view_student_records() %}
    <button onclick="viewStudentRecords()">Student Records</button>
    {% endif %}
</div>
{% endif %}
```

### Example 2: Role-Based Navigation

```html
<nav>
    <ul>
        <li><a href="/">Home</a></li>
        
        {% if is_student() %}
        <li><a href="/lessons">View Lessons</a></li>
        {% endif %}
        
        {% if is_teacher() %}
        <li><a href="/my-lessons">My Lessons</a></li>
        <li><a href="/upload-pdf">Upload PDF</a></li>
        {% endif %}
        
        {% if is_admin() %}
        <li><a href="/admin/users">All Users</a></li>
        <li><a href="/admin/lessons">All Lessons</a></li>
        <li><a href="/admin/teachers">Teacher Records</a></li>
        <li><a href="/admin/students">Student Records</a></li>
        {% endif %}
    </ul>
</nav>
```

### Example 3: Using Role Features Dictionary

```html
{% set features = rbac_features() %}

{% if features.view_lesson %}
<button>View Lessons</button>
{% endif %}

{% if features.my_lessons %}
<button>My Lessons</button>
{% endif %}

{% if features.upload_pdf %}
<button>Upload PDF</button>
{% endif %}

{% if features.view_all_users %}
<button>View All Users</button>
{% endif %}
```

## JavaScript/API Usage

You can also check permissions via API endpoints:

```javascript
// Get current user role and permissions
fetch('/api/user/info')
    .then(response => response.json())
    .then(data => {
        const role = data.role;
        const features = data.features;
        
        // Show/hide UI elements based on role
        if (role === 'student') {
            showElement('view-lessons-btn');
        } else if (role === 'teacher') {
            showElement('view-lessons-btn');
            showElement('my-lessons-btn');
            showElement('upload-pdf-btn');
        } else if (role === 'admin') {
            // Show all elements
            showElement('view-lessons-btn');
            showElement('my-lessons-btn');
            showElement('upload-pdf-btn');
            showElement('admin-panel');
        }
    });
```

## Creating Admin Users

To create an admin user, you can either:

1. **Directly in the database:**
   ```sql
   UPDATE users SET role = 'admin' WHERE useremail = 'admin@example.com';
   ```

2. **Via Python script:**
   ```python
   from app.models import UserModel
   from app.utils.db import get_db
   from app.models.database_models import User
   
   db = get_db()
   user = db.query(User).filter(User.useremail == 'admin@example.com').first()
   if user:
       user.role = 'admin'
       db.commit()
   ```

3. **Via registration form (if you add admin option):**
   - Update the registration form to include 'admin' as a role option
   - Note: In production, you should restrict admin creation to existing admins only

## Best Practices

1. **Always use decorators for route protection** - Don't rely solely on frontend checks
2. **Check permissions in both frontend and backend** - Frontend for UX, backend for security
3. **Use permission checks instead of role checks when possible** - More flexible and maintainable
4. **Log permission denials** - Helps with security auditing
5. **Test all role combinations** - Ensure each role can only access what they should

## Migration Notes

When adding the admin role to an existing database:

1. The database constraint has been updated to allow 'admin' role
2. Existing users will remain as 'student' or 'teacher'
3. You can manually update users to 'admin' role as needed
4. No migration script is required - the constraint change is backward compatible


































