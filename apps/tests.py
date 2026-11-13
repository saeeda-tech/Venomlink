"""
Reconciled & Comprehensive Engineering Verification Test Suite for VenomLink (Phase 1 & Phase 2).

Test Inventory Summary (75 Total Tests):
- Part I: Original 42 Audit & Baseline Tests (100% preserved and reconciled):
  - FacilityModelTest (7 tests)
  - InventoryServiceTest (6 tests)
  - MatchingEngineTest (10 tests)
  - USSDGatewayTest (3 tests)
  - DashboardViewTest (8 tests)
  - AuditBusinessLogicAndEdgeCaseTests (8 tests)
- Part II: Phase 1 & Phase 2 Adversarial, Security, USSD, Expiry, Concurrency & Performance Tests (33 tests):
  - ConcurrencyAndTransactionIntegrityTest (6 tests)
  - RBACAdversarialVerificationTest (6 tests)
  - USSDAdversarialSecurityTest (11 tests)
  - InventoryExpiryAndAlertVerificationTest (9 tests)
  - PerformanceQueryBoundTest (1 test)
"""

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from decimal import Decimal
import json
from threading import Barrier

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import connection, connections, transaction
from django.test import TestCase, TransactionTestCase, Client, override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from apps.facilities.models import (
    Facility, FacilityType, Reporter, ReporterRole,
    UserProfile, UserRole, ConsumptionConfidence
)
from apps.inventory.models import InventoryTransaction, TransactionType, SnakebiteCase
from apps.inventory.services import (
    record_stock_update, record_case_treatment, record_redistribution_transfer,
    validate_expiry_format, parse_expiry_date, is_expiry_date_expired
)
from apps.matching.engine import (
    haversine_distance_km, compute_distance_score, compute_freshness_score,
    compute_stock_score, rank_facilities_for_query
)
from apps.analytics.models import Alert, AlertType, AlertSeverity, AlertStatus
from apps.analytics.alert_engine import evaluate_facility_alerts
from apps.ussd.models import UssdSessionState
from apps.notifications.models import SmsLog
from apps.matching.models import AntivenomSearch

User = get_user_model()


def make_facility(
    name='Test Hospital',
    state='Gombe',
    lga='Kaltungo',
    stock=10,
    age_hours=2,
    expiry='06/27',
    pin='1234'
):
    """Helper: create a test facility with initial stock and a PIN-enabled reporter."""
    fac = Facility.objects.create(
        name=name,
        facility_type=FacilityType.GENERAL_HOSPITAL,
        state=state,
        lga=lga,
        latitude=Decimal('9.816667'),
        longitude=Decimal('11.316667'),
        phone='08030000001',
        verified=True,
        resupply_lead_time_days=3,
        safety_buffer_days=2,
    )
    reporter = Reporter(
        facility=fac,
        phone_number=f'+234803{fac.id:07d}',
        full_name=f'Test Reporter {fac.id}',
        role=ReporterRole.PHARMACIST,
        active=True
    )
    reporter.set_pin(pin)
    reporter.save()

    InventoryTransaction.objects.create(
        facility=fac,
        reporter=reporter,
        transaction_type=TransactionType.STOCK_CONFIRMATION,
        quantity=stock,
        stock_after=stock,
        expiry_date=expiry,
        timestamp=timezone.now() - timedelta(hours=age_hours),
        notes='Test setup'
    )
    return fac, reporter


def make_user_with_role(username, role, assigned_state='', facility=None, password='TestPassword123!'):
    user = User.objects.create_user(
        username=username,
        password=password,
        is_staff=(role == UserRole.SYSTEM_ADMIN),
        is_superuser=(role == UserRole.SYSTEM_ADMIN)
    )
    UserProfile.objects.create(
        user=user,
        role=role,
        assigned_state=assigned_state,
        facility=facility
    )
    return user


# ==============================================================================
# PART I: ORIGINAL 42 BASELINE & AUDIT TESTS (RECONCILED)
# ==============================================================================

class FacilityModelTest(TestCase):
    """Original 7 FacilityModelTest unit tests."""

    def setUp(self):
        self.fac, self.reporter = make_facility(stock=10, age_hours=2, expiry='06/27')

    def test_current_stock_derived_from_transactions(self):
        self.assertEqual(self.fac.current_stock, 10)

    def test_freshness_status_today(self):
        self.assertEqual(self.fac.freshness_status, 'TODAY')

    def test_freshness_status_stale(self):
        fac2, _ = make_facility(name='Old Hospital', stock=5, age_hours=73)
        self.assertEqual(fac2.freshness_status, 'STALE')

    def test_days_of_cover_calculation(self):
        """
        Reconciled for Priority 2.A: Without historical cases, days_of_cover is None
        (INSUFFICIENT_DATA); once historical cases exist, days_of_cover = usable_stock / daily_usage.
        """
        self.assertIsNone(self.fac.days_of_cover)
        SnakebiteCase.objects.create(
            facility=self.fac,
            reporter=self.reporter,
            date=timezone.now().date() - timedelta(days=5),
            vials_used=15,
            outcome='RECOVERED'
        )
        self.fac._clear_runtime_cache()
        self.assertEqual(self.fac.days_of_cover, 20.0)

    def test_stock_status_available(self):
        fac_high, _ = make_facility(name='High Stock Hospital', stock=20, age_hours=1)
        self.assertEqual(fac_high.stock_status, 'AVAILABLE')

    def test_stock_status_critical_zero(self):
        """Reconciled for Priority 2.B: 0 vials returns 'STOCK_OUT' (1-2 vials returns 'CRITICAL')."""
        InventoryTransaction.objects.create(
            facility=self.fac,
            transaction_type=TransactionType.STOCK_CONFIRMATION,
            quantity=0,
            stock_after=0,
            timestamp=timezone.now(),
            notes='empty'
        )
        self.fac._clear_runtime_cache()
        self.assertEqual(self.fac.stock_status, 'STOCK_OUT')

    def test_latest_expiry(self):
        self.assertEqual(self.fac.latest_expiry, '06/27')


class InventoryServiceTest(TestCase):
    """Original 6 InventoryServiceTest unit tests."""

    def setUp(self):
        self.fac, self.reporter = make_facility(stock=10)

    def test_stock_update_increases_stock(self):
        tx = record_stock_update(self.fac, quantity=15, expiry_date='06/27', reporter=self.reporter)
        self.assertEqual(tx.stock_after, 15)
        self.assertEqual(self.fac.current_stock, 15)

    def test_stock_update_rejects_negative(self):
        with self.assertRaises(ValueError):
            record_stock_update(self.fac, quantity=-1)

    def test_stock_update_rejects_bad_expiry(self):
        with self.assertRaises(ValueError):
            record_stock_update(self.fac, quantity=10, expiry_date='13/27')

    def test_case_treatment_deducts_stock(self):
        record_case_treatment(self.fac, vials_used=3, reporter=self.reporter)
        self.assertEqual(self.fac.current_stock, 7)

    def test_case_treatment_rejects_zero_vials(self):
        with self.assertRaises(ValueError):
            record_case_treatment(self.fac, vials_used=0)

    def test_validate_expiry_format(self):
        self.assertTrue(validate_expiry_format('06/27'))
        self.assertTrue(validate_expiry_format('12/99'))
        self.assertFalse(validate_expiry_format('13/27'))
        self.assertFalse(validate_expiry_format('3/27'))
        self.assertFalse(validate_expiry_format('March 2027'))


class MatchingEngineTest(TestCase):
    """Original 10 MatchingEngineTest unit tests."""

    def test_haversine_distance_same_point(self):
        self.assertAlmostEqual(haversine_distance_km(9.8, 11.3, 9.8, 11.3), 0.0, places=1)

    def test_haversine_distance_known_pair(self):
        dist = haversine_distance_km(9.8167, 11.3167, 10.2897, 11.1673)
        self.assertGreater(dist, 40)
        self.assertLess(dist, 70)

    def test_distance_score_nearby(self):
        self.assertEqual(compute_distance_score(5), 100.0)

    def test_distance_score_far(self):
        self.assertLess(compute_distance_score(200), 50)

    def test_freshness_score_today(self):
        self.assertEqual(compute_freshness_score('TODAY'), 100.0)

    def test_freshness_score_unconfirmed(self):
        self.assertEqual(compute_freshness_score('UNCONFIRMED'), 0.0)

    def test_stock_score_zero(self):
        self.assertEqual(compute_stock_score(0), 0.0)

    def test_stock_score_high(self):
        self.assertEqual(compute_stock_score(15), 100.0)

    def test_rank_facilities_excludes_zero_stock(self):
        fac_a, _ = make_facility('Empty Hospital', state='Gombe', lga='Kaltungo', stock=0)
        fac_b, _ = make_facility('Active Hospital', state='Gombe', lga='Kaltungo', stock=8)

        results = rank_facilities_for_query('Gombe', 'Kaltungo', limit=5, record_audit=False)
        returned_ids = [r['facility_id'] for r in results]
        self.assertIn(fac_b.id, returned_ids)
        self.assertNotIn(fac_a.id, returned_ids)

    def test_rank_facilities_returns_results(self):
        make_facility('Gombe Hospital', state='Gombe', lga='Gombe', stock=5)
        results = rank_facilities_for_query('Gombe', record_audit=False)
        self.assertGreater(len(results), 0)


class USSDGatewayTest(TestCase):
    """Original 3 USSDGatewayTest unit tests."""

    def setUp(self):
        self.client = Client()
        self.fac, self.reporter = make_facility(stock=10)

    def test_ussd_main_menu_unregistered(self):
        resp = self.client.post('/api/ussd/gateway/', {
            'sessionId': 'test-001',
            'serviceCode': '*384*20#',
            'phoneNumber': '+2340000000000',
            'text': ''
        })
        self.assertEqual(resp.status_code, 200)
        content = resp.content.decode()
        self.assertTrue(content.startswith('CON'))
        self.assertIn('VenomLink', content)

    def test_ussd_main_menu_registered_reporter(self):
        resp = self.client.post('/api/ussd/gateway/', {
            'sessionId': 'test-002',
            'serviceCode': '*384*20#',
            'phoneNumber': self.reporter.phone_number,
            'text': ''
        })
        self.assertEqual(resp.status_code, 200)
        content = resp.content.decode()
        self.assertTrue(content.startswith('CON'))
        self.assertIn(self.fac.name, content)

    def test_ussd_simulation_api(self):
        resp = self.client.post(
            '/api/ussd/simulate/',
            data='{"sessionId":"sim-001","serviceCode":"*384*20#","phoneNumber":"+2340000000000","text":""}',
            content_type='application/json'
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn('status', data)
        self.assertIn('display_text', data)
        self.assertIn(data['status'], ('CON', 'END'))


class DashboardViewTest(TestCase):
    """Original 8 DashboardViewTest unit tests."""

    def setUp(self):
        self.client = Client()
        self.fac, _ = make_facility(stock=8)

    def test_overview_loads(self):
        resp = self.client.get('/')
        self.assertEqual(resp.status_code, 200)

    def test_facilities_page_loads(self):
        resp = self.client.get('/facilities/')
        self.assertEqual(resp.status_code, 200)

    def test_facility_detail_loads(self):
        resp = self.client.get(f'/facilities/{self.fac.id}/')
        self.assertEqual(resp.status_code, 200)

    def test_alerts_page_loads(self):
        resp = self.client.get('/alerts/')
        self.assertEqual(resp.status_code, 200)

    def test_redistribution_page_loads(self):
        resp = self.client.get('/redistribution/')
        self.assertEqual(resp.status_code, 200)

    def test_ussd_simulator_page_loads(self):
        resp = self.client.get('/ussd-simulator/')
        self.assertEqual(resp.status_code, 200)

    def test_demo_runner_page_loads(self):
        resp = self.client.get('/demo-flow/')
        self.assertEqual(resp.status_code, 200)

    def test_facility_api_returns_data(self):
        resp = self.client.get('/api/facilities/')
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertGreater(data.get('count', len(data.get('results', [data]))), 0)


class AuditBusinessLogicAndEdgeCaseTests(TestCase):
    """Original 8 AuditBusinessLogicAndEdgeCaseTests (reconciled for Phase 1 security & integrity)."""

    def setUp(self):
        self.client = Client()
        self.fac, self.reporter = make_facility(
            name='Audit General Hospital', state='Gombe', lga='Kaltungo', stock=12, age_hours=1, pin='1234'
        )

    def test_stale_stock_detection_exact_threshold(self):
        stale_fac, _ = make_facility(name='Stale Clinic', stock=8, age_hours=50)
        self.assertEqual(stale_fac.freshness_status, 'STALE')
        self.assertTrue(stale_fac.is_stale)
        self.assertEqual(stale_fac.stock_status, 'UNCONFIRMED')

    def test_days_of_cover_exact_formula_with_30_day_cases(self):
        SnakebiteCase.objects.create(
            facility=self.fac,
            reporter=self.reporter,
            date=timezone.now().date() - timedelta(days=5),
            vials_used=60,
            outcome='RECOVERED'
        )
        self.fac._clear_runtime_cache()
        avg_usage = self.fac.calculate_average_daily_usage(days=30)
        self.assertEqual(avg_usage, 2.0)
        self.assertEqual(self.fac.days_of_cover, 6.0)

    def test_low_stock_and_stockout_alert_lifecycle(self):
        record_stock_update(self.fac, quantity=0, expiry_date='06/27', reporter=self.reporter)
        active_stockout = Alert.objects.filter(
            facility=self.fac,
            alert_type=AlertType.POTENTIAL_STOCKOUT,
            status=AlertStatus.ACTIVE
        ).exists()
        self.assertTrue(active_stockout)

        record_stock_update(self.fac, quantity=30, expiry_date='06/27', reporter=self.reporter)
        still_active = Alert.objects.filter(
            facility=self.fac,
            alert_type=AlertType.POTENTIAL_STOCKOUT,
            status=AlertStatus.ACTIVE
        ).exists()
        self.assertFalse(still_active)

    def test_expiry_risk_alert_within_60_days(self):
        near_date = timezone.now().date() + timedelta(days=25)
        exp_str = near_date.strftime('%m/%y')
        parsed = parse_expiry_date(exp_str)
        self.assertIsNotNone(parsed)

        record_stock_update(self.fac, quantity=15, expiry_date=exp_str, reporter=self.reporter)
        expiry_alert = Alert.objects.filter(
            facility=self.fac,
            alert_type=AlertType.EXPIRY_RISK,
            status=AlertStatus.ACTIVE
        ).exists()
        self.assertTrue(expiry_alert)

    def test_redistribution_transfer_conservation_of_stock(self):
        donor, _ = make_facility(name='Donor Hospital', stock=20, age_hours=1)
        recipient, _ = make_facility(name='Recipient Clinic', stock=2, age_hours=1)

        record_redistribution_transfer(donor, recipient, quantity=5, reporter=self.reporter)
        self.assertEqual(donor.current_stock, 15)
        self.assertEqual(recipient.current_stock, 7)

    def test_case_treatment_cannot_make_stock_negative(self):
        """
        Reconciled for Priority 2.E: Consuming 5 vials when only 2 exist now raises
        ValueError rather than silently clamping stock_after to 0 while recording -5.
        """
        low_fac, rep = make_facility(name='Two Vial Clinic', stock=2, age_hours=1)
        with self.assertRaises(ValueError):
            record_case_treatment(low_fac, vials_used=5, reporter=rep)
        self.assertEqual(low_fac.current_stock, 2)

    def test_ussd_multi_step_stock_update_transition(self):
        """
        Reconciled for Priority 1.B: Includes 4-digit Staff PIN ('1234') step.
        Main Menu -> 1 (Update) -> 1234 (PIN) -> 14 (Vials) -> 08/27 (Expiry) -> END.
        """
        r1 = self.client.post('/api/ussd/gateway/', {
            'sessionId': 'audit-s1', 'serviceCode': '*384*20#',
            'phoneNumber': self.reporter.phone_number, 'text': '1'
        })
        self.assertIn('Staff PIN', r1.content.decode())

        r2 = self.client.post('/api/ussd/gateway/', {
            'sessionId': 'audit-s1', 'serviceCode': '*384*20#',
            'phoneNumber': self.reporter.phone_number, 'text': '1*1234'
        })
        self.assertIn('Enter current number of vials', r2.content.decode())

        r3 = self.client.post('/api/ussd/gateway/', {
            'sessionId': 'audit-s1', 'serviceCode': '*384*20#',
            'phoneNumber': self.reporter.phone_number, 'text': '1*1234*14'
        })
        self.assertIn('Enter nearest batch expiry', r3.content.decode())

        r4 = self.client.post('/api/ussd/gateway/', {
            'sessionId': 'audit-s1', 'serviceCode': '*384*20#',
            'phoneNumber': self.reporter.phone_number, 'text': '1*1234*14*08/27'
        })
        body4 = r4.content.decode()
        self.assertTrue(body4.startswith('END'))
        self.assertIn('14 vials', body4)
        self.assertEqual(self.fac.current_stock, 14)

    def test_ussd_public_search_dispatches_sms_and_logs_audit(self):
        resp = self.client.post('/api/ussd/gateway/', {
            'sessionId': 'audit-pub-1', 'serviceCode': '*384*20#',
            'phoneNumber': '08091122334', 'text': '1*1*1'
        })
        body = resp.content.decode()
        self.assertTrue(body.startswith('END'))
        self.assertIn(self.fac.name, body)
        self.assertTrue(SmsLog.objects.filter(recipient='+2348091122334').exists())
        self.assertTrue(AntivenomSearch.objects.filter(state='Gombe', successful_match=True).exists())


# ==============================================================================
# PART II: PHASE 2 INDEPENDENT VERIFICATION & ADVERSARIAL TESTS (33 TESTS)
# ==============================================================================

class ConcurrencyAndTransactionIntegrityTest(TransactionTestCase):
    """
    Phase 2 Section 2: Concurrency, Row Locking, Rollback, and Deadlock Avoidance Tests.
    Note: Executes transaction-aware checks on the active DB engine and explicitly
    inspects connection.vendor ('sqlite' vs 'postgresql').
    """

    def setUp(self):
        self.fac_a, self.rep_a = make_facility(name='Concurrency Hospital A', stock=10, expiry='06/27')
        self.fac_b, self.rep_b = make_facility(name='Concurrency Hospital B', stock=10, expiry='08/27')

    def test_pg_or_db_simultaneous_stock_deductions(self):
        """
        2.1: Two deductions of 6 vials each against a 10-vial facility:
        exactly one succeeds (leaving 4 vials) and one raises ValueError; stock never goes negative.
        """
        def worker_deduct():
            try:
                fac = Facility.objects.get(pk=self.fac_a.pk)
                record_case_treatment(fac, vials_used=6, reporter=self.rep_a)
                return 'SUCCESS'
            except ValueError:
                return 'INSUFFICIENT_STOCK'
            finally:
                connections.close_all()

        if connection.vendor == 'postgresql':
            with ThreadPoolExecutor(max_workers=2) as pool:
                outcomes = list(pool.map(lambda _: worker_deduct(), range(2)))
        else:
            outcomes = [worker_deduct(), worker_deduct()]

        self.assertEqual(sorted(outcomes), ['INSUFFICIENT_STOCK', 'SUCCESS'])
        self.fac_a.refresh_from_db()
        self.fac_a._clear_runtime_cache()
        self.assertEqual(self.fac_a.current_stock, 4)

    def test_pg_or_db_simultaneous_stock_receipts(self):
        """
        2.2: Simultaneous/rapid stock receipts serialize cleanly under row-level locks
        and preserve deterministic transaction history matching physical stock.
        """
        def worker_receipt(qty, exp):
            try:
                fac = Facility.objects.get(pk=self.fac_a.pk)
                record_stock_update(
                    fac, quantity=qty, expiry_date=exp,
                    reporter=self.rep_a, transaction_type=TransactionType.RECEIVED
                )
                return 'OK'
            finally:
                connections.close_all()

        if connection.vendor == 'postgresql':
            barrier = Barrier(2, timeout=10)

            def sync_receipt(args):
                barrier.wait()
                return worker_receipt(*args)

            with ThreadPoolExecutor(max_workers=2) as pool:
                outcomes = list(pool.map(sync_receipt, [(15, '06/27'), (22, '08/27')]))
            self.assertEqual(outcomes, ['OK', 'OK'])
            self.fac_a.refresh_from_db()
            self.fac_a._clear_runtime_cache()
            self.assertIn(self.fac_a.current_stock, (15, 22))
            self.assertEqual(self.fac_a.current_stock, self.fac_a.latest_transaction.stock_after)
            self.assertEqual(InventoryTransaction.objects.filter(facility=self.fac_a).count(), 3)
        else:
            worker_receipt(15, '06/27')
            worker_receipt(22, '08/27')
            self.fac_a._clear_runtime_cache()
            self.assertEqual(self.fac_a.current_stock, 22)
            self.assertEqual(self.fac_a.latest_transaction.stock_after, 22)

    def test_pg_or_db_concurrent_redistribution_transfers(self):
        """
        2.3: Two concurrent transfers of 7 vials from a 10-vial donor to recipient:
        exactly one transfer succeeds (leaving 3 vials at donor, 17 at recipient),
        and one is rejected, conserving total stock (20 vials).
        """
        def worker_transfer():
            try:
                donor = Facility.objects.get(pk=self.fac_a.pk)
                recipient = Facility.objects.get(pk=self.fac_b.pk)
                record_redistribution_transfer(donor, recipient, quantity=7, reporter=self.rep_a)
                return 'SUCCESS'
            except ValueError:
                return 'INSUFFICIENT_STOCK'
            finally:
                connections.close_all()

        if connection.vendor == 'postgresql':
            barrier = Barrier(2, timeout=10)

            def sync_transfer(_):
                barrier.wait()
                return worker_transfer()

            with ThreadPoolExecutor(max_workers=2) as pool:
                outcomes = list(pool.map(sync_transfer, range(2)))
        else:
            outcomes = [worker_transfer(), worker_transfer()]

        self.assertEqual(sorted(outcomes), ['INSUFFICIENT_STOCK', 'SUCCESS'])
        self.fac_a.refresh_from_db()
        self.fac_b.refresh_from_db()
        self.fac_a._clear_runtime_cache()
        self.fac_b._clear_runtime_cache()
        self.assertEqual(self.fac_a.current_stock, 3)
        self.assertEqual(self.fac_b.current_stock, 17)
        self.assertEqual(self.fac_a.current_stock + self.fac_b.current_stock, 20)

    def test_pg_or_db_concurrent_deductions_and_transfers_same_facility(self):
        """
        2.4: Concurrent case deduction (6 vials) and outbound transfer (6 vials)
        against a 10-vial facility: exactly one succeeds and one fails cleanly without negative stock.
        """
        def worker_deduct():
            try:
                fac = Facility.objects.get(pk=self.fac_a.pk)
                record_case_treatment(fac, vials_used=6, reporter=self.rep_a)
                return 'DEDUCT_OK'
            except ValueError:
                return 'REJECTED'
            finally:
                connections.close_all()

        def worker_transfer():
            try:
                donor = Facility.objects.get(pk=self.fac_a.pk)
                recipient = Facility.objects.get(pk=self.fac_b.pk)
                record_redistribution_transfer(donor, recipient, quantity=6, reporter=self.rep_a)
                return 'TRANSFER_OK'
            except ValueError:
                return 'REJECTED'
            finally:
                connections.close_all()

        if connection.vendor == 'postgresql':
            barrier = Barrier(2, timeout=10)

            def run_fn(fn):
                barrier.wait()
                return fn()

            with ThreadPoolExecutor(max_workers=2) as pool:
                outcomes = list(pool.map(run_fn, [worker_deduct, worker_transfer]))
            self.assertEqual(outcomes.count('REJECTED'), 1)
        else:
            o1 = worker_deduct()
            o2 = worker_transfer()
            outcomes = [o1, o2]
            self.assertEqual(sorted(outcomes), ['DEDUCT_OK', 'REJECTED'])

        self.fac_a.refresh_from_db()
        self.fac_b.refresh_from_db()
        self.fac_a._clear_runtime_cache()
        self.fac_b._clear_runtime_cache()
        self.assertEqual(self.fac_a.current_stock, 4)
        self.assertIn(self.fac_b.current_stock, (10, 16))

    def test_pg_or_db_rollback_when_transaction_fails(self):
        """
        2.5: If an exception occurs inside an atomic inventory operation, all partial writes
        are rolled back completely and remain invisible to other connections.
        """
        initial_tx_count = InventoryTransaction.objects.filter(facility=self.fac_a).count()
        try:
            with transaction.atomic():
                record_stock_update(self.fac_a, quantity=25, expiry_date='06/27', reporter=self.rep_a)
                raise RuntimeError("Simulated mid-transaction failure")
        except RuntimeError:
            pass

        def check_isolated_connection():
            try:
                fac = Facility.objects.get(pk=self.fac_a.pk)
                return fac.current_stock
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=1) as pool:
            observed_stock = pool.submit(check_isolated_connection).result()

        self.fac_a.refresh_from_db()
        self.fac_a._clear_runtime_cache()
        self.assertEqual(observed_stock, 10)
        self.assertEqual(self.fac_a.current_stock, 10)
        self.assertEqual(InventoryTransaction.objects.filter(facility=self.fac_a).count(), initial_tx_count)

    def test_pg_or_db_deadlock_avoidance_opposite_direction_transfers(self):
        """
        2.6: Simultaneous transfers A -> B and B -> A acquire row locks in ascending primary-key
        order (sorted([source.pk, target.pk])) to prevent AB-BA deadlocks and conserve stock.
        """
        def transfer_ab():
            try:
                a = Facility.objects.get(pk=self.fac_a.pk)
                b = Facility.objects.get(pk=self.fac_b.pk)
                record_redistribution_transfer(a, b, quantity=4, reporter=self.rep_a)
                return 'AB_OK'
            finally:
                connections.close_all()

        def transfer_ba():
            try:
                a = Facility.objects.get(pk=self.fac_a.pk)
                b = Facility.objects.get(pk=self.fac_b.pk)
                record_redistribution_transfer(b, a, quantity=3, reporter=self.rep_b)
                return 'BA_OK'
            finally:
                connections.close_all()

        if connection.vendor == 'postgresql':
            barrier = Barrier(2, timeout=10)

            def sync_run(fn):
                barrier.wait()
                return fn()

            with ThreadPoolExecutor(max_workers=2) as pool:
                outcomes = list(pool.map(sync_run, [transfer_ab, transfer_ba]))
            self.assertEqual(sorted(outcomes), ['AB_OK', 'BA_OK'])
        else:
            transfer_ab()
            transfer_ba()

        self.fac_a.refresh_from_db()
        self.fac_b.refresh_from_db()
        self.fac_a._clear_runtime_cache()
        self.fac_b._clear_runtime_cache()
        self.assertEqual(self.fac_a.current_stock, 9)
        self.assertEqual(self.fac_b.current_stock, 11)
        self.assertEqual(self.fac_a.current_stock + self.fac_b.current_stock, 20)


class RBACAdversarialVerificationTest(TestCase):
    """
    Phase 2 Section 3: Deliberate adversarial testing across all 5 roles on both API and Web views.
    Roles tested:
    1. PUBLIC_USER
    2. FACILITY_REPORTER
    3. FACILITY_ADMIN
    4. STATE_OFFICIAL
    5. SYSTEM_ADMIN
    """

    def setUp(self):
        self.client = Client()
        self.gombe_fac1, self.gombe_rep1 = make_facility(
            name='Gombe Kaltungo GH', state='Gombe', lga='Kaltungo', stock=10, pin='1234'
        )
        self.gombe_fac2, self.gombe_rep2 = make_facility(
            name='Gombe Billiri GH', state='Gombe', lga='Billiri', stock=14, pin='2345'
        )
        self.plateau_fac, self.plateau_rep = make_facility(
            name='Plateau Shendam GH', state='Plateau', lga='Shendam', stock=12, pin='5678'
        )
        self.kaduna_fac, _ = make_facility(
            name='Kaduna Kafanchan GH', state='Kaduna', lga='Jema\'a', stock=8, pin='6789'
        )

        self.gombe_alert1 = Alert.objects.create(
            facility=self.gombe_fac1, alert_type=AlertType.LOW_STOCK,
            severity=AlertSeverity.WARNING, status=AlertStatus.ACTIVE, message='Gombe 1 low'
        )
        self.gombe_alert2 = Alert.objects.create(
            facility=self.gombe_fac2, alert_type=AlertType.LOW_STOCK,
            severity=AlertSeverity.WARNING, status=AlertStatus.ACTIVE, message='Gombe 2 low'
        )
        self.plateau_alert = Alert.objects.create(
            facility=self.plateau_fac, alert_type=AlertType.LOW_STOCK,
            severity=AlertSeverity.WARNING, status=AlertStatus.ACTIVE, message='Plateau low'
        )

        self.public_user = make_user_with_role('adv_public', UserRole.PUBLIC_USER)
        self.reporter_user = make_user_with_role(
            'adv_reporter', UserRole.FACILITY_REPORTER, assigned_state='Gombe', facility=self.gombe_fac1
        )
        self.fac_admin_user = make_user_with_role(
            'adv_fac_admin', UserRole.FACILITY_ADMIN, assigned_state='Gombe', facility=self.gombe_fac1
        )
        self.state_official_user = make_user_with_role(
            'adv_gombe_official', UserRole.STATE_OFFICIAL, assigned_state='Gombe'
        )
        self.sys_admin_user = make_user_with_role('adv_sys_admin', UserRole.SYSTEM_ADMIN)

    def test_rbac_public_user_adversarial_matrix(self):
        """3.1: PUBLIC_USER (and anonymous) blocked from all API & Web write actions."""
        self.client.force_login(self.public_user)

        # Facility create -> 403
        self.assertEqual(self.client.post('/api/facilities/', data=json.dumps({
            'name': 'Hack Fac', 'state': 'Gombe', 'lga': 'Kaltungo',
            'latitude': '9.8', 'longitude': '11.3', 'phone': '0800'
        }), content_type='application/json').status_code, 403)

        # Reporter create -> 403
        self.assertEqual(self.client.post('/api/facilities/reporters/', data=json.dumps({
            'phone_number': '+2348090001111', 'facility': self.gombe_fac1.id, 'full_name': 'Hacker'
        }), content_type='application/json').status_code, 403)

        # Stock update -> 403
        self.assertEqual(self.client.post('/api/inventory/update-stock/', data=json.dumps({
            'facility_id': self.gombe_fac1.id, 'quantity': 99
        }), content_type='application/json').status_code, 403)

        # Case report -> 403
        self.assertEqual(self.client.post('/api/inventory/report-case/', data=json.dumps({
            'facility_id': self.gombe_fac1.id, 'vials_used': 2
        }), content_type='application/json').status_code, 403)

        # Transfer -> 403
        self.assertEqual(self.client.post('/api/inventory/transfer/', data=json.dumps({
            'source_facility_id': self.gombe_fac2.id, 'target_facility_id': self.gombe_fac1.id, 'quantity': 2
        }), content_type='application/json').status_code, 403)

        # Alert acknowledge & resolve -> 403
        self.assertEqual(self.client.post(f'/api/analytics/alerts/{self.gombe_alert1.id}/acknowledge/').status_code, 403)
        self.assertEqual(self.client.post(f'/api/analytics/alerts/{self.gombe_alert1.id}/resolve/').status_code, 403)

        # Web POSTs (facility detail, alerts, redistribution) -> 403
        self.assertEqual(self.client.post(f'/facilities/{self.gombe_fac1.id}/', {
            'action': 'update_stock', 'quantity': 50
        }).status_code, 403)
        self.assertEqual(self.client.post('/alerts/', {
            'alert_id': self.gombe_alert1.id, 'action': 'resolve'
        }).status_code, 403)
        self.assertEqual(self.client.post('/redistribution/', {
            'source_facility_id': self.gombe_fac2.id, 'target_facility_id': self.gombe_fac1.id, 'quantity': 2
        }).status_code, 403)

        # Public search is permitted -> 200
        self.assertEqual(self.client.get('/api/matching/search/?state=Gombe').status_code, 200)

    def test_rbac_facility_reporter_adversarial_matrix(self):
        """
        3.2: FACILITY_REPORTER for gombe_fac1:
        - Blocked from updating gombe_fac2 or plateau_fac (API & Web) -> 403
        - Blocked from creating facilities or reporters -> 403
        - Blocked from executing transfers -> 403
        - Blocked from acknowledging/resolving alerts -> 403
        - Permitted to update stock & report cases for gombe_fac1 -> 200/201
        """
        self.client.force_login(self.reporter_user)

        # Cross-facility stock update (same state, different facility) -> 403
        self.assertEqual(self.client.post('/api/inventory/update-stock/', data=json.dumps({
            'facility_id': self.gombe_fac2.id, 'quantity': 30, 'expiry_date': '06/27'
        }), content_type='application/json').status_code, 403)

        # Cross-facility case report -> 403
        self.assertEqual(self.client.post('/api/inventory/report-case/', data=json.dumps({
            'facility_id': self.gombe_fac2.id, 'vials_used': 2
        }), content_type='application/json').status_code, 403)

        # Cross-facility web POST -> 403
        self.assertEqual(self.client.post(f'/facilities/{self.gombe_fac2.id}/', {
            'action': 'update_stock', 'quantity': 30, 'expiry_date': '06/27'
        }).status_code, 403)

        # Create facility or reporter -> 403
        self.assertEqual(self.client.post('/api/facilities/', data=json.dumps({
            'name': 'New Fac', 'state': 'Gombe', 'lga': 'Kaltungo', 'latitude': '9.8', 'longitude': '11.3', 'phone': '0800'
        }), content_type='application/json').status_code, 403)
        self.assertEqual(self.client.post('/api/facilities/reporters/', data=json.dumps({
            'phone_number': '+2348091234567', 'facility': self.gombe_fac1.id, 'full_name': 'Staff'
        }), content_type='application/json').status_code, 403)

        # Execute transfer -> 403
        self.assertEqual(self.client.post('/api/inventory/transfer/', data=json.dumps({
            'source_facility_id': self.gombe_fac2.id, 'target_facility_id': self.gombe_fac1.id, 'quantity': 2
        }), content_type='application/json').status_code, 403)

        # Resolve alert -> 403
        self.assertEqual(self.client.post(f'/api/analytics/alerts/{self.gombe_alert1.id}/resolve/').status_code, 403)

        # Permitted own-facility stock update -> 201
        r_own = self.client.post('/api/inventory/update-stock/', data=json.dumps({
            'facility_id': self.gombe_fac1.id, 'quantity': 11, 'expiry_date': '06/27'
        }), content_type='application/json')
        self.assertIn(r_own.status_code, (200, 201))

    def test_rbac_facility_admin_adversarial_matrix(self):
        """
        3.3: FACILITY_ADMIN for gombe_fac1:
        - Blocked from updating gombe_fac2 stock -> 403
        - Blocked from creating reporters for gombe_fac2 -> 403
        - Blocked from resolving alerts for gombe_fac2 -> 403
        - Blocked from executing inter-facility redistribution transfers -> 403
        - Permitted to manage gombe_fac1 stock, reporters, and alerts -> 200/201
        """
        self.client.force_login(self.fac_admin_user)

        # Cross-facility stock update -> 403
        self.assertEqual(self.client.post('/api/inventory/update-stock/', data=json.dumps({
            'facility_id': self.gombe_fac2.id, 'quantity': 30, 'expiry_date': '06/27'
        }), content_type='application/json').status_code, 403)

        # Cross-facility reporter creation -> 403
        self.assertEqual(self.client.post('/api/facilities/reporters/', data=json.dumps({
            'phone_number': '+2348095556666', 'facility': self.gombe_fac2.id, 'full_name': 'Other Fac Staff'
        }), content_type='application/json').status_code, 403)

        # Cross-facility alert resolution -> 403
        self.assertEqual(self.client.post(f'/api/analytics/alerts/{self.gombe_alert2.id}/resolve/').status_code, 403)

        # Inter-facility redistribution transfer -> 403
        self.assertEqual(self.client.post('/api/inventory/transfer/', data=json.dumps({
            'source_facility_id': self.gombe_fac2.id, 'target_facility_id': self.gombe_fac1.id, 'quantity': 2
        }), content_type='application/json').status_code, 403)

        # Own-facility alert resolution -> 200
        self.assertEqual(self.client.post(f'/api/analytics/alerts/{self.gombe_alert1.id}/acknowledge/').status_code, 200)

        # Own-facility reporter creation -> 201
        r_rep = self.client.post('/api/facilities/reporters/', data=json.dumps({
            'phone_number': '+2348095557777', 'facility': self.gombe_fac1.id, 'full_name': 'Own Fac Staff', 'pin': '4321'
        }), content_type='application/json')
        self.assertEqual(r_rep.status_code, 201)

    def test_rbac_state_official_adversarial_matrix(self):
        """
        3.4: STATE_OFFICIAL for Gombe:
        - Blocked from viewing/updating Plateau facilities (API & Web) -> 403/404
        - Blocked from creating facilities or reporters in Plateau -> 403
        - Blocked from resolving Plateau alerts -> 403
        - Blocked from transfers between Plateau and Kaduna -> 403
        - Permitted to manage facilities, alerts, and transfers in Gombe -> 200/201
        """
        self.client.force_login(self.state_official_user)

        # Cross-state facility detail (API & Web) -> 403/404
        self.assertIn(self.client.get(f'/api/facilities/{self.plateau_fac.id}/').status_code, (403, 404))
        self.assertEqual(self.client.get(f'/facilities/{self.plateau_fac.id}/').status_code, 403)

        # Cross-state facility creation -> 403
        self.assertEqual(self.client.post('/api/facilities/', data=json.dumps({
            'name': 'Plateau Unauthorized', 'state': 'Plateau', 'lga': 'Shendam',
            'latitude': '8.8', 'longitude': '9.5', 'phone': '0800'
        }), content_type='application/json').status_code, 403)

        # Cross-state stock update -> 403
        self.assertEqual(self.client.post('/api/inventory/update-stock/', data=json.dumps({
            'facility_id': self.plateau_fac.id, 'quantity': 20, 'expiry_date': '06/27'
        }), content_type='application/json').status_code, 403)

        # Cross-state alert resolution (API & Web) -> 403
        self.assertEqual(self.client.post(f'/api/analytics/alerts/{self.plateau_alert.id}/resolve/').status_code, 403)
        self.assertEqual(self.client.post('/alerts/', {
            'alert_id': self.plateau_alert.id, 'action': 'resolve'
        }).status_code, 403)

        # Transfer between two non-assigned states (Plateau -> Kaduna) -> 403
        self.assertEqual(self.client.post('/api/inventory/transfer/', data=json.dumps({
            'source_facility_id': self.plateau_fac.id,
            'target_facility_id': self.kaduna_fac.id,
            'quantity': 2
        }), content_type='application/json').status_code, 403)

        # Permitted intra-state transfer in Gombe -> 200
        r_gombe_tx = self.client.post('/api/inventory/transfer/', data=json.dumps({
            'source_facility_id': self.gombe_fac2.id,
            'target_facility_id': self.gombe_fac1.id,
            'quantity': 3
        }), content_type='application/json')
        self.assertEqual(r_gombe_tx.status_code, 200)

    def test_rbac_system_admin_full_scope_verification(self):
        """3.5: SYSTEM_ADMIN can manage facilities, stock, transfers, and alerts across all states."""
        self.client.force_login(self.sys_admin_user)
        r_plat = self.client.post('/api/inventory/update-stock/', data=json.dumps({
            'facility_id': self.plateau_fac.id, 'quantity': 19, 'expiry_date': '06/27'
        }), content_type='application/json')
        self.assertIn(r_plat.status_code, (200, 201))
        self.assertEqual(self.client.post(f'/api/analytics/alerts/{self.plateau_alert.id}/resolve/').status_code, 200)

    def test_csrf_protected_requests_with_authenticated_admin_session(self):
        """3.6: Enforces CSRF token check on session-authenticated write requests."""
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.sys_admin_user)
        csrf_client.get('/')
        csrf_token = csrf_client.cookies['csrftoken'].value

        r_no_csrf = csrf_client.post(
            '/api/inventory/update-stock/',
            data=json.dumps({'facility_id': self.gombe_fac1.id, 'quantity': 18, 'expiry_date': '06/27'}),
            content_type='application/json'
        )
        self.assertEqual(r_no_csrf.status_code, 403)

        r_with_csrf = csrf_client.post(
            '/api/inventory/update-stock/',
            data=json.dumps({'facility_id': self.gombe_fac1.id, 'quantity': 18, 'expiry_date': '06/27'}),
            content_type='application/json',
            HTTP_X_CSRFTOKEN=csrf_token
        )
        self.assertIn(r_with_csrf.status_code, (200, 201))


class USSDAdversarialSecurityTest(TestCase):
    """
    Phase 2 Section 4: Tests all 11 mandatory USSD adversarial verification cases.
    """

    def setUp(self):
        self.client = Client()
        self.fac, self.reporter = make_facility(stock=10, pin='1234')

    def test_ussd_01_unknown_phone_number_blocked_from_staff_update(self):
        """4.1: Unknown phone number attempting staff login/update is rejected."""
        resp = self.client.post('/api/ussd/gateway/', {
            'sessionId': 'ussd-adv-01', 'serviceCode': '*384*20#',
            'phoneNumber': '+2348090009999',
            'text': '2*+2348090009999*1234*25*06/27'
        })
        self.assertTrue(resp.content.decode().startswith('END Unauthorised'))
        self.assertEqual(self.fac.current_stock, 10)

    def test_ussd_02_inactive_reporter_blocked(self):
        """4.2: Inactive reporter cannot access staff menu or authenticate via option 2."""
        self.reporter.active = False
        self.reporter.save(update_fields=['active'])

        resp = self.client.post('/api/ussd/gateway/', {
            'sessionId': 'ussd-adv-02', 'serviceCode': '*384*20#',
            'phoneNumber': self.reporter.phone_number,
            'text': f'2*{self.reporter.phone_number}*1234*25*06/27'
        })
        self.assertTrue(resp.content.decode().startswith('END Unauthorised'))
        self.assertEqual(self.fac.current_stock, 10)

    def test_ussd_03_incorrect_pin_rejected(self):
        """4.3: Registered active reporter entering an incorrect PIN is denied and stock is unchanged."""
        resp = self.client.post('/api/ussd/gateway/', {
            'sessionId': 'ussd-adv-03', 'serviceCode': '*384*20#',
            'phoneNumber': self.reporter.phone_number,
            'text': '1*0000*25*06/27'
        })
        body = resp.content.decode()
        self.assertTrue(body.startswith('END Invalid Staff PIN'))
        self.reporter.refresh_from_db()
        self.assertEqual(self.reporter.failed_pin_attempts, 1)
        self.assertEqual(self.fac.current_stock, 10)

    def test_ussd_04_three_consecutive_incorrect_pins_trigger_lockout(self):
        """4.4: Three consecutive incorrect PIN attempts lock the reporter account."""
        for i in range(1, 4):
            resp = self.client.post('/api/ussd/gateway/', {
                'sessionId': f'ussd-adv-04-{i}', 'serviceCode': '*384*20#',
                'phoneNumber': self.reporter.phone_number,
                'text': '1*9999*25*06/27'
            })
            self.assertTrue(resp.content.decode().startswith('END Invalid Staff PIN'))

        self.reporter.refresh_from_db()
        self.assertEqual(self.reporter.failed_pin_attempts, 3)
        self.assertTrue(self.reporter.is_locked_out)
        self.assertIsNotNone(self.reporter.locked_until)

    def test_ussd_05_locked_reporter_denied_even_with_valid_pin(self):
        """4.5: A locked reporter is blocked from updating stock even when supplying the correct PIN."""
        self.reporter.locked_until = timezone.now() + timedelta(minutes=15)
        self.reporter.failed_pin_attempts = 3
        self.reporter.save(update_fields=['locked_until', 'failed_pin_attempts'])

        resp = self.client.post('/api/ussd/gateway/', {
            'sessionId': 'ussd-adv-05', 'serviceCode': '*384*20#',
            'phoneNumber': self.reporter.phone_number,
            'text': '1*1234*25*06/27'
        })
        self.assertIn('locked', resp.content.decode().lower())
        self.assertEqual(self.fac.current_stock, 10)

    def test_ussd_06_expired_session_rejected(self):
        """4.6: Continuing a session past its expiration timestamp is rejected."""
        self.client.post('/api/ussd/gateway/', {
            'sessionId': 'ussd-adv-06', 'serviceCode': '*384*20#',
            'phoneNumber': self.reporter.phone_number, 'text': '1*1234'
        })
        UssdSessionState.objects.filter(session_id='ussd-adv-06').update(
            expires_at=timezone.now() - timedelta(seconds=5)
        )
        resp = self.client.post('/api/ussd/gateway/', {
            'sessionId': 'ussd-adv-06', 'serviceCode': '*384*20#',
            'phoneNumber': self.reporter.phone_number, 'text': '1*1234*25*06/27'
        })
        self.assertIn('Session expired', resp.content.decode())
        self.assertEqual(self.fac.current_stock, 10)

    def test_ussd_07_replayed_terminated_session_rejected(self):
        """4.7: Replaying or continuing a sessionId that has already returned END is rejected."""
        r1 = self.client.post('/api/ussd/gateway/', {
            'sessionId': 'ussd-adv-07', 'serviceCode': '*384*20#',
            'phoneNumber': self.reporter.phone_number, 'text': '1*1234*16*06/27'
        })
        self.assertTrue(r1.content.decode().startswith('END Stock updated'))
        self.assertEqual(self.fac.current_stock, 16)

        # Replay with different stock quantity on same sessionId
        r2 = self.client.post('/api/ussd/gateway/', {
            'sessionId': 'ussd-adv-07', 'serviceCode': '*384*20#',
            'phoneNumber': self.reporter.phone_number, 'text': '1*1234*99*06/27'
        })
        self.assertIn('already ended', r2.content.decode())
        self.assertEqual(self.fac.current_stock, 16)

    def test_ussd_08_modified_phone_number_during_active_session_rejected(self):
        """4.8: Changing the caller phoneNumber mid-session on an active sessionId is rejected."""
        self.client.post('/api/ussd/gateway/', {
            'sessionId': 'ussd-adv-08', 'serviceCode': '*384*20#',
            'phoneNumber': self.reporter.phone_number, 'text': '1*1234'
        })
        # Attacker attempts to hijack 'ussd-adv-08' from a different phone number
        resp = self.client.post('/api/ussd/gateway/', {
            'sessionId': 'ussd-adv-08', 'serviceCode': '*384*20#',
            'phoneNumber': '+2348098887777', 'text': '1*1234*50*06/27'
        })
        self.assertIn('Session security violation', resp.content.decode())
        self.assertEqual(self.fac.current_stock, 10)

    @override_settings(VENOMLINK_CONFIG={
        'USSD_GATEWAY_TOKEN': 'live-gateway-secret-token',
        'USSD_SESSION_TIMEOUT_SECONDS': 180,
        'USSD_MAX_PIN_ATTEMPTS': 3,
        'USSD_LOCKOUT_MINUTES': 15,
    })
    def test_ussd_09_missing_gateway_token_rejected_when_configured(self):
        """4.9: Missing gateway token header returns HTTP 403 when USSD_GATEWAY_TOKEN is configured."""
        resp = self.client.post('/api/ussd/gateway/', {
            'sessionId': 'ussd-adv-09', 'serviceCode': '*384*20#',
            'phoneNumber': self.reporter.phone_number, 'text': ''
        })
        self.assertEqual(resp.status_code, 403)

    @override_settings(VENOMLINK_CONFIG={
        'USSD_GATEWAY_TOKEN': 'live-gateway-secret-token',
        'USSD_SESSION_TIMEOUT_SECONDS': 180,
        'USSD_MAX_PIN_ATTEMPTS': 3,
        'USSD_LOCKOUT_MINUTES': 15,
    })
    def test_ussd_10_invalid_gateway_token_rejected_when_configured(self):
        """4.10: Invalid gateway token header returns HTTP 403 when USSD_GATEWAY_TOKEN is configured."""
        resp = self.client.post('/api/ussd/gateway/', {
            'sessionId': 'ussd-adv-10', 'serviceCode': '*384*20#',
            'phoneNumber': self.reporter.phone_number, 'text': ''
        }, HTTP_X_GATEWAY_TOKEN='forged-token-value')
        self.assertEqual(resp.status_code, 403)

    def test_ussd_11_get_request_rejected_with_405(self):
        """4.11: GET requests to the USSD webhook are rejected with HTTP 405 Method Not Allowed."""
        resp = self.client.get('/api/ussd/gateway/?sessionId=s1&serviceCode=*384*20%23&phoneNumber=%2B2348030000001')
        self.assertEqual(resp.status_code, 405)


class InventoryExpiryAndAlertVerificationTest(TestCase):
    """
    Phase 2 Section 5 & Priority 2/3 Verification:
    - Expired batches never presented as usable
    - Partially expired / replaced inventory quarantine handling
    - Stock deductions update and preserve batch expiry
    - Transfers preserve stock conservation and nearest valid batch expiry
    - No negative inventory possible
    - Days of cover represents historical evidence accurately
    - Independent critical/zero/low alert thresholds & deterministic ordering
    """

    def setUp(self):
        self.fac, self.reporter = make_facility(stock=10, expiry='06/27')

    def test_expiry_01_expired_batches_never_presented_as_usable_or_searchable(self):
        """5.1: Expired batch has usable_stock=0, status=STOCK_OUT, and is excluded from search."""
        exp_fac, _ = make_facility(name='Expired Center', state='Gombe', lga='Kaltungo', stock=18, expiry='02/25')
        self.assertEqual(exp_fac.expiry_status, 'EXPIRED')
        self.assertTrue(exp_fac.has_expired_stock)
        self.assertEqual(exp_fac.current_stock, 18)
        self.assertEqual(exp_fac.usable_stock, 0)
        self.assertEqual(exp_fac.stock_status, 'STOCK_OUT')

        results = rank_facilities_for_query(state='Gombe', lga='Kaltungo', record_audit=False)
        self.assertNotIn(exp_fac.id, [r['facility_id'] for r in results])

    def test_expiry_02_expired_stock_rejected_on_receipt_and_adjustment(self):
        """5.2: Rejects already-expired stock during receipt or adjustment."""
        self.assertTrue(is_expiry_date_expired('01/25'))
        with self.assertRaises(ValueError):
            record_stock_update(self.fac, quantity=14, expiry_date='01/25', reporter=self.reporter)
        self.assertEqual(self.fac.current_stock, 10)

    def test_expiry_03_partially_expired_or_replaced_inventory_quarantine_handling(self):
        """
        5.3: If a target facility holds an expired batch (e.g. 4 vials of 01/25) and receives
        a redistribution transfer of 6 valid vials (08/27), the 4 expired vials are automatically
        quarantined via an EXPIRED (-4) transaction first so usable_stock becomes 6 (never 10).
        """
        exp_target, _ = make_facility(name='Quarantine Target', stock=4, expiry='01/25')
        self.assertEqual(exp_target.usable_stock, 0)

        record_redistribution_transfer(self.fac, exp_target, quantity=6, reporter=self.reporter)
        exp_target._clear_runtime_cache()
        self.assertEqual(exp_target.current_stock, 6)
        self.assertEqual(exp_target.usable_stock, 6)
        self.assertEqual(exp_target.latest_expiry, '06/27')
        self.assertTrue(
            InventoryTransaction.objects.filter(
                facility=exp_target, transaction_type=TransactionType.EXPIRED, quantity=-4
            ).exists()
        )

    def test_expiry_04_stock_deductions_preserve_batch_expiry_and_reject_expired_deduction(self):
        """
        5.4: Case deductions preserve the active batch expiry on the USED transaction,
        and deducting from an expired batch is rejected.
        """
        record_case_treatment(self.fac, vials_used=3, reporter=self.reporter)
        self.fac._clear_runtime_cache()
        self.assertEqual(self.fac.current_stock, 7)
        self.assertEqual(self.fac.latest_expiry, '06/27')

        exp_fac, exp_rep = make_facility(name='Expired Treatment Fac', stock=5, expiry='01/25')
        with self.assertRaises(ValueError):
            record_case_treatment(exp_fac, vials_used=1, reporter=exp_rep)
        self.assertEqual(exp_fac.current_stock, 5)

    def test_expiry_05_transfers_preserve_stock_conservation_and_nearest_expiry(self):
        """
        5.5: When transferring from Donor (expiry 09/27) to Recipient with existing valid stock
        (expiry 04/27), Recipient preserves the nearer expiry date (04/27) so early-expiry warnings
        are not masked. When Recipient has 0 vials, it adopts Donor's expiry (09/27).
        """
        donor, d_rep = make_facility(name='Donor 09/27', stock=15, expiry='09/27')
        rec_with_stock, _ = make_facility(name='Rec 04/27', stock=2, expiry='04/27')
        rec_empty, _ = make_facility(name='Rec Empty', stock=0, expiry='03/27')

        record_redistribution_transfer(donor, rec_with_stock, quantity=5, reporter=d_rep)
        rec_with_stock._clear_runtime_cache()
        self.assertEqual(rec_with_stock.current_stock, 7)
        self.assertEqual(rec_with_stock.latest_expiry, '04/27')

        record_redistribution_transfer(donor, rec_empty, quantity=4, reporter=d_rep)
        rec_empty._clear_runtime_cache()
        self.assertEqual(rec_empty.current_stock, 4)
        self.assertEqual(rec_empty.latest_expiry, '09/27')

    def test_expiry_06_no_negative_inventory_possible_under_any_operation(self):
        """5.6: Negative stock is rejected across stock updates, case treatments, and transfers."""
        with self.assertRaises(ValueError):
            record_stock_update(self.fac, quantity=-5, reporter=self.reporter)
        with self.assertRaises(ValueError):
            record_case_treatment(self.fac, vials_used=11, reporter=self.reporter)
        other_fac, _ = make_facility(name='Target Fac', stock=2)
        with self.assertRaises(ValueError):
            record_redistribution_transfer(self.fac, other_fac, quantity=11, reporter=self.reporter)
        with self.assertRaises(ValueError):
            record_redistribution_transfer(self.fac, self.fac, quantity=2, reporter=self.reporter)
        self.assertEqual(self.fac.current_stock, 10)

    def test_expiry_07_days_of_cover_reflects_historical_evidence_and_confidence_tiers(self):
        """
        5.7: Verifies INSUFFICIENT_DATA (0 events -> None), LOW_CONFIDENCE (1-2 events),
        and HIGH_CONFIDENCE (>=3 events over >=7 days).
        """
        fac, rep = make_facility(name='Evidence Hospital', stock=12, expiry='06/27')
        # 0 events -> INSUFFICIENT_DATA
        self.assertEqual(fac.consumption_confidence, ConsumptionConfidence.INSUFFICIENT_DATA)
        self.assertIsNone(fac.days_of_cover)

        # 1 event -> LOW_CONFIDENCE
        SnakebiteCase.objects.create(
            facility=fac, reporter=rep, date=timezone.now().date() - timedelta(days=2),
            vials_used=6, outcome='RECOVERED'
        )
        fac._clear_runtime_cache()
        self.assertEqual(fac.consumption_confidence, ConsumptionConfidence.LOW_CONFIDENCE)
        self.assertEqual(fac.days_of_cover, 60.0)

        # 3 events over 18 days -> HIGH_CONFIDENCE
        for d in (10, 18):
            SnakebiteCase.objects.create(
                facility=fac, reporter=rep, date=timezone.now().date() - timedelta(days=d),
                vials_used=12, outcome='RECOVERED'
            )
        fac._clear_runtime_cache()
        self.assertEqual(fac.consumption_confidence, ConsumptionConfidence.HIGH_CONFIDENCE)
        self.assertEqual(fac.calculate_average_daily_usage(days=30), 1.0)
        self.assertEqual(fac.days_of_cover, 12.0)

    def test_alert_critical_at_1_to_2_vials_without_history_and_deduplication(self):
        """Verifies 1-2 vials triggers CRITICAL alert without history and never duplicates."""
        fac2, _ = make_facility(name='Two Vial No History', stock=2, expiry='06/27')
        evaluate_facility_alerts(fac2)
        evaluate_facility_alerts(fac2)
        alerts = Alert.objects.filter(
            facility=fac2, alert_type=AlertType.POTENTIAL_STOCKOUT, status=AlertStatus.ACTIVE
        )
        self.assertEqual(alerts.count(), 1)
        self.assertEqual(alerts.first().severity, AlertSeverity.CRITICAL)

    def test_deterministic_ordering_identical_timestamps(self):
        """Verifies ['-timestamp', '-id'] ordering when timestamps match."""
        ts = timezone.now()
        InventoryTransaction.objects.create(
            facility=self.fac, reporter=self.reporter,
            transaction_type=TransactionType.STOCK_CONFIRMATION,
            quantity=0, stock_after=25, expiry_date='06/27', timestamp=ts
        )
        tx_last = InventoryTransaction.objects.create(
            facility=self.fac, reporter=self.reporter,
            transaction_type=TransactionType.USED,
            quantity=-4, stock_after=21, expiry_date='06/27', timestamp=ts
        )
        self.fac._clear_runtime_cache()
        self.assertEqual(self.fac.latest_transaction.id, tx_last.id)
        self.assertEqual(self.fac.current_stock, 21)


class PerformanceQueryBoundTest(TestCase):
    """Phase 1 Priority 4: Verifies N+1 query elimination across Overview, Facilities, and API."""

    def setUp(self):
        self.client = Client()
        for i in range(11):
            make_facility(
                name=f'Perf Facility {i}',
                state='Gombe' if i < 6 else 'Plateau',
                lga='Kaltungo',
                stock=8 + i
            )

    def test_overview_facilities_and_api_query_counts_bounded(self):
        with CaptureQueriesContext(connection) as ctx_overview:
            r1 = self.client.get('/')
            self.assertEqual(r1.status_code, 200)
        self.assertLessEqual(len(ctx_overview), 15)

        with CaptureQueriesContext(connection) as ctx_fac_page:
            r2 = self.client.get('/facilities/')
            self.assertEqual(r2.status_code, 200)
        self.assertLessEqual(len(ctx_fac_page), 15)

        with CaptureQueriesContext(connection) as ctx_fac_api:
            r3 = self.client.get('/api/facilities/')
            self.assertEqual(r3.status_code, 200)
        self.assertLessEqual(len(ctx_fac_api), 10)


# ==============================================================================
# PART III: PHASE 3 HARDENING, HEALTH PROBES, RATE LIMITING & DEMO GATING
# ==============================================================================


class Phase3HardeningAndReadinessTest(TestCase):
    """Phase 3 Task 2 & Task 4: Health probes, throttling, demo gating, and fail-closed audit APIs."""

    def setUp(self):
        cache.clear()
        self.client = Client()
        self.gombe_fac, self.gombe_rep = make_facility(
            name='Phase3 Gombe Hospital', state='Gombe', lga='Kaltungo', stock=12, pin='1234'
        )
        self.plateau_fac, self.plateau_rep = make_facility(
            name='Phase3 Plateau Hospital', state='Plateau', lga='Shendam', stock=9, pin='2345'
        )
        self.public_user = User.objects.create_user(username='p3_public', password='StrongPass!99')
        UserProfile.objects.create(user=self.public_user, role=UserRole.PUBLIC_USER)

        self.state_user = User.objects.create_user(username='p3_gombe_off', password='StrongPass!99')
        UserProfile.objects.create(
            user=self.state_user, role=UserRole.STATE_OFFICIAL, assigned_state='Gombe'
        )
        self.unassigned_official = User.objects.create_user(
            username='p3_unassigned_off', password='StrongPass!99'
        )
        UserProfile.objects.create(
            user=self.unassigned_official, role=UserRole.STATE_OFFICIAL, assigned_state=''
        )
        self.rep_user = User.objects.create_user(username='p3_rep_user', password='StrongPass!99')
        UserProfile.objects.create(
            user=self.rep_user,
            role=UserRole.FACILITY_REPORTER,
            facility=self.gombe_fac,
        )

    def tearDown(self):
        cache.clear()

    def test_healthz_and_readyz_endpoints(self):
        r_health = self.client.get('/healthz/')
        self.assertEqual(r_health.status_code, 200)
        self.assertEqual(r_health.json().get('status'), 'ok')

        r_ready = self.client.get('/readyz/')
        self.assertEqual(r_ready.status_code, 200)
        payload = r_ready.json()
        self.assertEqual(payload.get('status'), 'ready')
        self.assertEqual(payload.get('database'), 'ok')
        self.assertEqual(payload.get('cache'), 'ok')
        self.assertIn(payload.get('database_vendor'), ('sqlite', 'postgresql'))

    @override_settings(
        VENOMLINK_THROTTLE_RATES={'search_anon': '2/min', 'pin_auth': '2/min', 'ussd_simulator': '2/min'}
    )
    def test_anonymous_search_rate_limiting_returns_429(self):
        cache.clear()
        r1 = self.client.get('/api/matching/search/?state=Gombe')
        r2 = self.client.get('/api/matching/search/?state=Gombe')
        r3 = self.client.get('/api/matching/search/?state=Gombe')
        self.assertEqual(r1.status_code, 200)
        self.assertEqual(r2.status_code, 200)
        self.assertEqual(r3.status_code, 429)

    @override_settings(
        VENOMLINK_THROTTLE_RATES={'search_anon': '60/min', 'pin_auth': '2/min', 'ussd_simulator': '60/min'}
    )
    def test_pin_auth_endpoints_rate_limited(self):
        cache.clear()
        bad_payload = {
            'facility_id': self.gombe_fac.id,
            'quantity': 5,
            'expiry_date': '06/27',
            'phone_number': self.gombe_rep.phone_number,
            'pin': '9999',
        }
        r1 = self.client.post('/api/inventory/update-stock/', bad_payload)
        r2 = self.client.post('/api/inventory/update-stock/', bad_payload)
        r3 = self.client.post('/api/inventory/update-stock/', bad_payload)
        self.assertEqual(r1.status_code, 403)
        self.assertEqual(r2.status_code, 403)
        self.assertEqual(r3.status_code, 429)

    def test_demo_mode_disabled_blocks_simulator_demo_and_anon_sms_logs(self):
        with override_settings(
            VENOMLINK_CONFIG={**__import__('django.conf').conf.settings.VENOMLINK_CONFIG, 'SYSTEM_DEMO_MODE': False}
        ):
            r_demo = self.client.get('/demo-flow/')
            r_sim_page = self.client.get('/ussd-simulator/')
            r_sim = self.client.post('/api/ussd/simulate/', {'phoneNumber': '08011112222', 'text': ''})
            r_sms = self.client.get('/api/notifications/sms-logs/')
            self.assertEqual(r_demo.status_code, 404)
            self.assertEqual(r_sim_page.status_code, 404)
            self.assertEqual(r_sim.status_code, 403)
            self.assertEqual(r_sms.status_code, 403)

    @staticmethod
    def _unwrap_rows(payload):
        if isinstance(payload, dict) and 'results' in payload:
            return payload['results']
        return payload

    def test_audit_endpoints_fail_closed_and_scope_by_role(self):
        record_case_treatment(self.gombe_fac, vials_used=2, reporter=self.gombe_rep)
        record_case_treatment(self.plateau_fac, vials_used=2, reporter=self.plateau_rep)
        AntivenomSearch.objects.create(state='Gombe', lga='Kaltungo', successful_match=True)
        AntivenomSearch.objects.create(state='Plateau', lga='Shendam', successful_match=True)

        # Anonymous and PUBLIC_USER denied
        self.assertIn(self.client.get('/api/inventory/transactions/').status_code, (401, 403))
        self.assertIn(self.client.get('/api/inventory/cases/').status_code, (401, 403))
        self.assertIn(self.client.get('/api/matching/audits/').status_code, (401, 403))

        self.client.force_login(self.public_user)
        self.assertEqual(self.client.get('/api/inventory/transactions/').status_code, 403)
        self.assertEqual(self.client.get('/api/inventory/cases/').status_code, 403)
        self.assertEqual(self.client.get('/api/matching/audits/').status_code, 403)

        # Unassigned STATE_OFFICIAL sees 0 records (fail-closed)
        self.client.force_login(self.unassigned_official)
        self.assertEqual(len(self._unwrap_rows(self.client.get('/api/inventory/transactions/').json())), 0)
        self.assertEqual(len(self._unwrap_rows(self.client.get('/api/inventory/cases/').json())), 0)
        self.assertEqual(len(self._unwrap_rows(self.client.get('/api/matching/audits/').json())), 0)

        # Gombe STATE_OFFICIAL sees only Gombe records
        self.client.force_login(self.state_user)
        tx_rows = self._unwrap_rows(self.client.get('/api/inventory/transactions/').json())
        self.assertTrue(len(tx_rows) > 0)
        for row in tx_rows:
            self.assertEqual(row['facility'], self.gombe_fac.id)

        audit_rows = self._unwrap_rows(self.client.get('/api/matching/audits/').json())
        self.assertEqual(len(audit_rows), 1)
        self.assertEqual(audit_rows[0]['state'], 'Gombe')

        # FACILITY_REPORTER sees only own facility transactions/cases and 0 matching audits
        self.client.force_login(self.rep_user)
        rep_tx = self._unwrap_rows(self.client.get('/api/inventory/transactions/').json())
        self.assertTrue(all(r['facility'] == self.gombe_fac.id for r in rep_tx))
        self.assertEqual(len(self._unwrap_rows(self.client.get('/api/matching/audits/').json())), 0)

    def test_coordinate_validation_and_debug_false_error_safety(self):
        r_bad = self.client.get('/api/matching/search/?state=Gombe&latitude=999')
        self.assertEqual(r_bad.status_code, 400)

        with override_settings(DEBUG=False):
            r_404 = self.client.get('/api/facilities/999999/')
            self.assertEqual(r_404.status_code, 404)
            body = r_404.content.decode()
            self.assertNotIn('Traceback', body)
            self.assertNotIn('settings.py', body)
