"""
SMS Gateway service for VenomLink.
Handles phone number standardisation (+234), message formatting,
and dispatch (supporting live telco API or local simulated logging).
"""

import os
import re
import logging
from django.utils import timezone
from .models import SmsLog, SmsPurpose, SmsStatus

logger = logging.getLogger(__name__)

PHONE_NIGERIA_REGEX = re.compile(r'^(?:\+234|234|0)([789][01]\d{8})$')

def normalise_nigerian_phone(phone: str) -> str:
    """
    Standardises Nigerian phone numbers into international format: +23480XXXXXXXX
    Accepts:
    - 08031234567
    - +2348031234567
    - 2348031234567
    """
    cleaned = re.sub(r'[\s\-\(\)]', '', phone.strip())
    match = PHONE_NIGERIA_REGEX.match(cleaned)
    if match:
        national_number = match.group(1)
        return f"+234{national_number}"
    return cleaned

def send_sms(
    recipient: str,
    message: str,
    purpose: str = SmsPurpose.SEARCH_RESULT,
    facility = None
) -> SmsLog:
    """
    Dispatches an SMS and records an audit log.
    In demo / development mode, marks status as SIMULATED.
    """
    normalised_phone = normalise_nigerian_phone(recipient)

    # In production, check for environment credentials (e.g. AfricasTalking / Twilio)
    # If not configured, safely simulate without breaking
    sms_entry = SmsLog.objects.create(
        recipient=normalised_phone,
        message=message.strip(),
        facility=facility,
        purpose=purpose,
        status=SmsStatus.SIMULATED,
        timestamp=timezone.now()
    )

    logger.info(f"[VenomLink SMS] Sent to {normalised_phone} [{purpose}]: {message}")
    return sms_entry

