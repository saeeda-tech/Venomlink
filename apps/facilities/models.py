"""
Facilities, Reporters, and Role-Based Access Control (RBAC) models for VenomLink.
Represents verified public health facilities, primary health centres,
general hospitals, authorized staff reporters, and system user profiles.
"""

from calendar import monthrange
from datetime import date, timedelta
from decimal import Decimal
import re
from typing import Optional

from django.conf import settings
from django.contrib.auth.hashers import check_password, is_password_usable, make_password
from django.db import models
from django.utils import timezone

EXPIRY_PARSE_REGEX = re.compile(r'^(0[1-9]|1[0-2])\/([0-9]{2})$')


class FacilityType(models.TextChoices):
    GENERAL_HOSPITAL = 'GENERAL_HOSPITAL', 'General Hospital'
    SPECIALIST_HOSPITAL = 'SPECIALIST_HOSPITAL', 'Specialist Hospital'
    COMPREHENSIVE_HEALTH_CENTRE = 'COMPREHENSIVE_HEALTH_CENTRE', 'Comprehensive Health Centre'
    PRIMARY_HEALTH_CENTRE = 'PRIMARY_HEALTH_CENTRE', 'Primary Health Centre'
    PRIVATE_CLINIC = 'PRIVATE_CLINIC', 'Private Clinic / Mission Hospital'


class UserRole(models.TextChoices):
    SYSTEM_ADMIN = 'SYSTEM_ADMIN', 'System Administrator'
    STATE_OFFICIAL = 'STATE_OFFICIAL', 'State Health Official'
    FACILITY_ADMIN = 'FACILITY_ADMIN', 'Facility Administrator'
    FACILITY_REPORTER = 'FACILITY_REPORTER', 'Facility Reporter'
    PUBLIC_USER = 'PUBLIC_USER', 'Public User'


class ConsumptionConfidence(models.TextChoices):
    HIGH_CONFIDENCE = 'HIGH_CONFIDENCE', 'High confidence'
    LOW_CONFIDENCE = 'LOW_CONFIDENCE', 'Low confidence'
    INSUFFICIENT_DATA = 'INSUFFICIENT_DATA', 'Insufficient usage history'


class Facility(models.Model):
    name = models.CharField(max_length=255, db_index=True)
    facility_type = models.CharField(
        max_length=40,
        choices=FacilityType.choices,
        default=FacilityType.GENERAL_HOSPITAL
    )
    state = models.CharField(max_length=100, db_index=True)
    lga = models.CharField(max_length=100, db_index=True, verbose_name="LGA")
    latitude = models.DecimalField(max_digits=9, decimal_places=6)
    longitude = models.DecimalField(max_digits=9, decimal_places=6)
    phone = models.CharField(max_length=32, help_text="Public emergency contact number")
    verified = models.BooleanField(default=True, db_index=True)
    resupply_lead_time_days = models.PositiveIntegerField(
        default=3,
        help_text="Expected replenishment turnaround in days"
    )
    safety_buffer_days = models.PositiveIntegerField(
        default=2,
        help_text="Safety cushion in days before stockout becomes critical"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name_plural = 'Facilities'
        ordering = ['state', 'name']
        indexes = [
            models.Index(fields=['state', 'lga']),
            models.Index(fields=['verified', 'state']),
        ]

    def __str__(self):
        return f"{self.name} ({self.lga}, {self.state})"

    def _clear_runtime_cache(self):
        """Invalidates in-memory cached transaction/case evaluations after writes."""
        cached_keys = [k for k in list(self.__dict__.keys()) if k.startswith('_cached_')]
        for attr in cached_keys:
            delattr(self, attr)
        if hasattr(self, '_prefetched_objects_cache'):
            self._prefetched_objects_cache.pop('inventory_transactions', None)
            self._prefetched_objects_cache.pop('cases', None)

    @property
    def latest_transaction(self):
        """
        Fetch the most recent inventory transaction record deterministically
        (ordered by -timestamp, -id). Uses prefetch_related cache when present
        to prevent N+1 queries.
        """
        if hasattr(self, '_prefetched_objects_cache') and 'inventory_transactions' in self._prefetched_objects_cache:
            prefetched = self._prefetched_objects_cache['inventory_transactions']
            return prefetched[0] if prefetched else None

        if hasattr(self, '_cached_latest_tx'):
            return self._cached_latest_tx

        tx = self.inventory_transactions.order_by('-timestamp', '-id').first()
        self._cached_latest_tx = tx
        return tx

    @property
    def current_stock(self) -> int:
        """
        Derives current physical stock from latest confirmed transaction stock_after.
        Falls back to 0 if no transactions exist.
        """
        latest = self.latest_transaction
        return latest.stock_after if latest else 0

    @property
    def latest_expiry(self) -> str:
        """Nearest batch expiry string (e.g. '03/27' or 'Not recorded')."""
        latest = self.latest_transaction
        if latest and latest.expiry_date:
            return latest.expiry_date
        return "Not recorded"

    @property
    def expiry_date_parsed(self) -> Optional[date]:
        """Parses MM/YY latest_expiry into the last calendar day of that month."""
        exp_str = self.latest_expiry
        if not exp_str or exp_str == "Not recorded":
            return None
        match = EXPIRY_PARSE_REGEX.match(exp_str.strip())
        if not match:
            return None
        month, year = int(match.group(1)), 2000 + int(match.group(2))
        _, last_day = monthrange(year, month)
        return date(year, month, last_day)

    @property
    def days_to_expiry(self) -> Optional[int]:
        parsed = self.expiry_date_parsed
        if not parsed:
            return None
        return (parsed - timezone.now().date()).days

    @property
    def expiry_status(self) -> str:
        """
        Distinguishes batch expiry state:
        - EXPIRED: past end of expiry month (days_to_expiry < 0)
        - EXPIRING_SOON: within configured warning window (default 60 days)
        - VALID: > 60 days remaining
        - UNKNOWN: no expiry recorded
        """
        days_left = self.days_to_expiry
        if days_left is None:
            return 'UNKNOWN'
        warning_days = getattr(settings, 'VENOMLINK_CONFIG', {}).get('EXPIRY_WARNING_DAYS', 60)
        if days_left < 0:
            return 'EXPIRED'
        elif days_left <= warning_days:
            return 'EXPIRING_SOON'
        return 'VALID'

    @property
    def expiry_status_label(self) -> str:
        status = self.expiry_status
        days_left = self.days_to_expiry
        if status == 'EXPIRED':
            return f"Expired ({self.latest_expiry})"
        elif status == 'EXPIRING_SOON':
            return f"Expiring soon ({self.latest_expiry}, {days_left}d left)"
        elif status == 'VALID':
            return f"Valid ({self.latest_expiry})"
        return "Not recorded"

    @property
    def has_expired_stock(self) -> bool:
        """True if the facility holds physical vials whose batch expiry has passed."""
        return self.current_stock > 0 and self.expiry_status == 'EXPIRED'

    @property
    def usable_stock(self) -> int:
        """
        Expired stock must never be presented or counted as usable antivenom.
        """
        if self.has_expired_stock:
            return 0
        return self.current_stock

    @property
    def last_confirmed_at(self):
        """Timestamp of the most recent stock confirmation or transaction."""
        latest = self.latest_transaction
        return latest.timestamp if latest else None

    @property
    def freshness_status(self) -> str:
        """
        Categorise confirmation freshness:
        - TODAY: within last 24 hours
        - RECENT: between 24 and 48 hours
        - STALE: older than STALE_DATA_THRESHOLD_HOURS (default 48 hours)
        - UNCONFIRMED: no records
        """
        last = self.last_confirmed_at
        if not last:
            return 'UNCONFIRMED'
        stale_hours = getattr(settings, 'VENOMLINK_CONFIG', {}).get('STALE_DATA_THRESHOLD_HOURS', 48)
        age = timezone.now() - last
        if age <= timedelta(hours=24):
            return 'TODAY'
        elif age <= timedelta(hours=stale_hours):
            return 'RECENT'
        else:
            return 'STALE'

    @property
    def freshness_label(self) -> str:
        """Human-readable string for trust and audit transparency."""
        status = self.freshness_status
        if status == 'UNCONFIRMED':
            return "Stock information needs confirmation"
        last = self.last_confirmed_at
        age = timezone.now() - last
        hours = int(age.total_seconds() // 3600)
        days = age.days

        if hours < 1:
            return "Confirmed within the hour"
        elif hours < 24:
            return f"Confirmed today ({hours}h ago)"
        elif days == 1:
            return "Confirmed yesterday"
        elif status == 'RECENT':
            return f"Confirmed {days} days ago"
        else:
            return f"Confirmation overdue ({days} days ago)"

    @property
    def is_stale(self) -> bool:
        return self.freshness_status in ('STALE', 'UNCONFIRMED')

    def get_consumption_metrics(self, days: int = 30) -> dict:
        """
        Computes actual historical consumption over a defined observation window (days).
        Does NOT fabricate arbitrary clinical consumption rates when history is absent.
        Returns explicit data-quality and confidence indicators:
        - HIGH_CONFIDENCE: >= min_events (default 3) across >= min_obs_days (default 7)
        - LOW_CONFIDENCE: 1..2 events or < 7 days of observation span (sparse history)
        - INSUFFICIENT_DATA: 0 consumption events in window
        """
        cache_key = f'_cached_consumption_metrics_{days}'
        if hasattr(self, cache_key):
            return getattr(self, cache_key)

        now = timezone.now()
        since_dt = now - timedelta(days=days)
        since_date = since_dt.date()

        cfg = getattr(settings, 'VENOMLINK_CONFIG', {})
        min_events = cfg.get('MIN_CONSUMPTION_EVENTS_HIGH_CONFIDENCE', 3)
        min_obs_days = cfg.get('MIN_OBSERVATION_DAYS_HIGH_CONFIDENCE', 7)

        # Use prefetched caches if available to avoid N+1 SQL queries
        if hasattr(self, '_prefetched_objects_cache') and 'inventory_transactions' in self._prefetched_objects_cache:
            all_txs = self._prefetched_objects_cache['inventory_transactions']
            used_txs = [tx for tx in all_txs if tx.transaction_type == 'USED' and tx.timestamp >= since_dt]
            earliest_tx_dt = min((tx.timestamp for tx in all_txs), default=None)
        else:
            used_txs = list(self.inventory_transactions.filter(
                transaction_type='USED',
                timestamp__gte=since_dt
            ))
            earliest_tx = self.inventory_transactions.order_by('timestamp', 'id').first()
            earliest_tx_dt = earliest_tx.timestamp if earliest_tx else None

        if hasattr(self, '_prefetched_objects_cache') and 'cases' in self._prefetched_objects_cache:
            all_cases = self._prefetched_objects_cache['cases']
            recent_cases = [c for c in all_cases if c.date >= since_date]
            earliest_case_date = min((c.date for c in all_cases), default=None)
        else:
            recent_cases = list(self.cases.filter(date__gte=since_date))
            earliest_case = self.cases.order_by('date', 'id').first()
            earliest_case_date = earliest_case.date if earliest_case else None

        used_tx_qty = sum(abs(tx.quantity) for tx in used_txs)
        case_vials = sum(c.vials_used for c in recent_cases)

        total_consumption = max(used_tx_qty, case_vials)
        event_count = max(len(used_txs), len(recent_cases))

        # Determine observation history span in days
        obs_days = 0
        if earliest_tx_dt:
            obs_days = max(obs_days, (now.date() - earliest_tx_dt.date()).days)
        if earliest_case_date:
            obs_days = max(obs_days, (now.date() - earliest_case_date).days)
        obs_days = min(days, max(obs_days, 1 if event_count > 0 else 0))

        usable = self.usable_stock

        if total_consumption <= 0 or event_count == 0:
            metrics = {
                'window_days': days,
                'total_vials_consumed': 0,
                'event_count': 0,
                'observation_days': obs_days,
                'average_daily_usage': 0.0,
                'days_of_cover': 0.0 if usable == 0 else None,
                'confidence': 'INSUFFICIENT_DATA',
                'confidence_label': 'Insufficient usage history',
                'days_of_cover_display': '0.0 days (Stock-out)' if usable == 0 else 'Unavailable (Insufficient history)',
            }
        else:
            avg_daily = round(total_consumption / days, 2)
            doc = round(usable / avg_daily, 1) if avg_daily > 0 else (0.0 if usable == 0 else None)

            if event_count >= min_events and obs_days >= min_obs_days:
                confidence = 'HIGH_CONFIDENCE'
                confidence_label = 'High confidence'
                doc_display = f"{doc} days" if doc is not None else "Unavailable"
            else:
                confidence = 'LOW_CONFIDENCE'
                confidence_label = f"Low confidence ({event_count} event{'s' if event_count != 1 else ''} in {days}d)"
                doc_display = f"{doc}d (Low confidence)" if doc is not None else "Unavailable (Low confidence)"

            metrics = {
                'window_days': days,
                'total_vials_consumed': total_consumption,
                'event_count': event_count,
                'observation_days': obs_days,
                'average_daily_usage': avg_daily,
                'days_of_cover': doc,
                'confidence': confidence,
                'confidence_label': confidence_label,
                'days_of_cover_display': doc_display,
            }

        setattr(self, cache_key, metrics)
        return metrics

    def calculate_average_daily_usage(self, days: int = 30) -> float:
        """
        Returns actual historical average daily consumption over `days`.
        Returns 0.0 when no consumption has been recorded in the window.
        """
        return self.get_consumption_metrics(days=days)['average_daily_usage']

    @property
    def consumption_confidence(self) -> str:
        return self.get_consumption_metrics(days=30)['confidence']

    @property
    def consumption_confidence_label(self) -> str:
        return self.get_consumption_metrics(days=30)['confidence_label']

    @property
    def days_of_cover(self) -> Optional[float]:
        """
        Formula: Usable Stock ÷ Average Daily Usage.
        Returns None if there is no historical consumption data (unless usable stock is 0, which is 0.0).
        """
        return self.get_consumption_metrics(days=30)['days_of_cover']

    @property
    def days_of_cover_display(self) -> str:
        return self.get_consumption_metrics(days=30)['days_of_cover_display']

    @property
    def stock_status(self) -> str:
        """
        Semantic stock condition using independent configurable thresholds:
        - 0 usable vials (or expired batch): STOCK_OUT
        - 1–2 usable vials: CRITICAL (even when consumption history is insufficient)
        - 3–4 usable vials: LOW_STOCK (or CRITICAL if high-confidence days_of_cover <= reorder_threshold)
        - Above 4 usable vials:
          - If data is stale (>48h): UNCONFIRMED
          - Else evaluated via high-confidence days_of_cover against replenishment lead time + buffer
        """
        cfg = getattr(settings, 'VENOMLINK_CONFIG', {})
        stock_out_threshold = cfg.get('STOCK_OUT_THRESHOLD', 0)
        crit_max_vials = cfg.get('CRITICAL_STOCK_MAX_VIALS', 2)
        low_max_vials = cfg.get('LOW_STOCK_MAX_VIALS', 4)

        if self.has_expired_stock:
            return 'STOCK_OUT'

        usable = self.usable_stock
        if usable <= stock_out_threshold:
            return 'STOCK_OUT'

        if usable <= crit_max_vials:
            return 'CRITICAL'

        reorder_threshold = self.resupply_lead_time_days + self.safety_buffer_days
        doc = self.days_of_cover
        confidence = self.consumption_confidence

        if usable <= low_max_vials:
            if confidence == 'HIGH_CONFIDENCE' and doc is not None and doc <= reorder_threshold:
                return 'CRITICAL'
            return 'LOW_STOCK'

        # Above 4 vials: check freshness first, then high-confidence days of cover
        if self.is_stale:
            return 'UNCONFIRMED'

        if confidence == 'HIGH_CONFIDENCE' and doc is not None:
            if doc <= reorder_threshold:
                return 'CRITICAL'
            elif doc <= (reorder_threshold + 3):
                return 'LOW_STOCK'

        return 'AVAILABLE'

    @property
    def stock_status_label(self) -> str:
        if self.has_expired_stock:
            return "Expired Batch"
        status = self.stock_status
        if status == 'AVAILABLE':
            return "Available"
        elif status in ('LOW', 'LOW_STOCK'):
            return "Low Stock"
        elif status == 'STOCK_OUT':
            return "Stock-out"
        elif status == 'CRITICAL':
            return "Critical Stock"
        elif status == 'UNCONFIRMED':
            return "Unconfirmed"
        return "Unknown"

    @property
    def stock_status_color(self) -> str:
        """Return strictly semantic colours conforming to the specification."""
        status = self.stock_status
        if status == 'AVAILABLE':
            return "#1E7E34"  # Semantic Green
        elif status in ('LOW', 'LOW_STOCK'):
            return "#D97706"  # Semantic Amber
        elif status in ('CRITICAL', 'STOCK_OUT'):
            return "#B71C1C"  # Semantic Red
        return "#546E7A"      # Neutral Grey for unconfirmed / stale


class ReporterRole(models.TextChoices):
    PHARMACIST = 'PHARMACIST', 'Pharmacist'
    NURSE = 'NURSE', 'Nurse / Matron'
    MEDICAL_OFFICER = 'MEDICAL_OFFICER', 'Medical Officer'
    STORE_OFFICER = 'STORE_OFFICER', 'Store Officer'
    COMMUNITY_HEALTH = 'COMMUNITY_HEALTH', 'Community Health Worker (CHW)'


class Reporter(models.Model):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='reporter_record'
    )
    phone_number = models.CharField(
        max_length=32,
        unique=True,
        db_index=True,
        help_text="Normalised phone number e.g. +2348012345678"
    )
    facility = models.ForeignKey(
        Facility,
        on_delete=models.CASCADE,
        related_name='reporters'
    )
    full_name = models.CharField(max_length=150, blank=True)
    role = models.CharField(
        max_length=40,
        choices=ReporterRole.choices,
        default=ReporterRole.PHARMACIST
    )
    pin_hash = models.CharField(
        max_length=128,
        blank=True,
        help_text="PBKDF2 hashed staff authentication PIN (never plaintext)"
    )
    failed_pin_attempts = models.PositiveIntegerField(default=0)
    locked_until = models.DateTimeField(null=True, blank=True)
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['facility', 'full_name']

    def __str__(self):
        name = self.full_name or self.phone_number
        return f"{name} ({self.get_role_display()} at {self.facility.name})"

    def set_pin(self, raw_pin: str):
        """Hashes and sets the reporter's authentication PIN."""
        cleaned = str(raw_pin).strip()
        if not cleaned or len(cleaned) < 4:
            raise ValueError("Staff PIN must be at least 4 characters.")
        self.pin_hash = make_password(cleaned)
        self.failed_pin_attempts = 0
        self.locked_until = None

    def has_usable_pin(self) -> bool:
        return bool(self.pin_hash and is_password_usable(self.pin_hash))

    @property
    def is_locked_out(self) -> bool:
        return bool(self.locked_until and timezone.now() < self.locked_until)

    def verify_pin(self, raw_pin: str) -> bool:
        """
        Verifies a staff PIN against the stored hash.
        Enforces attempt limits and temporary account lockout.
        """
        if not self.active or self.is_locked_out:
            return False
        if not self.has_usable_pin():
            return False

        cleaned = str(raw_pin or '').strip()
        if check_password(cleaned, self.pin_hash):
            if self.failed_pin_attempts > 0 or self.locked_until is not None:
                self.failed_pin_attempts = 0
                self.locked_until = None
                self.save(update_fields=['failed_pin_attempts', 'locked_until'])
            return True

        # Failed attempt
        cfg = getattr(settings, 'VENOMLINK_CONFIG', {})
        max_attempts = cfg.get('USSD_MAX_PIN_ATTEMPTS', 3)
        lockout_minutes = cfg.get('USSD_LOCKOUT_MINUTES', 15)

        self.failed_pin_attempts += 1
        if self.failed_pin_attempts >= max_attempts:
            self.locked_until = timezone.now() + timedelta(minutes=lockout_minutes)
        self.save(update_fields=['failed_pin_attempts', 'locked_until'])
        return False


class UserProfile(models.Model):
    """
    Role-Based Access Control (RBAC) profile for web and API users.
    Roles:
    1. SYSTEM_ADMIN — National / system-wide authority
    2. STATE_OFFICIAL — Scoped to assigned_state
    3. FACILITY_ADMIN — Scoped to assigned facility (manage staff & stock)
    4. FACILITY_REPORTER — Scoped to assigned facility (update stock & log cases)
    5. PUBLIC_USER — Read-only search access
    """
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='venomlink_profile'
    )
    role = models.CharField(
        max_length=30,
        choices=UserRole.choices,
        default=UserRole.PUBLIC_USER,
        db_index=True
    )
    assigned_state = models.CharField(
        max_length=100,
        blank=True,
        db_index=True,
        help_text="Authorized state for STATE_OFFICIAL role"
    )
    facility = models.ForeignKey(
        Facility,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='user_profiles',
        help_text="Authorized facility for FACILITY_ADMIN and FACILITY_REPORTER roles"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'User RBAC Profile'
        verbose_name_plural = 'User RBAC Profiles'

    def __str__(self):
        return f"{self.user.username} — {self.get_role_display()}"
