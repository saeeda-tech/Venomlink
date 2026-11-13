"""
Security and Production Auth Validation Checks for VenomLink.

Registered with Django's system check framework and invoked at WSGI startup so
misconfigured production deployments fail closed rather than serving with demo
credentials or missing authentication middleware.
"""

from django.conf import settings
from django.core.checks import Error, Tags, register
from django.core.exceptions import ImproperlyConfigured
from django.db import connections
from django.db.utils import OperationalError, ProgrammingError

LEGACY_DEMO_USERNAMES = (
    'gombe_official',
    'plateau_official',
    'kaltungo_admin',
    'kaltungo_pharmacist',
)

LEGACY_UNAUTHORISED_EMAIL_DOMAIN = '@venomlink.gov.ng'


def _is_production_non_demo() -> bool:
    demo_mode = bool(getattr(settings, 'VENOMLINK_CONFIG', {}).get('SYSTEM_DEMO_MODE', False))
    return (not settings.DEBUG) and (not demo_mode)


def collect_production_security_errors() -> list:
    errors = []
    if not _is_production_non_demo():
        return errors

    middleware = getattr(settings, 'MIDDLEWARE', [])
    if 'django.contrib.auth.middleware.AuthenticationMiddleware' not in middleware:
        errors.append(
            Error(
                'AuthenticationMiddleware is missing in production configuration.',
                id='venomlink.E001',
            )
        )

    if not getattr(settings, 'LOGIN_URL', ''):
        errors.append(
            Error(
                'LOGIN_URL must be configured in production.',
                id='venomlink.E002',
            )
        )

    try:
        conn = connections['default']
        with conn.cursor():
            pass
        from django.contrib.auth import get_user_model
        User = get_user_model()

        legacy_users = User.objects.filter(
            is_active=True,
            username__in=LEGACY_DEMO_USERNAMES,
        ).values_list('username', flat=True)
        for uname in legacy_users:
            errors.append(
                Error(
                    f"Active legacy demo account '{uname}' detected in non-demo production environment. "
                    f"Run `python manage.py harden_demo_credentials` before starting.",
                    id='venomlink.E003',
                )
            )

        gov_email_users = User.objects.filter(
            is_active=True,
            email__iendswith=LEGACY_UNAUTHORISED_EMAIL_DOMAIN,
        ).values_list('username', flat=True)
        for uname in gov_email_users:
            errors.append(
                Error(
                    f"Active user '{uname}' retains legacy unrotated seeded credential metadata "
                    f"({LEGACY_UNAUTHORISED_EMAIL_DOMAIN}). Rotate password and update email.",
                    id='venomlink.E004',
                )
            )
    except (OperationalError, ProgrammingError):
        # Database tables may not exist yet (e.g. before initial migrate / during build).
        pass

    return errors


@register(Tags.security)
def check_production_auth_and_demo_safety(app_configs, **kwargs):
    return collect_production_security_errors()


def enforce_production_startup_safety():
    """Fail-closed WSGI startup guard for production non-demo deployments."""
    errors = collect_production_security_errors()
    if errors:
        raise ImproperlyConfigured("; ".join(err.msg for err in errors))
