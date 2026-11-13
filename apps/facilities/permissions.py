"""
Role-Based Access Control (RBAC) and Facility/State Data Isolation Permissions for VenomLink.

Roles:
1. SYSTEM_ADMIN
2. STATE_OFFICIAL
3. FACILITY_ADMIN
4. FACILITY_REPORTER
5. PUBLIC_USER
"""

from typing import Optional
from rest_framework import permissions
from rest_framework.exceptions import PermissionDenied, NotAuthenticated
from .models import Facility, Reporter, UserRole
from apps.notifications.sms_gateway import normalise_nigerian_phone


def get_user_role(user) -> str:
    """Resolves the effective RBAC role for a Django user."""
    if not user or not user.is_authenticated:
        return UserRole.PUBLIC_USER
    if user.is_superuser:
        return UserRole.SYSTEM_ADMIN
    profile = getattr(user, 'venomlink_profile', None)
    if profile:
        return profile.role
    reporter = getattr(user, 'reporter_record', None)
    if reporter and reporter.active:
        return UserRole.FACILITY_REPORTER
    if user.is_staff:
        return UserRole.STATE_OFFICIAL
    return UserRole.PUBLIC_USER


def get_user_assigned_state(user) -> str:
    """Returns the assigned state for a STATE_OFFICIAL user, or empty string."""
    if not user or not user.is_authenticated:
        return ''
    profile = getattr(user, 'venomlink_profile', None)
    if profile and profile.role == UserRole.STATE_OFFICIAL:
        return (profile.assigned_state or '').strip()
    return ''


def get_user_facility_id(user) -> Optional[int]:
    """Returns the assigned facility ID for FACILITY_ADMIN or FACILITY_REPORTER."""
    if not user or not user.is_authenticated:
        return None
    profile = getattr(user, 'venomlink_profile', None)
    if profile and profile.facility_id:
        return profile.facility_id
    reporter = getattr(user, 'reporter_record', None)
    if reporter and reporter.active:
        return reporter.facility_id
    return None


def verify_facility_write_access(user, facility: Facility) -> bool:
    """
    Verifies whether an authenticated user has permission to modify inventory
    for the given `facility`.
    """
    role = get_user_role(user)
    if role == UserRole.SYSTEM_ADMIN:
        return True
    if role == UserRole.STATE_OFFICIAL:
        assigned_state = get_user_assigned_state(user)
        if not assigned_state:
            return False
        return assigned_state.lower() == facility.state.lower()
    if role in (UserRole.FACILITY_ADMIN, UserRole.FACILITY_REPORTER):
        fac_id = get_user_facility_id(user)
        return fac_id is not None and fac_id == facility.id
    return False


def verify_reporter_pin_credentials(
    phone_number: str,
    pin: str,
    target_facility: Facility
) -> Reporter:
    """
    Authenticates a registered facility reporter using phone_number + PIN
    and enforces strict facility association isolation.
    """
    if not phone_number or not pin:
        raise NotAuthenticated("Authentication credentials (user session or reporter phone + PIN) were not provided.")

    norm_phone = normalise_nigerian_phone(phone_number)
    reporter = (
        Reporter.objects.filter(phone_number=norm_phone).first()
        or Reporter.objects.filter(phone_number=phone_number.strip()).first()
    )
    if not reporter or not reporter.active:
        raise PermissionDenied("Unregistered or inactive facility reporter.")

    if reporter.is_locked_out:
        raise PermissionDenied("Reporter account is temporarily locked due to multiple failed PIN attempts.")

    # Verify PIN before or alongside facility check so brute-force attempts are counted
    if not reporter.verify_pin(pin):
        raise PermissionDenied("Invalid staff PIN.")

    # Strict facility-level isolation: reporter can ONLY update their own registered facility
    if reporter.facility_id != target_facility.id:
        raise PermissionDenied(
            f"Facility access denied: reporter is registered to {reporter.facility.name}, "
            f"not {target_facility.name}."
        )

    return reporter


class IsSystemOrStateAdminOrReadOnly(permissions.BasePermission):
    """
    Allows read-only access to public/authenticated users,
    but restricts creation/modification of facilities and reporters to
    SYSTEM_ADMIN, STATE_OFFICIAL (within state), or FACILITY_ADMIN (own facility).
    """
    def has_permission(self, request, view):
        if request.method in permissions.SAFE_METHODS:
            return True
        if not request.user or not request.user.is_authenticated:
            return False
        role = get_user_role(request.user)
        return role in (UserRole.SYSTEM_ADMIN, UserRole.STATE_OFFICIAL, UserRole.FACILITY_ADMIN)

    def has_object_permission(self, request, view, obj):
        if request.method in permissions.SAFE_METHODS:
            role = get_user_role(request.user)
            if role == UserRole.STATE_OFFICIAL:
                assigned_state = get_user_assigned_state(request.user)
                obj_state = getattr(obj, 'state', None) or getattr(getattr(obj, 'facility', None), 'state', '')
                if assigned_state and obj_state and assigned_state.lower() != obj_state.lower():
                    return False
            return True

        role = get_user_role(request.user)
        if role == UserRole.SYSTEM_ADMIN:
            return True
        if role == UserRole.STATE_OFFICIAL:
            assigned_state = get_user_assigned_state(request.user)
            obj_state = getattr(obj, 'state', None) or getattr(getattr(obj, 'facility', None), 'state', '')
            return bool(assigned_state and obj_state and assigned_state.lower() == obj_state.lower())
        if role == UserRole.FACILITY_ADMIN:
            fac_id = get_user_facility_id(request.user)
            obj_fac_id = getattr(obj, 'facility_id', None) or getattr(obj, 'id', None)
            return bool(fac_id and fac_id == obj_fac_id)
        return False


class IsAuthorizedForTransferWrite(permissions.BasePermission):
    """
    Only SYSTEM_ADMIN and STATE_OFFICIAL may execute lateral stock transfers.
    """
    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False
        role = get_user_role(request.user)
        return role in (UserRole.SYSTEM_ADMIN, UserRole.STATE_OFFICIAL)


class IsAuthorizedForAlertManagement(permissions.BasePermission):
    """
    Only SYSTEM_ADMIN, STATE_OFFICIAL, and FACILITY_ADMIN may acknowledge or resolve alerts.
    """
    def has_permission(self, request, view):
        if request.method in permissions.SAFE_METHODS:
            return True
        if not request.user or not request.user.is_authenticated:
            return False
        role = get_user_role(request.user)
        return role in (UserRole.SYSTEM_ADMIN, UserRole.STATE_OFFICIAL, UserRole.FACILITY_ADMIN)


def is_demo_mode() -> bool:
    from django.conf import settings
    return bool(getattr(settings, 'VENOMLINK_CONFIG', {}).get('SYSTEM_DEMO_MODE', False))


class DemoModeOrAuthenticated(permissions.BasePermission):
    """
    Anonymous access is allowed ONLY when SYSTEM_DEMO_MODE is enabled.
    Otherwise the caller must be an authenticated user.
    """
    def has_permission(self, request, view):
        if request.user and request.user.is_authenticated:
            return True
        return is_demo_mode()


class DemoModeOnly(permissions.BasePermission):
    """Endpoint exists only when SYSTEM_DEMO_MODE is enabled (e.g. the USSD browser simulator)."""
    def has_permission(self, request, view):
        return is_demo_mode()


class IsAuthenticatedWithOperationalRole(permissions.BasePermission):
    """
    Authenticated users whose RBAC role is an operational role (not PUBLIC_USER).
    Used for audit data (transaction ledger, case registry) that identifies staff.
    """
    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False
        return get_user_role(request.user) != UserRole.PUBLIC_USER
