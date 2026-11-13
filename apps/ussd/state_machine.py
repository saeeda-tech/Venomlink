"""
USSD State Machine for VenomLink.

Handles multi-turn USSD sessions conforming to African Telco standards
(Africa's Talking, MTN, Airtel, Glo, 9mobile).
Standard interface:
Request: sessionId, serviceCode, phoneNumber, text
Response: CON <prompt> for continuing sessions, END <message> for termination.

Security & Integrity Controls:
1. Registered active reporter lookup & facility association verification.
2. PBKDF2-hashed Staff PIN verification with attempt limits (3) and lockout (15m).
3. Persistent session state with phone binding, expiration (180s), and replay rejection.
4. Expired batch rejection during stock updates.
5. Credentials/PINs are never logged or stored in plaintext.
"""

from datetime import timedelta
from typing import List, Optional
from django.conf import settings
from django.utils import timezone
from apps.facilities.models import Facility, Reporter
from apps.inventory.services import (
    record_stock_update, record_case_treatment,
    validate_expiry_format, is_expiry_date_expired
)
from apps.matching.engine import rank_facilities_for_query
from apps.notifications.sms_gateway import send_sms, normalise_nigerian_phone, SmsPurpose
from .models import UssdSessionState

STATES_LIST = ['Gombe', 'Plateau', 'Kaduna', 'Bauchi', 'Taraba', 'Benue']


def get_lgas_for_state(state: str) -> List[str]:
    """Retrieve distinct LGAs for the given state from registered facilities."""
    lgas = (
        Facility.objects.filter(state__iexact=state, verified=True)
        .values_list('lga', flat=True)
        .distinct()
        .order_by('lga')
    )
    res = list(lgas)
    if not res:
        if state.lower() == 'gombe':
            return ['Kaltungo', 'Billiri', 'Balanga', 'Yamaltu/Deba', 'Gombe']
        elif state.lower() == 'plateau':
            return ['Shendam', 'Pankshin', 'Langtang South', 'Mangu', 'Jos South']
        elif state.lower() == 'kaduna':
            return ['Kaduna South', 'Zaria', 'Kafanchan', 'Soba']
        return ['Central', 'North', 'South']
    return res


class UssdSessionHandler:
    def __init__(self, session_id: str, service_code: str, phone_number: str, text: str):
        self.session_id = (session_id or '').strip()
        self.service_code = (service_code or '*384*20#').strip()
        self.raw_phone = (phone_number or '').strip()
        self.phone_number = normalise_nigerian_phone(self.raw_phone)
        self.text = (text or '').strip()
        self.steps = [part.strip() for part in self.text.split('*')] if self.text else []
        self.reporter = self._find_active_reporter(self.phone_number, self.raw_phone)
        self.session_state: Optional[UssdSessionState] = None

    @staticmethod
    def _find_active_reporter(norm_phone: str, raw_phone: str = '') -> Optional[Reporter]:
        """Lookup active registered reporter with a verified facility association."""
        if not norm_phone and not raw_phone:
            return None
        rep = (
            Reporter.objects.select_related('facility')
            .filter(phone_number=norm_phone, active=True, facility__isnull=False)
            .first()
        )
        if not rep and raw_phone:
            rep = (
                Reporter.objects.select_related('facility')
                .filter(phone_number=raw_phone, active=True, facility__isnull=False)
                .first()
            )
        return rep

    def _load_or_create_session(self) -> Optional[str]:
        """
        Loads or initializes UssdSessionState.
        Returns an error 'END ...' string if the session is expired, hijacked, or replayed.
        """
        if not self.session_id:
            return "END Invalid USSD session identifier."

        timeout_sec = getattr(settings, 'VENOMLINK_CONFIG', {}).get('USSD_SESSION_TIMEOUT_SECONDS', 180)
        now = timezone.now()

        state = UssdSessionState.objects.select_related('authenticated_reporter__facility').filter(
            session_id=self.session_id
        ).first()

        if state is None:
            self.session_state = UssdSessionState.objects.create(
                session_id=self.session_id,
                phone_number=self.phone_number,
                service_code=self.service_code,
                step_count=len(self.steps),
                expires_at=now + timedelta(seconds=timeout_sec),
            )
            return None

        # Verify caller phone matches session owner
        if state.phone_number != self.phone_number:
            return "END Session security violation: caller mismatch."

        # Reject replayed requests on already-terminated sessions
        if state.is_terminated:
            return "END Session has already ended. Please redial."

        # Reject expired sessions
        if now > state.expires_at:
            state.is_terminated = True
            state.save(update_fields=['is_terminated', 'updated_at'])
            return "END Session expired due to inactivity. Please redial."

        state.step_count = len(self.steps)
        state.refresh_expiry()
        state.save(update_fields=['step_count', 'expires_at', 'updated_at'])
        self.session_state = state
        return None

    def process(self) -> str:
        """
        Main entry point for session dispatch.
        Returns 'CON ...' or 'END ...' and updates session termination state.
        """
        session_err = self._load_or_create_session()
        if session_err:
            return session_err

        response = self._dispatch()

        if self.session_state and response.startswith('END'):
            self.session_state.is_terminated = True
            self.session_state.save(update_fields=['is_terminated', 'updated_at'])

        return response

    def _dispatch(self) -> str:
        # Top-level screen
        if not self.steps or self.steps == ['']:
            return self._render_main_menu()

        root_choice = self.steps[0]

        if self.reporter:
            # Registered Facility Reporter Menu Branches
            if root_choice == '1':
                return self._handle_reporter_update_stock(self.steps[1:])
            elif root_choice == '2':
                return self._handle_reporter_report_case(self.steps[1:])
            elif root_choice == '3':
                return self._handle_check_stock()
            elif root_choice == '4':
                return self._handle_find_antivenom(self.steps[1:])
            else:
                return "END Invalid option selected. Please redial."
        else:
            # Public / Alternate Handset Menu Branches
            if root_choice == '1':
                return self._handle_find_antivenom(self.steps[1:])
            elif root_choice == '2':
                return self._handle_staff_authenticated_login(self.steps[1:])
            elif root_choice == '3':
                return self._handle_helpline_info()
            else:
                return "END Invalid option selected. Please redial."

    def _render_main_menu(self) -> str:
        if self.reporter:
            fac = self.reporter.facility
            return (
                f"CON VenomLink — {fac.name}\n"
                f"Role: {self.reporter.get_role_display()}\n\n"
                f"1. Update antivenom stock\n"
                f"2. Report snakebite case\n"
                f"3. Check current stock\n"
                f"4. Find nearby antivenom"
            )
        else:
            return (
                "CON Welcome to VenomLink\n"
                "Antivenom Availability System\n\n"
                "1. Find antivenom near you\n"
                "2. Facility staff stock update\n"
                "3. Emergency snakebite guidance"
            )

    def _authenticate_reporter_pin(self, reporter: Reporter, pin_input: str) -> Optional[str]:
        """
        Verifies the reporter's active status, facility association, lockout state, and PIN.
        Persists authentication state in UssdSessionState so subsequent turns in the same
        session do not re-trigger PIN verification.
        Returns None on success, or an 'END ...' error string on failure.
        """
        if not reporter or not reporter.active:
            return "END Unauthorised: Reporter account is inactive or unregistered."

        if not reporter.facility_id or not reporter.facility:
            return "END Unauthorised: Reporter is not associated with a valid facility."

        if reporter.is_locked_out:
            return (
                "END Account temporarily locked due to multiple failed PIN attempts. "
                "Please try again in 15 minutes."
            )

        # If already authenticated in this session for this exact reporter, allow continuation
        if (
            self.session_state
            and self.session_state.is_authenticated
            and self.session_state.authenticated_reporter_id == reporter.id
        ):
            return None

        if not reporter.verify_pin(pin_input):
            if reporter.is_locked_out:
                return (
                    "END Invalid Staff PIN. Account locked for 15 minutes "
                    "after 3 failed attempts."
                )
            max_attempts = getattr(settings, 'VENOMLINK_CONFIG', {}).get('USSD_MAX_PIN_ATTEMPTS', 3)
            remaining = max(0, max_attempts - reporter.failed_pin_attempts)
            return f"END Invalid Staff PIN. Access denied ({remaining} attempt(s) remaining)."

        if self.session_state:
            self.session_state.is_authenticated = True
            self.session_state.authenticated_reporter = reporter
            self.session_state.save(update_fields=['is_authenticated', 'authenticated_reporter', 'updated_at'])

        return None

    # ----------------------------------------------------
    # FEATURE 1: REGISTERED REPORTER UPDATE STOCK FLOW
    # ----------------------------------------------------
    def _handle_reporter_update_stock(self, steps: List[str]) -> str:
        if not self.reporter or not self.reporter.facility:
            return "END Unauthorised: Facility reporter not identified."

        if self.reporter.is_locked_out:
            return "END Account temporarily locked due to failed PIN attempts. Try again later."

        # Step 0: Prompt for Staff PIN
        if len(steps) == 0:
            return (
                f"CON {self.reporter.facility.name}\n"
                f"Enter your 4-digit Staff PIN to update stock:"
            )

        pin_err = self._authenticate_reporter_pin(self.reporter, steps[0])
        if pin_err:
            return pin_err

        return self._execute_stock_update_steps(
            steps=steps[1:],
            facility=self.reporter.facility,
            reporter=self.reporter
        )

    def _execute_stock_update_steps(
        self,
        steps: List[str],
        facility: Facility,
        reporter: Reporter
    ) -> str:
        # Step 1: Prompt number of vials
        if len(steps) == 0:
            return (
                f"CON {facility.name}\n"
                f"Current stock: {facility.current_stock} vials\n\n"
                f"Enter current number of vials:"
            )

        qty_input = steps[0]
        try:
            quantity = int(qty_input)
            if quantity < 0:
                return "CON Invalid stock quantity.\nPlease enter a whole number >= 0:"
        except ValueError:
            return "CON Invalid stock quantity.\nPlease enter a whole number (e.g. 6):"

        # Step 2: Prompt batch expiry
        if len(steps) == 1:
            current_exp = facility.latest_expiry if facility.latest_expiry != "Not recorded" else "MM/YY"
            return f"CON Enter nearest batch expiry:\n(e.g. 06/27 or enter 0 to keep {current_exp})"

        exp_input = steps[1].strip()
        if len(exp_input) == 4 and exp_input.isdigit():
            exp_input = f"{exp_input[:2]}/{exp_input[2:]}"

        expiry_date = ""
        if exp_input and exp_input != '0':
            if not validate_expiry_format(exp_input):
                return "CON Invalid expiry format.\nPlease enter MM/YY (e.g. 06/27 or 0627):"
            if quantity > 0 and is_expiry_date_expired(exp_input):
                return f"END Cannot record expired antivenom batch ({exp_input}). Quarantine expired vials."
            expiry_date = exp_input

        try:
            tx = record_stock_update(
                facility=facility,
                quantity=quantity,
                expiry_date=expiry_date,
                reporter=reporter,
                notes="Updated via authenticated USSD session"
            )
            return (
                f"END Stock updated successfully.\n\n"
                f"Facility: {facility.name}\n"
                f"Current recorded stock:\n{tx.stock_after} vials\n"
                f"Expiry: {facility.latest_expiry}\n"
                f"Status: {facility.stock_status_label}"
            )
        except ValueError as e:
            return f"END Validation error: {str(e)}"
        except Exception:
            return "END System error updating stock. Please try again."

    # ----------------------------------------------------
    # FEATURE: REPORT SNAKEBITE CASE (AUTHENTICATED)
    # ----------------------------------------------------
    def _handle_reporter_report_case(self, steps: List[str]) -> str:
        if not self.reporter or not self.reporter.facility:
            return "END Unauthorised: Facility reporter not identified."

        if self.reporter.is_locked_out:
            return "END Account temporarily locked due to failed PIN attempts. Try again later."

        facility = self.reporter.facility

        # Step 0: Prompt for Staff PIN
        if len(steps) == 0:
            return (
                f"CON Report Snakebite Treatment\n"
                f"{facility.name}\n"
                f"Enter your 4-digit Staff PIN:"
            )

        pin_err = self._authenticate_reporter_pin(self.reporter, steps[0])
        if pin_err:
            return pin_err

        case_steps = steps[1:]

        # Step 1: Enter vials administered
        if len(case_steps) == 0:
            return (
                f"CON {facility.name}\n"
                f"Available stock: {facility.current_stock} vials\n"
                f"Enter number of vials administered:"
            )

        vials_input = case_steps[0]
        try:
            vials = int(vials_input)
            if vials <= 0:
                return "CON Vials must be at least 1.\nEnter number of vials administered:"
        except ValueError:
            return "CON Invalid number of vials.\nEnter a whole number (e.g. 1 or 2):"

        # Step 2: Select clinical outcome
        if len(case_steps) == 1:
            return (
                "CON Select patient outcome:\n"
                "1. Recovered / Discharged\n"
                "2. Currently under treatment\n"
                "3. Referred / Transferred\n"
                "4. Fatality"
            )

        outcome_map = {
            '1': 'RECOVERED',
            '2': 'UNDER_TREATMENT',
            '3': 'TRANSFERRED',
            '4': 'FATALITY'
        }
        if case_steps[1] not in outcome_map:
            return "END Invalid outcome selection. Please redial."
        outcome = outcome_map[case_steps[1]]

        try:
            record_case_treatment(
                facility=facility,
                vials_used=vials,
                outcome=outcome,
                reporter=self.reporter,
                notes="Reported via authenticated USSD session"
            )
            doc_label = (
                f"{facility.days_of_cover} days"
                if facility.days_of_cover is not None
                else facility.days_of_cover_display
            )
            return (
                f"END Snakebite case recorded.\n\n"
                f"Vials administered: {vials}\n"
                f"Remaining stock at {facility.name}:\n"
                f"{facility.current_stock} vials\n"
                f"Days of cover: {doc_label}"
            )
        except ValueError as e:
            return f"END Cannot record case: {str(e)}"
        except Exception:
            return "END System error logging case."

    # ----------------------------------------------------
    # FEATURE: CHECK CURRENT STOCK
    # ----------------------------------------------------
    def _handle_check_stock(self) -> str:
        facility = self.reporter.facility
        return (
            f"END {facility.name}\n"
            f"LGA: {facility.lga}, {facility.state}\n"
            f"Current stock: {facility.current_stock} vials\n"
            f"Nearest expiry: {facility.latest_expiry} ({facility.expiry_status_label})\n"
            f"Status: {facility.stock_status_label}\n"
            f"Freshness: {facility.freshness_label}"
        )

    # ----------------------------------------------------
    # FEATURE 2: FIND ANTIVENOM (PUBLIC & FACILITY)
    # ----------------------------------------------------
    def _handle_find_antivenom(self, steps: List[str]) -> str:
        # Step 1: Select State
        if len(steps) == 0:
            menu_lines = ["CON Select State:"]
            for idx, st in enumerate(STATES_LIST, start=1):
                menu_lines.append(f"{idx}. {st}")
            return "\n".join(menu_lines)

        state_idx = steps[0]
        try:
            s_num = int(state_idx)
            if 1 <= s_num <= len(STATES_LIST):
                selected_state = STATES_LIST[s_num - 1]
            else:
                return "END Invalid state selection. Please redial."
        except ValueError:
            return "END Invalid state selection. Please redial."

        lgas = get_lgas_for_state(selected_state)

        # Step 2: Select LGA
        if len(steps) == 1:
            menu_lines = [f"CON Select LGA in {selected_state}:"]
            for idx, lga in enumerate(lgas[:6], start=1):
                menu_lines.append(f"{idx}. {lga}")
            return "\n".join(menu_lines)

        lga_idx = steps[1]
        try:
            l_num = int(lga_idx)
            if 1 <= l_num <= len(lgas):
                selected_lga = lgas[l_num - 1]
            else:
                selected_lga = ""
        except ValueError:
            selected_lga = ""

        # Step 3: Run matching engine
        results = rank_facilities_for_query(
            state=selected_state,
            lga=selected_lga,
            limit=2,
            channel='USSD',
            caller_hash=self.phone_number[-4:] if len(self.phone_number) >= 4 else 'USSD'
        )

        if not results or results[0]['stock'] == 0:
            return (
                f"END No confirmed antivenom currently available in {selected_lga or selected_state}.\n\n"
                f"Please contact emergency services (112) or local health authorities."
            )

        has_demo = any(res.get('is_demo') for res in results[:2])
        header = "END Nearest antivenom (DEMO DATA):\n" if has_demo else "END Nearest available antivenom:\n"
        lines = [header]
        sms_prefix = "VenomLink (DEMO - Simulated Data)" if has_demo else "VenomLink"
        sms_lines = [f"{sms_prefix}: Antivenom availability for {selected_lga}, {selected_state}:\n"]

        for idx, res in enumerate(results[:2], start=1):
            name = res['name']
            stock = res['stock']
            phone = res.get('phone_display') or res.get('phone') or "Contact unavailable"
            label = res['last_confirmed_label']
            dist = res['distance_km']

            if idx == 1:
                lines.append(f"{idx}. {name}")
                lines.append(f"{stock} vials available")
                lines.append(f"{label}")
                lines.append(f"Call: {phone}\n")
            else:
                lines.append(f"{idx}. {name}")
                lines.append(f"{stock} vials available")
                lines.append(f"{label}")
                lines.append(f"Approx. {dist} km away\n")

            sms_lines.append(f"{idx}. {name}: {stock} vials ({label}). Contact: {phone}")

        lines.append("Details sent via SMS.")

        sms_text = "\n".join(sms_lines)
        send_sms(
            recipient=self.phone_number,
            message=sms_text,
            purpose=SmsPurpose.SEARCH_RESULT,
            facility=results[0]['facility'] if results else None
        )

        return "\n".join(lines)

    # ----------------------------------------------------
    # AUTHENTICATED STAFF LOGIN FROM ALTERNATE HANDSET
    # ----------------------------------------------------
    def _handle_staff_authenticated_login(self, steps: List[str]) -> str:
        """
        Requires registered staff phone number + active reporter status +
        facility association + valid 4-digit Staff PIN.
        Never authenticates by Facility ID or public facility phone number.
        """
        if len(steps) == 0:
            return "CON Staff Authentication\nEnter your registered staff phone number:"

        staff_phone_raw = steps[0].strip()
        staff_phone_norm = normalise_nigerian_phone(staff_phone_raw)
        reporter = self._find_active_reporter(staff_phone_norm, staff_phone_raw)

        if not reporter:
            return "END Unauthorised: No active registered reporter found for this phone number."

        if reporter.is_locked_out:
            return (
                "END Account temporarily locked due to multiple failed PIN attempts. "
                "Please try again in 15 minutes."
            )

        if len(steps) == 1:
            return (
                f"CON Staff: {reporter.full_name or reporter.phone_number}\n"
                f"Facility: {reporter.facility.name}\n"
                f"Enter your 4-digit Staff PIN:"
            )

        pin_err = self._authenticate_reporter_pin(reporter, steps[1])
        if pin_err:
            return pin_err

        return self._execute_stock_update_steps(
            steps=steps[2:],
            facility=reporter.facility,
            reporter=reporter
        )

    def _handle_helpline_info(self) -> str:
        return (
            "END Snakebite Emergency Guidelines:\n"
            "1. Keep patient calm and immobilised\n"
            "2. Do NOT cut, suck, or apply tourniquet\n"
            "3. Transport immediately to nearest antivenom centre\n\n"
            "Emergency Helpline: 0800-SNAKE-AID (0800-762-5324)"
        )
