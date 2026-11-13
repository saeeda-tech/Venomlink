"""
Rate limiting for VenomLink.

Rates are read from `settings.VENOMLINK_THROTTLE_RATES` at request time (not import time),
so they can be tuned per environment and overridden in tests.

Scopes
------
search_anon      Unauthenticated availability / facility searches (per client IP).
pin_auth         Unauthenticated requests that present a reporter phone + PIN (per client IP).
ussd_simulator   Browser USSD simulator (per client IP).

Authenticated session users are exempt from these throttles (they are covered by RBAC).
Per-reporter brute-force protection (3 attempts -> 15 minute lock) is enforced
separately in `Reporter.verify_pin()` and is independent of these IP limits.

NOTE: Throttle counters live in the Django cache. With the default LocMemCache the counters
are per-process; set CACHE_BACKEND=db (or a shared Redis/Memcached cache) in any
multi-worker deployment so limits are shared across workers.
"""

from django.conf import settings
from django.http import HttpResponse
from rest_framework.throttling import SimpleRateThrottle

DEFAULT_RATES = {
    'search_anon': '60/min',
    'pin_auth': '10/min',
    'ussd_simulator': '60/min',
}


class ConfiguredRateThrottle(SimpleRateThrottle):
    scope = None

    def get_rate(self):
        configured = getattr(settings, 'VENOMLINK_THROTTLE_RATES', {}) or {}
        return configured.get(self.scope, DEFAULT_RATES.get(self.scope))

    def get_cache_key(self, request, view):
        user = getattr(request, 'user', None)
        if user is not None and getattr(user, 'is_authenticated', False):
            return None  # authenticated users are governed by RBAC, not IP throttles
        return self.cache_format % {'scope': self.scope, 'ident': self.get_ident(request)}


class AnonSearchRateThrottle(ConfiguredRateThrottle):
    scope = 'search_anon'


class PinAuthRateThrottle(ConfiguredRateThrottle):
    scope = 'pin_auth'


class UssdSimulatorRateThrottle(ConfiguredRateThrottle):
    scope = 'ussd_simulator'


def rate_limited_response(request, throttle_cls):
    """
    Helper for plain Django views. Returns an HTTP 429 response when the caller has
    exceeded the limit for `throttle_cls`, otherwise None.
    """
    throttle = throttle_cls()
    if throttle.allow_request(request, None):
        return None
    response = HttpResponse(
        "Too many requests. Please wait and try again.",
        status=429,
        content_type='text/plain; charset=utf-8',
    )
    wait = throttle.wait()
    if wait:
        response['Retry-After'] = str(int(wait) + 1)
    return response
