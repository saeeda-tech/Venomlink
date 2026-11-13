"""
Demonstration Facility Seed Script for VenomLink Prototype (USPF Changemaker Challenge 2026).

Creates clearly fictional simulated facilities across endemic geographic test zones
(Gombe, Plateau, Kaduna, Taraba) with synthetic stock histories, simulated clinical
consumption events, and hashed reporter PINs.

Security Rules:
- Contains ZERO hardcoded administrator or staff passwords.
- Never silently creates an administrator account with a default password.
- Refuses execution when DEBUG=False and DEMO_MODE=False (production/pilot guard).
- Uses nullable facility phone (`None` -> "Demo contact unavailable") and synthetic
  non-personal reporter identifiers (`DEMO-REP-XX`) so no real hospitals or phone
  numbers are exposed.
"""

import os
import secrets
from datetime import timedelta
from decimal import Decimal

import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'venomlink.settings')
django.setup()

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.utils import timezone
from apps.facilities.models import (
    Facility, FacilityType, Reporter, ReporterRole, UserProfile, UserRole
)
from apps.inventory.models import InventoryTransaction, TransactionType, SnakebiteCase, CaseOutcome
from apps.analytics.models import Alert
from apps.analytics.alert_engine import evaluate_all_alerts
from apps.ussd.models import UssdSessionState

User = get_user_model()


def _provision_admin_from_env():
    """
    Creates or updates an initial administrator ONLY when explicitly requested via
    VENOMLINK_INITIAL_ADMIN_USERNAME + VENOMLINK_INITIAL_ADMIN_PASSWORD env vars.
    Never falls back to hardcoded defaults.
    """
    username = os.getenv('VENOMLINK_INITIAL_ADMIN_USERNAME', '').strip()
    password = os.getenv('VENOMLINK_INITIAL_ADMIN_PASSWORD', '')
    email = os.getenv('VENOMLINK_INITIAL_ADMIN_EMAIL', '').strip()

    if not username or not password:
        print(
            "Skipping admin creation (no hardcoded passwords). "
            "Run `python manage.py createsuperuser` or set VENOMLINK_INITIAL_ADMIN_USERNAME "
            "and VENOMLINK_INITIAL_ADMIN_PASSWORD to provision an admin."
        )
        return

    user, _ = User.objects.get_or_create(
        username=username,
        defaults={'email': email, 'is_staff': True, 'is_superuser': True, 'is_active': True},
    )
    validate_password(password, user=user)
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
    print(f"Provisioned administrator account '{username}' from environment configuration.")


def run_seed():
    demo_mode = bool(getattr(settings, 'VENOMLINK_CONFIG', {}).get('SYSTEM_DEMO_MODE', False))
    if not settings.DEBUG and not demo_mode:
        raise SystemExit(
            "Refusing to run demo seed script in production mode (DEBUG=False and DEMO_MODE=False)."
        )

    print("Clearing previous demo records...")
    UssdSessionState.objects.all().delete()
    Alert.objects.all().delete()
    SnakebiteCase.objects.all().delete()
    InventoryTransaction.objects.all().delete()
    Reporter.objects.all().delete()
    Facility.objects.all().delete()

    # Optional explicit demo reporter PIN from environment, otherwise ephemeral random PIN per seed run
    configured_pin = os.getenv('VENOMLINK_DEMO_REPORTER_PIN', '').strip()
    ephemeral_pin = configured_pin or f"{secrets.randbelow(900000) + 100000}"

    now = timezone.now()

    facilities_data = [
        {
            'name': 'Demo Antivenom Hub Alpha',
            'facility_type': FacilityType.GENERAL_HOSPITAL,
            'state': 'Gombe',
            'lga': 'Kaltungo',
            'lat': Decimal('9.816667'),
            'lon': Decimal('11.316667'),
            'phone': None,
            'initial_stock': 8,
            'expiry': '06/27',
            'age_hours': 1,
            'lead_time': 3,
            'reporter_name': 'Demo Staff 01',
            'reporter_role': ReporterRole.PHARMACIST,
            'reporter_phone': 'DEMO-REP-01',
            'case_history': [(22, 4), (16, 4), (11, 3), (5, 4), (2, 3)],
        },
        {
            'name': 'Demo Clinic Beta',
            'facility_type': FacilityType.GENERAL_HOSPITAL,
            'state': 'Gombe',
            'lga': 'Billiri',
            'lat': Decimal('9.864700'),
            'lon': Decimal('11.224400'),
            'phone': None,
            'initial_stock': 16,
            'expiry': '08/27',
            'age_hours': 4,
            'lead_time': 3,
            'reporter_name': 'Demo Staff 02',
            'reporter_role': ReporterRole.PHARMACIST,
            'reporter_phone': 'DEMO-REP-02',
            'case_history': [(18, 2), (9, 2), (3, 2)],
        },
        {
            'name': 'Simulated Health Post Gamma',
            'facility_type': FacilityType.COMPREHENSIVE_HEALTH_CENTRE,
            'state': 'Gombe',
            'lga': 'Balanga',
            'lat': Decimal('9.583300'),
            'lon': Decimal('11.683300'),
            'phone': None,
            'initial_stock': 2,
            'expiry': '12/26',
            'age_hours': 6,
            'lead_time': 4,
            'reporter_name': 'Demo Staff 03',
            'reporter_role': ReporterRole.NURSE,
            'reporter_phone': 'DEMO-REP-03',
            'case_history': [(14, 2), (4, 2)],
        },
        {
            'name': 'Demo Rural Clinic Delta',
            'facility_type': FacilityType.COMPREHENSIVE_HEALTH_CENTRE,
            'state': 'Gombe',
            'lga': 'Balanga',
            'lat': Decimal('9.600000'),
            'lon': Decimal('11.550000'),
            'phone': None,
            'initial_stock': 4,
            'expiry': '01/27',
            'age_hours': 18,
            'lead_time': 3,
            'reporter_name': 'Demo Staff 04',
            'reporter_role': ReporterRole.STORE_OFFICER,
            'reporter_phone': 'DEMO-REP-04',
            'case_history': [(10, 2)],
        },
        {
            'name': 'Demo Central Depot Epsilon',
            'facility_type': FacilityType.SPECIALIST_HOSPITAL,
            'state': 'Gombe',
            'lga': 'Gombe',
            'lat': Decimal('10.289700'),
            'lon': Decimal('11.167300'),
            'phone': None,
            'initial_stock': 22,
            'expiry': '12/27',
            'age_hours': 2,
            'lead_time': 2,
            'reporter_name': 'Demo Staff 05',
            'reporter_role': ReporterRole.MEDICAL_OFFICER,
            'reporter_phone': 'DEMO-REP-05',
            'case_history': [(24, 3), (15, 2), (6, 3)],
        },
        {
            'name': 'Simulated Treatment Post Zeta',
            'facility_type': FacilityType.COMPREHENSIVE_HEALTH_CENTRE,
            'state': 'Plateau',
            'lga': 'Langtang South',
            'lat': Decimal('8.878900'),
            'lon': Decimal('9.812200'),
            'phone': None,
            'initial_stock': 12,
            'expiry': '05/27',
            'age_hours': 3,
            'lead_time': 3,
            'reporter_name': 'Demo Staff 06',
            'reporter_role': ReporterRole.PHARMACIST,
            'reporter_phone': 'DEMO-REP-06',
            'case_history': [(20, 2), (12, 2), (4, 2)],
        },
        {
            'name': 'Demo Facility Eta',
            'facility_type': FacilityType.GENERAL_HOSPITAL,
            'state': 'Plateau',
            'lga': 'Shendam',
            'lat': Decimal('8.883300'),
            'lon': Decimal('9.500000'),
            'phone': None,
            'initial_stock': 3,
            'expiry': '04/27',
            'age_hours': 8,
            'lead_time': 3,
            'reporter_name': 'Demo Staff 07',
            'reporter_role': ReporterRole.NURSE,
            'reporter_phone': 'DEMO-REP-07',
            'case_history': [(21, 5), (14, 4), (7, 5), (2, 4)],
        },
        {
            'name': 'Simulated Clinic Theta',
            'facility_type': FacilityType.GENERAL_HOSPITAL,
            'state': 'Plateau',
            'lga': 'Pankshin',
            'lat': Decimal('9.333300'),
            'lon': Decimal('9.450000'),
            'phone': None,
            'initial_stock': 9,
            'expiry': '07/27',
            'age_hours': 72,
            'lead_time': 4,
            'reporter_name': 'Demo Staff 08',
            'reporter_role': ReporterRole.STORE_OFFICER,
            'reporter_phone': 'DEMO-REP-08',
            'case_history': [(19, 2), (8, 2)],
        },
        {
            'name': 'Demo Health Centre Iota',
            'facility_type': FacilityType.GENERAL_HOSPITAL,
            'state': 'Kaduna',
            'lga': "Jema'a",
            'lat': Decimal('9.583300'),
            'lon': Decimal('8.300000'),
            'phone': None,
            'initial_stock': 14,
            'expiry': '10/27',
            'age_hours': 10,
            'lead_time': 3,
            'reporter_name': 'Demo Staff 09',
            'reporter_role': ReporterRole.PHARMACIST,
            'reporter_phone': 'DEMO-REP-09',
            'case_history': [(25, 3), (15, 3), (5, 2)],
        },
        {
            'name': 'Simulated Health Post Kappa',
            'facility_type': FacilityType.COMPREHENSIVE_HEALTH_CENTRE,
            'state': 'Kaduna',
            'lga': 'Soba',
            'lat': Decimal('10.983300'),
            'lon': Decimal('8.050000'),
            'phone': None,
            'initial_stock': 7,
            'expiry': '06/27',
            'age_hours': 120,
            'lead_time': 3,
            'reporter_name': 'Demo Staff 10',
            'reporter_role': ReporterRole.COMMUNITY_HEALTH,
            'reporter_phone': 'DEMO-REP-10',
            'case_history': [],
        },
        {
            'name': 'Demo Rural Post Lambda',
            'facility_type': FacilityType.COMPREHENSIVE_HEALTH_CENTRE,
            'state': 'Taraba',
            'lga': 'Donga',
            'lat': Decimal('7.716700'),
            'lon': Decimal('10.050000'),
            'phone': None,
            'initial_stock': 1,
            'expiry': '06/27',
            'age_hours': 5,
            'lead_time': 4,
            'reporter_name': 'Demo Staff 11',
            'reporter_role': ReporterRole.NURSE,
            'reporter_phone': 'DEMO-REP-11',
            'case_history': [],
        },
    ]

    for item in facilities_data:
        fac = Facility.objects.create(
            name=item['name'],
            facility_type=item['facility_type'],
            state=item['state'],
            lga=item['lga'],
            latitude=item['lat'],
            longitude=item['lon'],
            phone=item['phone'],
            is_demo=True,
            verified=True,
            resupply_lead_time_days=item['lead_time'],
            safety_buffer_days=2,
        )

        reporter = Reporter(
            facility=fac,
            full_name=item['reporter_name'],
            role=item['reporter_role'],
            phone_number=item['reporter_phone'],
            active=True,
        )
        reporter.set_pin(ephemeral_pin)
        reporter.save()

        total_used = sum(vials for _, vials in item['case_history'])
        starting_stock = item['initial_stock'] + total_used

        arrival_time = now - timedelta(days=28)
        InventoryTransaction.objects.create(
            facility=fac,
            reporter=reporter,
            transaction_type=TransactionType.RECEIVED,
            quantity=starting_stock,
            stock_after=starting_stock,
            expiry_date=item['expiry'],
            batch_number='DEMO-LOT-2026',
            timestamp=arrival_time,
            notes='Simulated baseline batch delivery',
        )

        running_stock = starting_stock
        for days_ago, vials_used in item['case_history']:
            case_time = now - timedelta(days=days_ago)
            running_stock -= vials_used
            SnakebiteCase.objects.create(
                facility=fac,
                reporter=reporter,
                date=case_time.date(),
                vials_used=vials_used,
                outcome=CaseOutcome.RECOVERED,
                notes='Simulated anonymized envenomation treatment event.',
            )
            InventoryTransaction.objects.create(
                facility=fac,
                reporter=reporter,
                transaction_type=TransactionType.USED,
                quantity=-vials_used,
                stock_after=running_stock,
                expiry_date=item['expiry'],
                timestamp=case_time,
                notes=f'Simulated usage of {vials_used} vials',
            )

        confirm_time = now - timedelta(hours=item['age_hours'])
        InventoryTransaction.objects.create(
            facility=fac,
            reporter=reporter,
            transaction_type=TransactionType.STOCK_CONFIRMATION,
            quantity=0,
            stock_after=item['initial_stock'],
            expiry_date=item['expiry'],
            batch_number='DEMO-LOT-2026',
            timestamp=confirm_time,
            notes='Simulated physical inventory count',
        )

    _provision_admin_from_env()
    print(f"Created {len(facilities_data)} clearly labelled demonstration facilities (phone=None, is_demo=True).")
    print("Evaluating operational alerts across facilities...")
    evaluate_all_alerts()
    print("Demo seed completed successfully.")


if __name__ == '__main__':
    run_seed()
