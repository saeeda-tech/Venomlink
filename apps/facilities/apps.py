from django.apps import AppConfig


class FacilitiesConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.facilities'
    verbose_name = 'Health Facilities & Reporters'

    def ready(self):
        from . import security_checks  # noqa: F401

