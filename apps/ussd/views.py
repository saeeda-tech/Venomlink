"""
USSD Webhook and Gateway Views for VenomLink.
Compliant with Africa's Talking and standard Nigerian Telco specifications.

Security Controls:
- Rejects GET and non-POST methods with HTTP 405.
- Validates required fields (sessionId, serviceCode, phoneNumber) with HTTP 400.
- Verifies gateway authentication token/header using constant-time comparison when configured.
- Never logs USSD text payloads containing Staff PINs.
"""

import hmac
from django.conf import settings
from django.http import HttpResponse
from django.views.decorators.csrf import csrf_exempt
from rest_framework import views, status, permissions
from rest_framework.response import Response
from apps.facilities.permissions import DemoModeOnly
from apps.facilities.throttling import UssdSimulatorRateThrottle
from .state_machine import UssdSessionHandler


def _verify_gateway_auth(request) -> bool:
    """
    Verifies the USSD gateway's callback token when USSD_GATEWAY_TOKEN is configured.
    Africa's Talking supports callback URL Basic Auth / Authorization header or custom header.
    Uses hmac.compare_digest to prevent timing attacks.
    """
    expected_token = getattr(settings, 'VENOMLINK_CONFIG', {}).get('USSD_GATEWAY_TOKEN', '')
    if not expected_token:
        return True

    provided = (
        request.META.get('HTTP_X_GATEWAY_TOKEN', '')
        or request.META.get('HTTP_AUTHORIZATION', '')
    ).strip()

    if provided.lower().startswith('bearer '):
        provided = provided[7:].strip()
    elif provided.lower().startswith('basic '):
        provided = provided[6:].strip()

    if not provided:
        return False

    return hmac.compare_digest(provided.encode('utf-8'), expected_token.encode('utf-8'))


@csrf_exempt
def ussd_gateway_callback(request):
    """
    Standard Telco USSD callback endpoint (POST only).
    Required POST fields:
    - sessionId
    - serviceCode
    - phoneNumber
    Optional POST field:
    - text (defaults to '')
    Returns plain text: 'CON ...' or 'END ...'
    """
    if request.method != 'POST':
        return HttpResponse(
            "Method Not Allowed. USSD webhook accepts POST requests only.",
            status=405,
            content_type='text/plain; charset=utf-8'
        )

    if not _verify_gateway_auth(request):
        return HttpResponse(
            "END Unauthorised USSD gateway request.",
            status=403,
            content_type='text/plain; charset=utf-8'
        )

    session_id = (request.POST.get('sessionId') or request.POST.get('session_id') or '').strip()
    service_code = (request.POST.get('serviceCode') or request.POST.get('service_code') or '').strip()
    phone_number = (request.POST.get('phoneNumber') or request.POST.get('phone_number') or '').strip()
    text = request.POST.get('text', '')

    if not session_id or not service_code or not phone_number:
        return HttpResponse(
            "Bad Request: Missing required USSD fields (sessionId, serviceCode, phoneNumber).",
            status=400,
            content_type='text/plain; charset=utf-8'
        )

    if len(session_id) > 128 or len(phone_number) > 30 or len(str(text)) > 500:
        return HttpResponse(
            "Bad Request: Malformed USSD field length.",
            status=400,
            content_type='text/plain; charset=utf-8'
        )

    handler = UssdSessionHandler(
        session_id=session_id,
        service_code=service_code,
        phone_number=phone_number,
        text=str(text)
    )
    ussd_response = handler.process()

    return HttpResponse(ussd_response, content_type='text/plain; charset=utf-8')


class UssdSimulationAPIView(views.APIView):
    """
    JSON API endpoint powering the interactive on-screen USSD Handset Simulator.
    Enforces the exact same UssdSessionHandler PIN authentication, lockout, and
    session state rules as the live telco callback.

    Only available when SYSTEM_DEMO_MODE is enabled (returns 403/404-style denial otherwise),
    and rate limited per client IP, because it exposes the PIN-authenticated state machine
    to anonymous browsers.
    """
    permission_classes = [DemoModeOnly]
    throttle_classes = [UssdSimulatorRateThrottle]

    def post(self, request):
        session_id = str(request.data.get('sessionId', '')).strip()
        service_code = str(request.data.get('serviceCode', '*384*20#')).strip()
        phone_number = str(request.data.get('phoneNumber', '')).strip()
        text = str(request.data.get('text', ''))

        if not session_id or not phone_number:
            return Response(
                {'error': 'sessionId and phoneNumber are required.'},
                status=status.HTTP_400_BAD_REQUEST
            )

        handler = UssdSessionHandler(
            session_id=session_id,
            service_code=service_code,
            phone_number=phone_number,
            text=text
        )
        ussd_output = handler.process()

        is_end = ussd_output.startswith('END')
        clean_text = (
            ussd_output[4:].strip()
            if (ussd_output.startswith('CON ') or ussd_output.startswith('END '))
            else ussd_output
        )

        return Response({
            'sessionId': session_id,
            'serviceCode': service_code,
            'phoneNumber': phone_number,
            'raw_output': ussd_output,
            'display_text': clean_text,
            'is_finished': is_end,
            'status': 'END' if is_end else 'CON'
        })
