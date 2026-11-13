"""Validation shared by public registration and admin account creation."""


def school_name_problem(value, *, required=True):
    if value is None or (isinstance(value, str) and not value.strip()):
        return 'School name is required' if required else ''
    if not isinstance(value, str):
        return 'School name must be text'
    if len(value.strip()) > 255:
        return 'School name must be 255 characters or fewer'
    return ''
