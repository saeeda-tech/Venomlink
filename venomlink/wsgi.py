"""WSGI config for VenomLink project."""
import os
from django.core.wsgi import get_wsgi_application

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'venomlink.settings')
application = get_wsgi_application()

from apps.facilities.security_checks import enforce_production_startup_safety  # noqa: E402
enforce_production_startup_safety()

