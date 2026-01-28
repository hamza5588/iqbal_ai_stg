# RBAC (Role-Based Access Control) Module

This module provides a comprehensive Role-Based Access Control system for IqbalAI.

## Structure

```
app/rbac/
├── __init__.py           # Module exports
├── roles.py              # Role enum definitions
├── permissions.py        # Permission definitions and role-permission mappings
├── decorators.py        # Route protection decorators
├── utils.py             # Utility functions for permission checks
├── template_helpers.py   # Jinja2 template helper functions
├── README.md            # This file
└── USAGE_EXAMPLES.md    # Detailed usage examples
```

## Roles

### Student
- **Purpose**: View and access lessons
- **Key Features**: 
  - View lessons (public lessons)
  - View own profile
  - Create conversations

### Teacher
- **Purpose**: Create and manage lessons, upload PDFs
- **Key Features**:
  - All student permissions
  - View own lessons
  - Create lessons
  - Edit/delete own lessons
  - Upload PDF documents
  - Manage own documents

### Admin
- **Purpose**: Full system access and management
- **Key Features**:
  - All teacher permissions
  - View all lessons (from all teachers)
  - Edit/delete any lesson
  - View all users
  - Manage user roles
  - View teacher records
  - View student records
  - View all system records

## Quick Start

### In Routes

```python
from app.rbac.decorators import role_required, admin_only
from app.rbac.roles import Role

@bp.route('/admin/users')
@admin_only
def view_all_users():
    # Only admins can access
    pass
```

### In Templates

```html
{% if is_admin() %}
<div class="admin-panel">Admin Content</div>
{% endif %}

{% if can_upload_pdf() %}
<button>Upload PDF</button>
{% endif %}
```

### In Python Code

```python
from app.rbac.utils import is_admin, can_manage_lesson

if is_admin():
    # Admin logic
    pass

if can_manage_lesson(lesson_id):
    # Can manage this lesson
    pass
```

## Features

1. **Role Enum**: Type-safe role definitions
2. **Permission System**: Granular permission control
3. **Decorators**: Easy route protection
4. **Template Helpers**: Built-in Jinja2 helpers
5. **Utility Functions**: Helper functions for common checks
6. **Session Integration**: Role stored in session for performance

## Database Changes

The database model has been updated to support the 'admin' role:
- `User.role` constraint now allows: 'student', 'teacher', 'admin'
- No migration needed - backward compatible

## Integration

The RBAC system is automatically integrated:
- Template helpers are registered in Flask app
- Session stores user role on login
- All existing routes continue to work

## See Also

- `USAGE_EXAMPLES.md` - Detailed usage examples
- `permissions.py` - Full permission list
- `roles.py` - Role definitions


































