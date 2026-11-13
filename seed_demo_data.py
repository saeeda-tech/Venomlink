"""
Realistic Nigerian Health Facility Seed Script for VenomLink MVP.

Includes actual public-health facilities in high snakebite endemic zones
(Gombe, Plateau, Kaduna, Taraba) with inventory transactions,
clinical cases across 30 days, hashed reporter PINs, and RBAC user profiles.
"""

import os
import django
from datetime import timedelta
from decimal import Decimal

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'venomlink.settings')
django.setup()

from django.contrib.auth import get_user_model
from django.utils import timezone
from apps.facilities.models import (
    Facility, FacilityType, Reporter, ReporterRole, UserProfile, UserRole
)
from apps.inventory.models import InventoryTransaction, TransactionType, SnakebiteCase, CaseOutcome
from apps.analytics.models import Alert
from apps.analytics.alert_engine import evaluate_all_alerts
from apps.ussd.models import UssdSessionState

User = get_user_model()


def _seed_rbac_users(kaltungo_fac):
    """Creates demonstration RBAC accounts for each defined role."""
    # 1. SYSTEM_ADMIN
    sys_admin, _ = User.objects.update_or_create(
        username='admin',
        defaults={'email': 'admin@venomlink.gov.ng', 'is_staff': True, 'is_superuser': True}
    )
    sys_admin.set_password('VenomLinkAdmin2026!')
    sys_admin.save()
    UserProfile.objects.update_or_create(
        user=sys_admin,
        defaults={'role': UserRole.SYSTEM_ADMIN, 'assigned_state': '', 'facility': None}
    )

    # 2. STATE_OFFICIAL (Gombe)
    gombe_off, _ = User.objects.update_or_create(
        username='gombe_official',
        defaults={'email': 'gombe.logistics@venomlink.gov.ng', 'is_staff': False, 'is_superuser': False}
    )
    gombe_off.set_password('GombeState2026!')
    gombe_off.save()
    UserProfile.objects.update_or_create(
        user=gombe_off,
        defaults={'role': UserRole.STATE_OFFICIAL, 'assigned_state': 'Gombe', 'facility': None}
    )

    # 3. STATE_OFFICIAL (Plateau)
    plateau_off, _ = User.objects.update_or_create(
        username='plateau_official',
        defaults={'email': 'plateau.logistics@venomlink.gov.ng', 'is_staff': False, 'is_superuser': False}
    )
    plateau_off.set_password('PlateauState2026!')
    plateau_off.save()
    UserProfile.objects.update_or_create(
        user=plateau_off,
        defaults={'role': UserRole.STATE_OFFICIAL, 'assigned_state': 'Plateau', 'facility': None}
    )

    # 4. FACILITY_ADMIN (Kaltungo General Hospital)
    fac_admin, _ = User.objects.update_or_create(
        username='kaltungo_admin',
        defaults={'email': 'cmd.kaltungo@venomlink.gov.ng', 'is_staff': False, 'is_superuser': False}
    )
    fac_admin.set_password('KaltungoAdmin2026!')
    fac_admin.save()
    UserProfile.objects.update_or_create(
        user=fac_admin,
        defaults={'role': UserRole.FACILITY_ADMIN, 'assigned_state': 'Gombe', 'facility': kaltungo_fac}
    )

    # 5. FACILITY_REPORTER (Kaltungo General Hospital)
    fac_rep_user, _ = User.objects.update_or_create(
        username='kaltungo_pharmacist',
        defaults={'email': 'pharmacy.kaltungo@venomlink.gov.ng', 'is_staff': False, 'is_superuser': False}
    )
    fac_rep_user.set_password('KaltungoStaff2026!')
    fac_rep_user.save()
    UserProfile.objects.update_or_create(
        user=fac_rep_user,
        defaults={'role': UserRole.FACILITY_REPORTER, 'assigned_state': 'Gombe', 'facility': kaltungo_fac}
    )
    return fac_rep_user


def run_seed():
    print("Clearing previous demo records...")
    UssdSessionState.objects.all().delete()
    Alert.objects.all().delete()
    SnakebiteCase.objects.all().delete()
    InventoryTransaction.objects.all().delete()
    Reporter.objects.all().delete()
    Facility.objects.all().delete()

    now = timezone.now()

    facilities_data = [
        {
            'name': 'Kaltungo General Hospital',
            'facility_type': FacilityType.GENERAL_HOSPITAL,
            'state': 'Gombe',
            'lga': 'Kaltungo',
            'lat': Decimal('9.816667'),
            'lon': Decimal('11.316667'),
            'phone': '08034567891',
            'initial_stock': 8,
            'expiry': '06/27',
            'age_hours': 1,  # Confirmed 1 hour ago (TODAY)
            'lead_time': 3,
            'reporter_name': 'Hauwa Abubakar',
            'reporter_role': ReporterRole.PHARMACIST,
            'reporter_phone': '+2348030000001',
            'case_history': [(22, 4), (16, 4), (11, 3), (5, 4), (2, 3)],  # High confidence (5 events over 20d)
        },
        {
            'name': 'Billiri General Hospital',
            'facility_type': FacilityType.GENERAL_HOSPITAL,
            'state': 'Gombe',
            'lga': 'Billiri',
            'lat': Decimal('9.864700'),
            'lon': Decimal('11.224400'),
            'phone': '08034567892',
            'initial_stock': 16,  # Surplus available for redistribution
            'expiry': '08/27',
            'age_hours': 4,
            'lead_time': 3,
            'reporter_name': 'Bulus Danladi',
            'reporter_role': ReporterRole.PHARMACIST,
            'reporter_phone': '+2348030000002',
            'case_history': [(18, 2), (9, 2), (3, 2)],  # High confidence, low usage
        },
        {
            'name': 'Balanga Cottage Hospital',
            'facility_type': FacilityType.COMPREHENSIVE_HEALTH_CENTRE,
            'state': 'Gombe',
            'lga': 'Balanga',
            'lat': Decimal('9.583300'),
            'lon': Decimal('11.683300'),
            'phone': '08034567893',
            'initial_stock': 2,  # Critical low stock (1-2 vials threshold)
            'expiry': '12/26',   # Expiring soon (< 60 days from Nov 2026)
            'age_hours': 6,
            'lead_time': 4,
            'reporter_name': 'Grace Joshua',
            'reporter_role': ReporterRole.NURSE,
            'reporter_phone': '+2348030000003',
            'case_history': [(14, 2), (4, 2)],  # Sparse / Low confidence (2 events)
        },
        {
            'name': 'Bambam Comprehensive Health Centre',
            'facility_type': FacilityType.COMPREHENSIVE_HEALTH_CENTRE,
            'state': 'Gombe',
            'lga': 'Balanga',
            'lat': Decimal('9.600000'),
            'lon': Decimal('11.550000'),
            'phone': '08034567894',
            'initial_stock': 4,  # Low stock warning (3-4 vials threshold)
            'expiry': '01/27',   # Expiring within 60 days
            'age_hours': 18,
            'lead_time': 3,
            'reporter_name': 'Ibrahim Suleiman',
            'reporter_role': ReporterRole.STORE_OFFICER,
            'reporter_phone': '+2348030000004',
            'case_history': [(10, 2)],  # Sparse / Low confidence (1 event)
        },
        {
            'name': 'Gombe Specialist Hospital',
            'facility_type': FacilityType.SPECIALIST_HOSPITAL,
            'state': 'Gombe',
            'lga': 'Gombe',
            'lat': Decimal('10.289700'),
            'lon': Decimal('11.167300'),
            'phone': '08034567895',
            'initial_stock': 22,  # Primary central depot
            'expiry': '12/27',
            'age_hours': 2,
            'lead_time': 2,
            'reporter_name': 'Dr. Amina Bello',
            'reporter_role': ReporterRole.MEDICAL_OFFICER,
            'reporter_phone': '+2348030000005',
            'case_history': [(24, 3), (15, 2), (6, 3)],  # High confidence
        },
        {
            'name': 'Zamko Comprehensive Health Centre',
            'facility_type': FacilityType.COMPREHENSIVE_HEALTH_CENTRE,
            'state': 'Plateau',
            'lga': 'Langtang South',
            'lat': Decimal('8.878900'),
            'lon': Decimal('9.812200'),
            'phone': '08034567896',
            'initial_stock': 12,
            'expiry': '05/27',
            'age_hours': 3,
            'lead_time': 3,
            'reporter_name': 'Nanzing Lar',
            'reporter_role': ReporterRole.PHARMACIST,
            'reporter_phone': '+2348030000006',
            'case_history': [(20, 2), (12, 2), (4, 2)],  # High confidence
        },
        {
            'name': 'Shendam General Hospital',
            'facility_type': FacilityType.GENERAL_HOSPITAL,
            'state': 'Plateau',
            'lga': 'Shendam',
            'lat': Decimal('8.883300'),
            'lon': Decimal('9.500000'),
            'phone': '08034567897',
            'initial_stock': 3,  # Low stock
            'expiry': '04/27',
            'age_hours': 8,
            'lead_time': 3,
            'reporter_name': 'Mary Plang',
            'reporter_role': ReporterRole.NURSE,
            'reporter_phone': '+2348030000007',
            'case_history': [(21, 5), (14, 4), (7, 5), (2, 4)],  # High confidence, high demand
        },
        {
            'name': 'Pankshin General Hospital',
            'facility_type': FacilityType.GENERAL_HOSPITAL,
            'state': 'Plateau',
            'lga': 'Pankshin',
            'lat': Decimal('9.333300'),
            'lon': Decimal('9.450000'),
            'phone': '08034567898',
            'initial_stock': 9,
            'expiry': '07/27',
            'age_hours': 72,  # Stale data (> 48h)
            'lead_time': 4,
            'reporter_name': 'Gideon Dapiap',
            'reporter_role': ReporterRole.STORE_OFFICER,
            'reporter_phone': '+2348030000008',
            'case_history': [(19, 2), (8, 2)],  # Low confidence
        },
        {
            'name': 'Kafanchan General Hospital',
            'facility_type': FacilityType.GENERAL_HOSPITAL,
            'state': 'Kaduna',
            'lga': 'Jema\'a',
            'lat': Decimal('9.583300'),
            'lon': Decimal('8.300000'),
            'phone': '08034567899',
            'initial_stock': 14,
            'expiry': '10/27',
            'age_hours': 10,
            'lead_time': 3,
            'reporter_name': 'Sunday Katung',
            'reporter_role': ReporterRole.PHARMACIST,
            'reporter_phone': '+2348030000009',
            'case_history': [(25, 3), (15, 3), (5, 2)],  # High confidence
        },
        {
            'name': 'Soba Comprehensive Health Centre',
            'facility_type': FacilityType.COMPREHENSIVE_HEALTH_CENTRE,
            'state': 'Kaduna',
            'lga': 'Soba',
            'lat': Decimal('10.983300'),
            'lon': Decimal('8.050000'),
            'phone': '08034567810',
            'initial_stock': 7,
            'expiry': '06/27',
            'age_hours': 120,  # Stale data (5 days ago)
            'lead_time': 3,
            'reporter_name': 'Fatima Sani',
            'reporter_role': ReporterRole.COMMUNITY_HEALTH,
            'reporter_phone': '+2348030000010',
            'case_history': [],  # Insufficient data (0 cases in 30d)
        },
        {
            'name': 'Donga Comprehensive Health Centre',
            'facility_type': FacilityType.COMPREHENSIVE_HEALTH_CENTRE,
            'state': 'Taraba',
            'lga': 'Donga',
            'lat': Decimal('7.716700'),
            'lon': Decimal('10.050000'),
            'phone': '08034567811',
            'initial_stock': 1,  # Critical stock (1 vial) with zero history -> still triggers CRITICAL alert!
            'expiry': '06/27',
            'age_hours': 5,
            'lead_time': 4,
            'reporter_name': 'Emmanuel Nyame',
            'reporter_role': ReporterRole.NURSE,
            'reporter_phone': '+2348030000011',
            'case_history': [],  # Insufficient data (0 cases in 30d)
        }
    ]

    kaltungo_fac = None
    kaltungo_reporter = None

    for item in facilities_data:
        fac = Facility.objects.create(
            name=item['name'],
            facility_type=item['facility_type'],
            state=item['state'],
            lga=item['lga'],
            latitude=item['lat'],
            longitude=item['lon'],
            phone=item['phone'],
            verified=True,
            resupply_lead_time_days=item['lead_time'],
            safety_buffer_days=2
        )

        reporter = Reporter(
            facility=fac,
            full_name=item['reporter_name'],
            role=item['reporter_role'],
            phone_number=item['reporter_phone'],
            active=True
        )
        reporter.set_pin('1234')
        reporter.save()

        if 'kaltungo' in fac.name.lower():
            kaltungo_fac = fac
            kaltungo_reporter = reporter

        total_used = sum(vials for _, vials in item['case_history'])
        starting_stock = item['initial_stock'] + total_used

        # Baseline stock arrival 28 days ago
        arrival_time = now - timedelta(days=28)
        InventoryTransaction.objects.create(
            facility=fac,
            reporter=reporter,
            transaction_type=TransactionType.RECEIVED,
            quantity=starting_stock,
            stock_after=starting_stock,
            expiry_date=item['expiry'],
            batch_number='LOT-2026-AV',
            timestamp=arrival_time,
            notes='Initial clinical batch delivery via State Medical Stores'
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
                notes='Echis ocellatus (carpet viper) envenomation treated.'
            )
            InventoryTransaction.objects.create(
                facility=fac,
                reporter=reporter,
                transaction_type=TransactionType.USED,
                quantity=-vials_used,
                stock_after=running_stock,
                expiry_date=item['expiry'],
                timestamp=case_time,
                notes=f'Administered {vials_used} vials for clinical emergency'
            )

        # Latest stock confirmation
        confirm_time = now - timedelta(hours=item['age_hours'])
        InventoryTransaction.objects.create(
            facility=fac,
            reporter=reporter,
            transaction_type=TransactionType.STOCK_CONFIRMATION,
            quantity=0,
            stock_after=item['initial_stock'],
            expiry_date=item['expiry'],
            batch_number='LOT-2026-AV',
            timestamp=confirm_time,
            notes='Verified physical inventory count'
        )

    if kaltungo_fac:
        rep_user = _seed_rbac_users(kaltungo_fac)
        if kaltungo_reporter:
            kaltungo_reporter.user = rep_user
            kaltungo_reporter.save(update_fields=['user'])

    print(f"Created {len(facilities_data)} realistic demo facilities with hashed reporter PINs (default demo PIN: 1234).")
    print("Evaluating operational alerts across facilities...")
    evaluate_all_alerts()
    print("Demo seed completed successfully.")


if __name__ == '__main__':
    run_seed()
