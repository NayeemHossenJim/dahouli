import time
import random
import string
import re
from urllib.parse import quote, unquote, urlparse, urlunparse

from telegram import send_message
from checker import check_appointments
from config import (
    BLOCK_BACKOFF_MAX_SECONDS,
    BLOCK_BACKOFF_MIN_SECONDS,
    CHECK_INTERVAL_MAX_SECONDS,
    CHECK_INTERVAL_MIN_SECONDS,
    DRY_RUN,
    NOTIFY_ON_UNAVAILABLE,
    NOTIFY_COOLDOWN_SECONDS,
    OXYLABS_CITY,
    OXYLABS_COUNTRY,
    OXYLABS_ENABLED,
    OXYLABS_ENTRY,
    OXYLABS_PASSWORD,
    OXYLABS_PORT,
    OXYLABS_SESSION_TIME_MINUTES,
    OXYLABS_STICKY_SESSION,
    OXYLABS_USERNAME,
    PROXIES,
    PROXY_ROTATE_EVERY,
    RUN_ONCE,
    TARGET_PROCEDURE_TEXT,
    TARGET_PROVINCE,
    UNAVAILABLE_NOTIFY_COOLDOWN_SECONDS,
    validate_required_config,
)

BLOCK_REASON_MARKERS = [
    "blocked or challenged",
    "timeout under challenge/block",
    "err_tunnel_connection_failed",
    "tunnel_connection_failed",
    "tunnel connection failed",
    "proxy connection",
    "proxy authentication",
    "proxy error",
    "connection refused",
    "connection reset",
    "forbidden",
    "access denied",
    "temporarily blocked",
    "captcha",
    "cloudflare",
    "verify you are human",
    "are you a robot",
    "target page, context or browser has been closed",
    "page.wait_for_timeout: target page, context or browser has been closed",
]

SUPPORT_ID_REASON_MARKERS = [
    "support id",
    "request rejected",
    "requested url was rejected",
]

# Marker that indicates appointments exist but only via Cl@ve login.
# Used to craft a more informative Telegram alert.
CLAVE_ONLY_MARKER = "cl@ve only"


def _safe_interval_seconds():
    lo = min(CHECK_INTERVAL_MIN_SECONDS, CHECK_INTERVAL_MAX_SECONDS)
    hi = max(CHECK_INTERVAL_MIN_SECONDS, CHECK_INTERVAL_MAX_SECONDS)
    return random.randint(lo, hi)


def _block_backoff_seconds():
    lo = min(BLOCK_BACKOFF_MIN_SECONDS, BLOCK_BACKOFF_MAX_SECONDS)
    hi = max(BLOCK_BACKOFF_MIN_SECONDS, BLOCK_BACKOFF_MAX_SECONDS)
    return random.randint(lo, hi)


def _is_blocked_reason(reason):
    lowered = (reason or "").strip().lower()
    return any(marker in lowered for marker in BLOCK_REASON_MARKERS)


def _is_support_id_reason(reason):
    lowered = (reason or "").strip().lower()
    return any(marker in lowered for marker in SUPPORT_ID_REASON_MARKERS)


def _mask_proxy(proxy_value):
    if not proxy_value:
        return "none"
    if "@" not in proxy_value:
        return proxy_value
    return proxy_value.split("@", 1)[1]


def _random_session_id(length=10):
    alphabet = string.ascii_uppercase + string.digits
    return "".join(random.choice(alphabet) for _ in range(length))


def _refresh_oxylabs_session_in_proxy_url(proxy_value):
    """Refresh sessid token for Oxylabs-style proxy URLs from PROXIES_FILE/PROXIES.

    Returns:
        (proxy_url, session_id, refreshed)
    """
    if not proxy_value:
        return proxy_value, None, False

    try:
        parsed = urlparse(proxy_value)
        if not parsed.scheme:
            parsed = urlparse(f"http://{proxy_value}")

        host = (parsed.hostname or "").lower()
        if "oxylabs" not in host or parsed.username is None:
            return proxy_value, None, False

        username = unquote(parsed.username)
        if "customer-" not in username.lower():
            return proxy_value, None, False

        session_id = _random_session_id()
        if re.search(r"sessid-[^-]+", username, flags=re.IGNORECASE):
            refreshed_username = re.sub(
                r"sessid-[^-]+",
                f"sessid-{session_id}",
                username,
                count=1,
                flags=re.IGNORECASE,
            )
        else:
            refreshed_username = f"{username}-sessid-{session_id}"

        password = unquote(parsed.password or "")
        auth = quote(refreshed_username, safe="")
        if parsed.password is not None:
            auth = f"{auth}:{quote(password, safe='')}"

        netloc = f"{auth}@{parsed.hostname}"
        if parsed.port:
            netloc = f"{netloc}:{parsed.port}"

        refreshed_proxy = urlunparse(
            (
                parsed.scheme or "http",
                netloc,
                parsed.path or "",
                parsed.params or "",
                parsed.query or "",
                parsed.fragment or "",
            )
        )
        return refreshed_proxy, session_id, True
    except Exception:
        return proxy_value, None, False


def _build_oxylabs_proxy(session_id=None):
    username_parts = [f"customer-{OXYLABS_USERNAME}"]

    if OXYLABS_COUNTRY:
        username_parts.append(f"cc-{OXYLABS_COUNTRY.upper()}")
    if OXYLABS_CITY:
        username_parts.append(f"city-{OXYLABS_CITY.lower().replace(' ', '_')}")
    if OXYLABS_STICKY_SESSION and session_id:
        username_parts.append(f"sessid-{session_id}")
    if OXYLABS_STICKY_SESSION and OXYLABS_SESSION_TIME_MINUTES > 0:
        username_parts.append(f"sesstime-{OXYLABS_SESSION_TIME_MINUTES}")

    username = "-".join(username_parts)
    return (
        f"http://{quote(username, safe='')}:{quote(OXYLABS_PASSWORD, safe='')}"
        f"@{OXYLABS_ENTRY}:{OXYLABS_PORT}"
    )


def _oxylabs_profile_summary():
    country = OXYLABS_COUNTRY.upper() if OXYLABS_COUNTRY else "any"
    city = OXYLABS_CITY.lower().replace(" ", "_") if OXYLABS_CITY else "any"
    session_mode = "sticky" if OXYLABS_STICKY_SESSION else "rotating"
    sesstime = (
        f"{OXYLABS_SESSION_TIME_MINUTES}m"
        if OXYLABS_SESSION_TIME_MINUTES > 0
        else "default"
    )
    return (
        f"entry={OXYLABS_ENTRY}:{OXYLABS_PORT} "
        f"country={country} city={city} mode={session_mode} sesstime={sesstime}"
    )


def _build_alert_message(reason):
    """Build a Telegram alert message tailored to the type of availability found."""
    reason_lower = (reason or "").lower()

    if CLAVE_ONLY_MARKER in reason_lower:
        return (
            f"[WARNING] CITA PREVIA ALERT  -  {TARGET_PROVINCE}\n"
            f"Procedure: {TARGET_PROCEDURE_TEXT}\n\n"
            f"Appointments are available but ONLY via Cl@ve login.\n"
            f"No slots available for 'sin Cl@ve' booking at this moment.\n\n"
            f"[INFO] Log in with Cl@ve at:\n"
            f"https://icp.administracionelectronica.gob.es/icpplus/index.html"
        )

    return (
        f"[SUCCESS] CITA PREVIA ALERT  -  {TARGET_PROVINCE}\n"
        f"Procedure: {TARGET_PROCEDURE_TEXT}\n\n"
        f"Appointment slots appear to be available!\n"
        f"Book now (without Cl@ve):\n"
        f"https://icp.administracionelectronica.gob.es/icpplus/index.html"
    )


def _build_unavailable_message(reason):
    """Build a Telegram status message for unavailable/error outcomes."""
    reason_text = (reason or "Unknown reason").strip()
    reason_lower = reason_text.lower()

    if _is_support_id_reason(reason_text):
        return (
            f"[WARNING] CITA PREVIA STATUS  -  {TARGET_PROVINCE}\n"
            f"Procedure: {TARGET_PROCEDURE_TEXT}\n\n"
            f"WAF support-id rejection while checking appointments.\n"
            f"Reason: {reason_text}\n\n"
            f"The monitor will force proxy/session refresh and apply extra backoff."
        )

    if _is_blocked_reason(reason_text):
        return (
            f"[WARNING] CITA PREVIA STATUS  -  {TARGET_PROVINCE}\n"
            f"Procedure: {TARGET_PROCEDURE_TEXT}\n\n"
            f"Blocked/challenged while checking appointments.\n"
            f"Reason: {reason_text}\n\n"
            f"The monitor will rotate proxy/session and retry."
        )

    if "no appointment" in reason_lower or "no appointments" in reason_lower:
        return (
            f"[INFO] CITA PREVIA STATUS  -  {TARGET_PROVINCE}\n"
            f"Procedure: {TARGET_PROCEDURE_TEXT}\n\n"
            f"No appointments available right now.\n"
            f"Reason: {reason_text}"
        )

    return (
        f"[INFO] CITA PREVIA STATUS  -  {TARGET_PROVINCE}\n"
        f"Procedure: {TARGET_PROCEDURE_TEXT}\n\n"
        f"No bookable appointment detected in this check.\n"
        f"Reason: {reason_text}"
    )


def run_monitor():
    validate_required_config(require_telegram=not DRY_RUN)

    if OXYLABS_ENABLED:
        print(f"Startup: Oxylabs profile {_oxylabs_profile_summary()}")

    run_number = 0
    proxy_index = -1
    runs_on_current_proxy = 0   # how many checks have used the current proxy/session
    current_proxy_runtime = None
    current_proxy_runtime_session_id = None
    oxylabs_session_id = None
    last_notification_at = 0.0
    last_unavailable_notification_at = 0.0
    last_unavailable_reason = ""

    try:
        while True:
            run_number += 1

            # ── Proxy selection ────────────────────────────────────────────────
            active_proxy = None
            if OXYLABS_ENABLED:
                if OXYLABS_STICKY_SESSION:
                    # Rotate the Oxylabs session ID every PROXY_ROTATE_EVERY runs
                    if runs_on_current_proxy >= PROXY_ROTATE_EVERY or not oxylabs_session_id:
                        oxylabs_session_id = _random_session_id()
                        runs_on_current_proxy = 0
                        print(f"Run {run_number}: Oxylabs session rotated -> new session={oxylabs_session_id}")
                    runs_on_current_proxy += 1
                active_proxy = _build_oxylabs_proxy(session_id=oxylabs_session_id)
            elif PROXIES:
                # Rotate to the next proxy every PROXY_ROTATE_EVERY runs
                if runs_on_current_proxy >= PROXY_ROTATE_EVERY:
                    proxy_index = (proxy_index + 1) % len(PROXIES)
                    runs_on_current_proxy = 0
                    current_proxy_runtime = None
                    current_proxy_runtime_session_id = None
                    print(f"Run {run_number}: proxy rotated -> {_mask_proxy(PROXIES[proxy_index])}")
                if proxy_index < 0:
                    proxy_index = 0

                # Start a new proxy cycle: refresh Oxylabs sessid if this list proxy uses Oxylabs format.
                if runs_on_current_proxy == 0 or not current_proxy_runtime:
                    refreshed_proxy, refreshed_session_id, refreshed = _refresh_oxylabs_session_in_proxy_url(
                        PROXIES[proxy_index]
                    )
                    current_proxy_runtime = refreshed_proxy
                    current_proxy_runtime_session_id = refreshed_session_id if refreshed else None
                    if refreshed:
                        print(
                            f"Run {run_number}: list-proxy Oxylabs session refreshed -> "
                            f"{current_proxy_runtime_session_id}"
                        )

                runs_on_current_proxy += 1
                active_proxy = current_proxy_runtime or PROXIES[proxy_index]

            # ── Run info ───────────────────────────────────────────────────────
            print(f"\nRun {run_number}: checking {TARGET_PROVINCE} / {TARGET_PROCEDURE_TEXT}")
            if OXYLABS_ENABLED and OXYLABS_STICKY_SESSION and oxylabs_session_id:
                print(
                    f"Run {run_number}: proxy oxylabs {OXYLABS_ENTRY}:{OXYLABS_PORT} "
                    f"session={oxylabs_session_id}"
                )
            else:
                print(f"Run {run_number}: proxy {_mask_proxy(active_proxy)}")
                if current_proxy_runtime_session_id:
                    print(f"Run {run_number}: list-proxy session={current_proxy_runtime_session_id}")

            # ── Check ──────────────────────────────────────────────────────────
            try:
                available, reason = check_appointments(proxy_value=active_proxy)
            except Exception as exc:
                available, reason = False, f"Unexpected monitor error: {exc}"

            print(f"Run {run_number}: available={available} | reason={reason}")

            # ── Block / challenge handling ─────────────────────────────────────
            blocked_or_challenged = _is_blocked_reason(reason)
            support_id_blocked = _is_support_id_reason(reason)
            if blocked_or_challenged:
                runs_on_current_proxy = PROXY_ROTATE_EVERY  # force rotation on next run
                if support_id_blocked:
                    print(f"Run {run_number}: support-id rejection detected  -  escalating recovery")

                if OXYLABS_ENABLED and OXYLABS_STICKY_SESSION:
                    previous_session = oxylabs_session_id or "none"
                    oxylabs_session_id = None
                    print(
                        f"Run {run_number}: block/challenge  -  forcing Oxylabs session rotation "
                        f"(previous session={previous_session})"
                    )
                elif PROXIES:
                    current_proxy_runtime = None
                    current_proxy_runtime_session_id = None
                    if support_id_blocked:
                        print(
                            f"Run {run_number}: support-id recovery  -  force proxy rotation "
                            f"and list-proxy session refresh"
                        )
                    else:
                        print(f"Run {run_number}: block/challenge  -  proxy will rotate on next run")
                else:
                    print(f"Run {run_number}: block/challenge  -  no proxy configured")

            # ── Notification ───────────────────────────────────────────────────
            now = time.time()
            if available:
                if now - last_notification_at >= NOTIFY_COOLDOWN_SECONDS:
                    alert_text = _build_alert_message(reason)
                    if DRY_RUN:
                        delivered = True
                        print(f"Run {run_number}: DRY_RUN  -  skipping Telegram send")
                        print(f"Run {run_number}: Would have sent:\n{alert_text}")
                    else:
                        delivered = send_message(alert_text)

                    if delivered:
                        last_notification_at = now
                        print(f"Run {run_number}: Telegram alert sent")
                    else:
                        print(f"Run {run_number}: Telegram alert failed to send")
                else:
                    remaining = int(NOTIFY_COOLDOWN_SECONDS - (now - last_notification_at))
                    print(f"Run {run_number}: alert suppressed by cooldown ({remaining}s remaining)")
            elif NOTIFY_ON_UNAVAILABLE:
                same_reason = (reason or "").strip() == last_unavailable_reason
                cooldown_active = (
                    now - last_unavailable_notification_at
                    < UNAVAILABLE_NOTIFY_COOLDOWN_SECONDS
                )

                if not (same_reason and cooldown_active):
                    status_text = _build_unavailable_message(reason)
                    if DRY_RUN:
                        delivered = True
                        print(f"Run {run_number}: DRY_RUN  -  skipping Telegram send")
                        print(f"Run {run_number}: Would have sent:\n{status_text}")
                    else:
                        delivered = send_message(status_text)

                    if delivered:
                        last_unavailable_notification_at = now
                        last_unavailable_reason = (reason or "").strip()
                        print(f"Run {run_number}: Telegram status sent")
                    else:
                        print(f"Run {run_number}: Telegram status failed to send")
                else:
                    remaining = int(
                        UNAVAILABLE_NOTIFY_COOLDOWN_SECONDS
                        - (now - last_unavailable_notification_at)
                    )
                    print(
                        f"Run {run_number}: status suppressed by cooldown "
                        f"({remaining}s remaining, same reason)"
                    )

            # ── Sleep ──────────────────────────────────────────────────────────
            wait_time = _safe_interval_seconds()
            if blocked_or_challenged:
                extra_backoff = _block_backoff_seconds()
                wait_time += extra_backoff
                print(f"Run {run_number}: block backoff applied (+{extra_backoff}s)")

                if support_id_blocked:
                    support_id_extra_backoff = max(60, _block_backoff_seconds())
                    wait_time += support_id_extra_backoff
                    print(
                        f"Run {run_number}: support-id extra backoff applied "
                        f"(+{support_id_extra_backoff}s)"
                    )

            print(f"Run {run_number}: sleeping {wait_time}s until next check")

            if RUN_ONCE:
                print(f"Run {run_number}: RUN_ONCE=true  -  exiting")
                break

            time.sleep(wait_time)
    except KeyboardInterrupt:
        print("Monitor stopped by user (Ctrl+C).")
        return


if __name__ == "__main__":
    run_monitor()