"""
Management command to deactivate legacy seeded demo accounts and optionally
provision or rotate the administrator account from environment variables.
"""

import os
from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from apps.facilities.models import UserProfile, UserRole
from apps.facilities.security_checks import (
    LEGACY_DEMO_USERNAMES,
    LEGACY_UNAUTHORISED_EMAIL_DOMAIN,
)


class Command(BaseCommand):
    help = "Deactivates unrotated legacy demo accounts and optionally sets admin password from env."

    def add_arguments(self, parser):
        parser.add_argument(
            '--rotate-admin-from-env',
            action='store_true',
            help='Create/rotate superuser using VENOMLINK_ADMIN_USERNAME and VENOMLINK_ADMIN_PASSWORD.',
        )

    def handle(self, *args, **options):
        User = get_user_model()
        disabled_count = 0

        legacy_qs = User.objects.filter(username__in=LEGACY_DEMO_USERNAMES)
        for user in legacy_qs:
            user.is_active = False
            user.set_unusable_password()
            user.save(update_fields=['is_active', 'password'])
            disabled_count += 1

        gov_qs = User.objects.filter(email__iendswith=LEGACY_UNAUTHORISED_EMAIL_DOMAIN)
        for user in gov_qs:
            user.email = ''
            if user.username != os.getenv('VENOMLINK_ADMIN_USERNAME', '').strip():
                user.is_active = False
                user.set_unusable_password()
            user.save()
            disabled_count += 1

        self.stdout.write(self.style.SUCCESS(f"Hardened {disabled_count} legacy account record(s)."))

        if options.get('rotate_admin_from_env'):
            username = os.getenv('VENOMLINK_ADMIN_USERNAME', '').strip()
            password = os.getenv('VENOMLINK_ADMIN_PASSWORD', '')
            email = os.getenv('VENOMLINK_ADMIN_EMAIL', '').strip()
            if not username or not password:
                raise CommandError(
                    "VENOMLINK_ADMIN_USERNAME and VENOMLINK_ADMIN_PASSWORD must both be set."
                )

            user, _ = User.objects.get_or_create(
                username=username,
                defaults={'email': email, 'is_staff': True, 'is_superuser': True, 'is_active': True},
            )
            try:
                validate_password(password, user=user)
            except ValidationError as exc:
                raise CommandError(f"Admin password rejected: {'; '.join(exc.messages)}")

            user.is_staff = True
            user.is_superuser = True
            user.is_active = True
            if email:
                user.email = email
            user.set_password(password)
            user.save()
            UserProfile.objects.update_or_create(
                user=user,
                defaults={'role': UserRole.SYSTEM_ADMIN, 'assigned_state': '', 'facility': None},
            )
            self.stdout.write(self.style.SUCCESS(f"Administrator '{username}' credentials updated."))
