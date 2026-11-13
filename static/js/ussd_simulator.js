/**
 * VenomLink Interactive USSD Handset Simulator
 * Emulates African Telco USSD session protocol (*384*20#) in real-time.
 */

document.addEventListener('DOMContentLoaded', function () {
  const displayScreen = document.getElementById('ussd-screen');
  const inputField = document.getElementById('ussd-input');
  const sendBtn = document.getElementById('ussd-send-btn');
  const resetBtn = document.getElementById('ussd-reset-btn');
  const phoneSelect = document.getElementById('sim-phone-select');
  const smsInbox = document.getElementById('sms-inbox-container');

  if (!displayScreen || !inputField) return;

  let currentSessionId = 'sim-' + Math.floor(Math.random() * 100000);
  let accumulatedText = '';
  let isSessionFinished = false;

  // Initialize session
  initSession();

  function initSession() {
    currentSessionId = 'sim-' + Math.floor(Math.random() * 100000);
    accumulatedText = '';
    isSessionFinished = false;
    inputField.disabled = false;
    inputField.value = '';
    if (sendBtn) sendBtn.disabled = false;

    displayScreen.textContent = 'Dialing *384*20#...\nConnecting to VenomLink gateway...';
    executeUssdRequest('');
  }

  function executeUssdRequest(userEntry) {
    const phone = phoneSelect ? phoneSelect.value : '+2348030000001';

    let textToSend = '';
    if (userEntry) {
      if (accumulatedText) {
        textToSend = accumulatedText + '*' + userEntry;
      } else {
        textToSend = userEntry;
      }
    } else {
      textToSend = accumulatedText;
    }

    displayScreen.textContent = 'Transmitting...';

    fetch('/api/ussd/simulate/', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-CSRFToken': window.getCsrfToken ? window.getCsrfToken() : ''
      },
      body: JSON.stringify({
        sessionId: currentSessionId,
        serviceCode: '*384*20#',
        phoneNumber: phone,
        text: textToSend
      })
    })
      .then(res => res.json())
      .then(data => {
        // Only append to accumulated session path if input did not trigger a validation retry prompt
        if (!data.display_text.startsWith('Invalid ') && !data.display_text.startsWith('Vials must ')) {
          accumulatedText = textToSend;
        }
        displayScreen.textContent = data.display_text;
        isSessionFinished = data.is_finished;

        if (isSessionFinished) {
          inputField.disabled = true;
          if (sendBtn) sendBtn.disabled = true;
          // Refresh SMS inbox to catch newly dispatched SMS
          fetchRecentSms(phone);
        } else {
          inputField.disabled = false;
          inputField.value = '';
          inputField.focus();
        }
      })
      .catch(err => {
        displayScreen.textContent = 'Network/USSD error: ' + err.message;
      });
  }

  if (sendBtn) {
    sendBtn.addEventListener('click', function () {
      submitCurrentInput();
    });
  }

  inputField.addEventListener('keydown', function (e) {
    if (e.key === 'Enter') {
      submitCurrentInput();
    }
  });

  function submitCurrentInput() {
    if (isSessionFinished) return;
    const val = inputField.value.trim();
    if (val === '') return;
    executeUssdRequest(val);
  }

  if (resetBtn) {
    resetBtn.addEventListener('click', function () {
      initSession();
    });
  }

  if (phoneSelect) {
    phoneSelect.addEventListener('change', function () {
      initSession();
    });
  }

  // Keypad button clicks
  document.querySelectorAll('.keypad-btn').forEach(btn => {
    btn.addEventListener('click', function () {
      if (isSessionFinished) return;
      const char = this.getAttribute('data-key');
      if (char === 'SEND') {
        submitCurrentInput();
      } else if (char === 'CLR') {
        inputField.value = '';
      } else if (char) {
        inputField.value += char;
        inputField.focus();
      }
    });
  });

  // Fetch recent SMS logs for this phone
  function fetchRecentSms(phone) {
    if (!smsInbox) return;
    fetch('/api/notifications/sms-logs/')
      .then(res => res.json())
      .then(data => {
        const results = data.results || data;
        if (!Array.isArray(results) || results.length === 0) return;

        // Show latest SMS
        const latest = results[0];
        smsInbox.innerHTML = `
          <div class="sms-bubble">
            <div style="font-size: 0.75rem; color: #546E7A; margin-bottom: 4px;">
              From: VenomLink &bull; To: ${latest.recipient} &bull; ${new Date(latest.timestamp).toLocaleTimeString()}
            </div>
            <div>${escapeHtml(latest.message)}</div>
          </div>
        `;
      })
      .catch(e => console.error('Error fetching SMS:', e));
  }

  function escapeHtml(text) {
    if (!text) return '';
    return String(text)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;');
  }
});

