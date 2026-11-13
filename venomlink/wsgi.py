"""WSGI config for VenomLink project."""
import os
from django.core.wsgi import get_wsgi_application

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'venomlink.settings')
application = get_wsgi_application()

