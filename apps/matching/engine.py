"""
Transparent and Deterministic Antivenom Matching Engine for VenomLink.

Implements the official multi-factor scoring formula:
Ranking Score = (Distance Score × Weight) + (Freshness Score × Weight) + (Stock Score × Weight)

Weights:
- Distance Weight: 0.35
- Freshness Weight: 0.35
- Stock Level Weight: 0.30

Ensures zero-stock facilities are never recommended as available antivenom sources.
Highlights unconfirmed/stale data transparently with warnings.
"""

import math
from typing import List, Dict, Any, Optional
from datetime import timedelta
from django.utils import timezone
from apps.facilities.models import Facility
from .models import AntivenomSearch

# Standard Scoring Weights
WEIGHT_DISTANCE = 0.35
WEIGHT_FRESHNESS = 0.35
WEIGHT_STOCK = 0.30

def haversine_distance_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """
    Computes great-circle distance in kilometres using the Haversine formula.
    """
    R = 6371.0  # Earth mean radius in km
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2 +
         math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) *
         math.sin(dlon / 2) ** 2)
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return round(R * c, 1)

def compute_distance_score(distance_km: float) -> float:
    """
    Distance Score (0 to 100):
    - < 15 km: 100
    - 15 - 30 km: 85
    - 30 - 60 km: 65
    - 60 - 100 km: 40
    - > 100 km: max(0, 100 - distance_km)
    """
    if distance_km <= 15:
        return 100.0
    elif distance_km <= 30:
        return 85.0
    elif distance_km <= 60:
        return 65.0
    elif distance_km <= 100:
        return 40.0
    else:
        return max(5.0, round(100.0 - distance_km, 1))

def compute_freshness_score(freshness_status: str, age_hours: Optional[float] = None) -> float:
    """
    Freshness Score (0 to 100):
    - Confirmed today (within 24 hours): 100
    - Confirmed recent (24-48 hours): 75
    - Confirmation overdue / stale (>48 hours): 20
    - Unconfirmed: 0
    """
    if freshness_status == 'TODAY':
        return 100.0
    elif freshness_status == 'RECENT':
        return 75.0
    elif freshness_status == 'STALE':
        return 20.0
    return 0.0

def compute_stock_score(stock: int) -> float:
    """
    Stock Availability Score (0 to 100):
    - Stock = 0: 0.0 (Zero stock must never be recommended)
    - Stock >= 10: 100.0
    - Stock 6 - 9: 85.0
    - Stock 3 - 5: 65.0
    - Stock 1 - 2: 40.0
    """
    if stock <= 0:
        return 0.0
    elif stock >= 10:
        return 100.0
    elif stock >= 6:
        return 85.0
    elif stock >= 3:
        return 65.0
    else:
        return 40.0

def rank_facilities_for_query(
    state: str,
    lga: str = '',
    user_lat: Optional[float] = None,
    user_lon: Optional[float] = None,
    limit: int = 5,
    record_audit: bool = True,
    channel: str = 'USSD',
    caller_hash: str = ''
) -> List[Dict[str, Any]]:
    """
    Executes transparent ranking across facilities.
    Prioritises same state & LGA, then surrounding facilities.
    """
    facilities_qs = Facility.objects.filter(verified=True).prefetch_related('inventory_transactions')

    # Filter by state if provided
    if state:
        facilities_qs = facilities_qs.filter(state__iexact=state.strip())

    facilities = list(facilities_qs)
    if not facilities and state:
        # Fallback to all facilities across Nigeria if state not matched
        facilities = list(Facility.objects.filter(verified=True).prefetch_related('inventory_transactions'))

    # Determine reference origin coordinate
    ref_lat, ref_lon = user_lat, user_lon
    if ref_lat is None or ref_lon is None:
        # If LGA provided, find average centroid of facilities in that LGA
        if lga:
            lga_facilities = [f for f in facilities if f.lga.lower() == lga.strip().lower()]
            if lga_facilities:
                ref_lat = sum(float(f.latitude) for f in lga_facilities) / len(lga_facilities)
                ref_lon = sum(float(f.longitude) for f in lga_facilities) / len(lga_facilities)

        # Fallback to state centroid or first facility
        if (ref_lat is None or ref_lon is None) and facilities:
            ref_lat = float(facilities[0].latitude)
            ref_lon = float(facilities[0].longitude)

    scored_facilities = []
    now = timezone.now()

    for fac in facilities:
        has_expired = fac.has_expired_stock
        stock = fac.usable_stock

        # Calculate distance
        if ref_lat is not None and ref_lon is not None:
            dist_km = haversine_distance_km(ref_lat, ref_lon, float(fac.latitude), float(fac.longitude))
        else:
            dist_km = 0.0 if fac.lga.lower() == lga.strip().lower() else 50.0

        # Adjust distance bonus if directly in the target LGA
        is_same_lga = bool(lga and fac.lga.lower() == lga.strip().lower())
        distance_score = compute_distance_score(dist_km)
        if is_same_lga and distance_score < 95.0:
            distance_score = min(100.0, distance_score + 15.0)

        # Freshness score
        freshness_status = fac.freshness_status
        last_confirmed = fac.last_confirmed_at
        age_hours = (now - last_confirmed).total_seconds() / 3600 if last_confirmed else None
        freshness_score = compute_freshness_score(freshness_status, age_hours)

        # Stock score
        stock_score = compute_stock_score(stock)

        # Critical filtering rule:
        # Never send a patient towards a facility with 0 stock or expired stock
        if stock <= 0 or has_expired:
            total_score = 0.0
        else:
            total_score = round(
                (distance_score * WEIGHT_DISTANCE) +
                (freshness_score * WEIGHT_FRESHNESS) +
                (stock_score * WEIGHT_STOCK),
                2
            )

        # Stale warning notice
        stale_warning = ""
        if fac.is_stale:
            stale_warning = "Stock information needs confirmation"

        scored_facilities.append({
            'facility': fac,
            'facility_id': fac.id,
            'name': fac.name,
            'facility_type': fac.get_facility_type_display(),
            'state': fac.state,
            'lga': fac.lga,
            'phone': fac.phone,
            'stock': stock,
            'latest_expiry': fac.latest_expiry,
            'expiry_status': fac.expiry_status,
            'has_expired_stock': has_expired,
            'last_confirmed_label': fac.freshness_label,
            'freshness_status': freshness_status,
            'is_stale': fac.is_stale,
            'stale_warning': stale_warning,
            'distance_km': dist_km,
            'is_same_lga': is_same_lga,
            'scoring_breakdown': {
                'distance_score': distance_score,
                'distance_weight': WEIGHT_DISTANCE,
                'freshness_score': freshness_score,
                'freshness_weight': WEIGHT_FRESHNESS,
                'stock_score': stock_score,
                'stock_weight': WEIGHT_STOCK,
                'total_score': total_score,
            },
            'ranking_score': total_score
        })

    # Sort descending by ranking score, then ascending by distance
    scored_facilities.sort(key=lambda x: (x['ranking_score'], -x['distance_km']), reverse=True)

    # Strictly filter out 0-stock and expired-stock facilities from patient recommendations
    viable_facilities = [
        f for f in scored_facilities
        if f['stock'] > 0 and not f.get('has_expired_stock', False)
    ]
    results = viable_facilities[:limit]

    # Operational audit recording
    if record_audit:
        matched = bool(results and results[0]['stock'] > 0)
        best_fac = results[0]['facility'] if results else None
        AntivenomSearch.objects.create(
            state=state or (results[0]['state'] if results else 'Unknown'),
            lga=lga or (results[0]['lga'] if results else ''),
            phone_hash=caller_hash,
            results_found=len(results),
            successful_match=matched,
            selected_facility=best_fac,
            channel=channel
        )

    return results

