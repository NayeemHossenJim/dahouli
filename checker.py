import random
import re
import time
import unicodedata
import html as html_lib
from datetime import datetime
from pathlib import Path
from urllib.parse import unquote, urlparse

from playwright.sync_api import sync_playwright
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from config import (
    ACTION_TIMEOUT_MS,
    ACTION_DELAY_MAX_MS,
    ACTION_DELAY_MIN_MS,
    DEBUG_ARTIFACTS_DIR,
    HEADLESS,
    MANUAL_ALLOW_WAIT_SECONDS,
    NAVIGATION_TIMEOUT_MS,
    NATIONALITY,
    NIE,
    NAME,
    PAGE_TIMEOUT_MS,
    PROXY_PREFLIGHT_ENABLED,
    PROXY_PREFLIGHT_TIMEOUT_MS,
    PROXY_PREFLIGHT_URLS,
    SAVE_DEBUG_ARTIFACTS,
    STAGE_READY_POLL_MS,
    STAGE_READY_TIMEOUT_MS,
    TARGET_PROCEDURE_TEXT,
    TARGET_PROVINCE,
    UNKNOWN_STAGE_EXP_BACKOFF,
    UNKNOWN_STAGE_MAX_RETRIES,
    UNKNOWN_STAGE_MAX_TOTAL_MS,
    UNKNOWN_STAGE_RETRY_BASE_MS,
    UNKNOWN_STAGE_RETRY_MAX_MS,
    UNKNOWN_STAGE_RETRY_STEP_MS,
)

URL = "https://icp.administracionelectronica.gob.es/icpplus/index.html"

NO_APPOINTMENT_MARKERS = [
    "no hay citas disponibles",
    "en este momento no hay citas disponibles",
]

AVAILABILITY_HINTS = [
    "seleccione fecha",
    "seleccionar fecha",
    "seleccione hora",
    "horas disponibles",
    "confirmar cita",
    "select date",
    "select time",
    "available slots",
]

CLAVE_ONLY_MARKERS = [
    "no hay citas disponibles para la reserva sin cl@ve",
    "si tienen a su disposicion mediante el uso de cl@ve, citas disponibles",
    "citas disponibles para su reserva",
]

BLOCK_OR_CHALLENGE_MARKERS = [
    "request rejected",
    "requested url was rejected",
    "access denied",
    "forbidden",
    "temporarily blocked",
    "intrusion prevention triggered",
    "intrusion prevention violation",
    "fortigate intrusion prevention",
    "blocked by intrusion prevention",
    "verify you are human",
    "captcha",
    "cloudflare",
    "are you a robot",
    "verifica que eres humano",
    "acceso denegado",
    "support id",
    "please enable javascript to view the page content",
    "something went wrong",
]

FINAL_MENU_ACTION_GROUPS = {
    "request": [
        "request appointment",
        "solicitar cita",
    ],
    "consult": [
        "consult confirmed appointments",
        "consultar citas confirmadas",
        "consultar cita",
    ],
    "cancel": [
        "cancel appointment",
        "cancelar cita",
        "anular cita",
    ],
    "exit": [
        "go out",
        "salir",
    ],
}

# ---------------------------------------------------------------------------
# Realistic User-Agent pool  -  rotate on every browser launch
# Keep these updated; a stale UA is a strong bot signal.
# ---------------------------------------------------------------------------
_USER_AGENTS = [
    # Chrome 130 on Windows
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    # Chrome 131 on Windows
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    # Chrome 132 on Windows
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36",
    # Chrome 133 on Windows
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36",
    # Chrome 130 on macOS
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    # Chrome 132 on macOS
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36",
]

# ---------------------------------------------------------------------------
# Stealth JavaScript injected into every page before any scripts run.
# This is the primary anti-detection layer  -  it patches all standard
# fingerprinting vectors that identify headless Playwright/Chromium.
# ---------------------------------------------------------------------------
_STEALTH_INIT_SCRIPT = """
() => {
    // 1. Remove the webdriver flag  -  the #1 bot detector check
    try {
        Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
    } catch(e) {}

    // 2. Add a realistic window.chrome object (absent in headless Chromium)
    try {
        if (!window.chrome) {
            window.chrome = {
                app: {
                    isInstalled: false,
                    InstallState: { DISABLED: 'disabled', INSTALLED: 'installed', NOT_INSTALLED: 'not_installed' },
                    RunningState: { CANNOT_RUN: 'cannot_run', READY_TO_RUN: 'ready_to_run', RUNNING: 'running' }
                },
                runtime: {
                    OnInstalledReason: { CHROME_UPDATE: 'chrome_update', INSTALL: 'install', SHARED_MODULE_UPDATE: 'shared_module_update', UPDATE: 'update' },
                    OnRestartRequiredReason: { APP_UPDATE: 'app_update', GC_PRESSURE: 'gc_pressure', OS_UPDATE: 'os_update' },
                    PlatformArch: { ARM: 'arm', ARM64: 'arm64', MIPS: 'mips', MIPS64: 'mips64', X86_32: 'x86-32', X86_64: 'x86-64' },
                    PlatformNaclArch: { ARM: 'arm', MIPS: 'mips', MIPS64: 'mips64', X86_32: 'x86-32', X86_64: 'x86-64' },
                    PlatformOs: { ANDROID: 'android', CROS: 'cros', LINUX: 'linux', MAC: 'mac', OPENBSD: 'openbsd', WIN: 'win' },
                    RequestUpdateCheckStatus: { NO_UPDATE: 'no_update', THROTTLED: 'throttled', UPDATE_AVAILABLE: 'update_available' }
                }
            };
        }
    } catch(e) {}

    // 3. Patch navigator.permissions to avoid the headless detection fingerprint
    try {
        const originalQuery = window.navigator.permissions.query;
        window.navigator.permissions.__proto__.query = function(parameters) {
            if (parameters.name === 'notifications') {
                return Promise.resolve({ state: Notification.permission });
            }
            return originalQuery.call(this, parameters);
        };
    } catch(e) {}

    // 4. Add realistic browser plugins (headless Chromium has none)
    try {
        Object.defineProperty(navigator, 'plugins', {
            get: () => {
                const fakePlugins = [
                    { name: 'PDF Viewer',               filename: 'internal-pdf-viewer',               description: 'Portable Document Format', length: 1 },
                    { name: 'Chrome PDF Viewer',        filename: 'mhjfbmdgcfjbbpaeojofohoefgiehjai',   description: '',                         length: 1 },
                    { name: 'Chromium PDF Viewer',      filename: 'internal-pdf-viewer',               description: '',                         length: 1 },
                    { name: 'Microsoft Edge PDF Viewer',filename: 'msedge_pdf',                        description: '',                         length: 1 },
                    { name: 'WebKit built-in PDF',      filename: 'webkit_pdf',                        description: '',                         length: 1 },
                ];
                Object.setPrototypeOf(fakePlugins, PluginArray.prototype);
                return fakePlugins;
            },
        });
    } catch(e) {}

    // 5. Realistic language list (Spanish user accessing Spanish govt site)
    try {
        Object.defineProperty(navigator, 'languages', {
            get: () => ['es-ES', 'es', 'en-US', 'en'],
        });
    } catch(e) {}

    // 6. Realistic hardware concurrency and memory
    try {
        Object.defineProperty(navigator, 'hardwareConcurrency', { get: () => 8 });
    } catch(e) {}
    try {
        Object.defineProperty(navigator, 'deviceMemory', { get: () => 8 });
    } catch(e) {}

    // 7. Spoof vendor/renderer (blank in headless Chromium)
    try {
        const getParameter = WebGLRenderingContext.prototype.getParameter;
        WebGLRenderingContext.prototype.getParameter = function(parameter) {
            if (parameter === 37445) return 'Intel Inc.';
            if (parameter === 37446) return 'Intel Iris OpenGL Engine';
            return getParameter.call(this, parameter);
        };
    } catch(e) {}

    // 8. Prevent iframe contentWindow.navigator.webdriver exposure
    try {
        const origDescriptor = Object.getOwnPropertyDescriptor(HTMLIFrameElement.prototype, 'contentWindow');
        Object.defineProperty(HTMLIFrameElement.prototype, 'contentWindow', {
            get: function() {
                const win = origDescriptor.get.call(this);
                if (!win) return win;
                try {
                    Object.defineProperty(win.navigator, 'webdriver', { get: () => undefined });
                } catch(e) {}
                return win;
            }
        });
    } catch(e) {}
}
"""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _human_pause(page):
    min_delay = min(ACTION_DELAY_MIN_MS, ACTION_DELAY_MAX_MS)
    max_delay = max(ACTION_DELAY_MIN_MS, ACTION_DELAY_MAX_MS)
    page.wait_for_timeout(random.randint(min_delay, max_delay))


def _normalize_text(value):
    normalized = unicodedata.normalize("NFKD", value or "")
    normalized = "".join(ch for ch in normalized if not unicodedata.combining(ch))
    return " ".join(normalized.lower().split())


def _contains_any(text, markers):
    return any(_normalize_text(marker) in text for marker in markers)


def _has_any_selector(page, selectors):
    for selector in selectors:
        try:
            if page.locator(selector).count() > 0:
                return True
        except Exception:
            continue
    return False


def _wait_for_manual_allow(page):
    if HEADLESS or MANUAL_ALLOW_WAIT_SECONDS <= 0:
        return
    print(f"Checker: waiting {MANUAL_ALLOW_WAIT_SECONDS}s for manual browser allow popup")
    page.wait_for_timeout(MANUAL_ALLOW_WAIT_SECONDS * 1000)


def _log(run_tag, message):
    if run_tag:
        print(f"Checker[{run_tag}]: {message}")
        return
    print(f"Checker: {message}")


def _wait_for_navigation(page, reason="", run_tag=None):
    nav_ready = True

    try:
        page.wait_for_load_state("domcontentloaded", timeout=NAVIGATION_TIMEOUT_MS)
    except PlaywrightTimeoutError:
        nav_ready = False
        _log(
            run_tag,
            f"navigation wait timed out{' (' + reason + ')' if reason else ''}",
        )
    except Exception as exc:
        nav_ready = False
        _log(
            run_tag,
            f"navigation wait error{' (' + reason + ')' if reason else ''}: {exc}",
        )

    # networkidle can be noisy on dynamic pages, so treat timeout as informational only.
    try:
        page.wait_for_load_state("networkidle", timeout=min(15000, NAVIGATION_TIMEOUT_MS))
    except PlaywrightTimeoutError:
        pass
    except Exception:
        pass

    return nav_ready


def _wait_for_stage_ready(page, reason="", run_tag=None):
    deadline = time.time() + (STAGE_READY_TIMEOUT_MS / 1000)
    last_stage = "unknown"

    while time.time() < deadline:
        stage, _ = _detect_page_stage(page)
        last_stage = stage
        if stage != "unknown":
            return stage
        page.wait_for_timeout(STAGE_READY_POLL_MS)

    _log(run_tag, f"stage readiness timed out{' (' + reason + ')' if reason else ''}")
    return last_stage


def _detect_block_from_response(page, response):
    try:
        if response and response.status >= 400:
            return f"http {response.status}"
    except Exception:
        pass

    try:
        title_marker = _detect_block_marker(_normalize_text(page.title() or ""))
        if title_marker:
            return title_marker
    except Exception:
        pass

    try:
        return _detect_block_marker(_get_page_text(page))
    except Exception:
        return None


def _run_proxy_preflight(context, run_tag=None):
    if not PROXY_PREFLIGHT_ENABLED or not PROXY_PREFLIGHT_URLS:
        return True, "Proxy preflight disabled"

    failures = []
    preflight_page = None

    try:
        # Use real page navigation so proxy auth is exercised exactly like the
        # main ICP flow. context.request may produce false 407s with some proxies.
        preflight_page = context.new_page()
        preflight_page.set_default_timeout(PROXY_PREFLIGHT_TIMEOUT_MS)

        for url in PROXY_PREFLIGHT_URLS:
            try:
                response = preflight_page.goto(
                    url,
                    wait_until="domcontentloaded",
                    timeout=PROXY_PREFLIGHT_TIMEOUT_MS,
                )
            except PlaywrightTimeoutError:
                failures.append(f"{url}: timeout")
                continue
            except Exception as exc:
                if _looks_like_proxy_auth_failure(str(exc)):
                    failures.append(f"{url}: HTTP 407")
                else:
                    failures.append(f"{url}: {exc}")
                continue

            if response and response.ok:
                _log(run_tag, f"proxy preflight passed via {url} (status={response.status})")
                return True, f"Proxy preflight passed via {url}"

            if response is None:
                failures.append(f"{url}: no response")
                continue

            failures.append(f"{url}: HTTP {response.status}")
    finally:
        if preflight_page:
            try:
                preflight_page.close()
            except Exception:
                pass

    if any("HTTP 407" in failure for failure in failures):
        details = "; ".join(failures[:3]) if failures else "no endpoint reachable"
        return (
            False,
            (
                "Proxy authentication failed (HTTP 407) during preflight "
                f"({details}). Check Oxylabs username/password and account access"
            ),
        )

    details = "; ".join(failures[:3]) if failures else "no endpoint reachable"
    return False, f"Proxy preflight failed ({details})"


def _detect_final_menu_actions(page_text):
    detected_groups = []
    for group_name, phrases in FINAL_MENU_ACTION_GROUPS.items():
        if any(phrase in page_text for phrase in phrases):
            detected_groups.append(group_name)
    return detected_groups


def _get_page_text(page):
    """Return normalised body text, including <input type="button/submit"> values.

    The ICP portal renders its action buttons as <input type="button" value="...">
    elements.  Playwright's inner_text() only captures text *nodes*  -  it ignores
    the value attribute entirely, so stage detection based purely on inner_text()
    can never see "Solicitar Cita", "Anular Cita", etc.

    The JS snippet appends all button/submit input values to the body text so
    that every stage-detection keyword check works correctly.
    """
    dom_text = ""
    html_text = ""

    try:
        result = page.evaluate(
            """
            () => {
                const bodyText = document.body ? (document.body.innerText || '') : '';
                const inputValues = Array.from(
                    document.querySelectorAll(
                        'input[type="button"], input[type="submit"], input[type="reset"]'
                    )
                ).map(el => el.value || '').join(' ');
                return bodyText + ' ' + inputValues;
            }
            """
        )
        dom_text = _normalize_text(result or "")
    except Exception:
        pass

    if not dom_text:
        try:
            dom_text = _normalize_text(page.locator("body").inner_text())
        except Exception:
            pass

    # Extract from raw HTML too. On this portal, anti-bot scripts can cause
    # DOM text APIs to return only challenge script snippets.
    try:
        html = page.content() or ""

        # Preserve labels rendered via <input value="..."> (common on ICP pages).
        input_values = []
        for input_tag in re.findall(r"<input[^>]*>", html, flags=re.IGNORECASE):
            if not re.search(r"type\s*=\s*[\"']?(?:button|submit|reset)\b", input_tag, flags=re.IGNORECASE):
                continue
            match = re.search(r"value\s*=\s*[\"']([^\"']*)[\"']", input_tag, flags=re.IGNORECASE)
            if match:
                input_values.append(match.group(1))

        # Important: use [\s\S] (single-escaped) so script/style blocks are actually removed.
        html = re.sub(r"<script[\s\S]*?</script>", " ", html, flags=re.IGNORECASE)
        html = re.sub(r"<style[\s\S]*?</style>", " ", html, flags=re.IGNORECASE)
        text = re.sub(r"<[^>]+>", " ", html)
        html_text = _normalize_text(
            html_lib.unescape(text) + " " + " ".join(input_values)
        )
    except Exception:
        pass

    # Use whichever source is richer.
    if len(html_text) > len(dom_text):
        return html_text
    return dom_text


def _detect_block_marker(page_text):
    for marker in BLOCK_OR_CHALLENGE_MARKERS:
        if _normalize_text(marker) in page_text:
            return marker
    return None


def _looks_like_proxy_auth_failure(error_text):
    text = _normalize_text(error_text or "")
    markers = [
        "proxy authentication failed",
        "proxy authentication required",
        "http 407",
        "407 proxy authentication required",
        "tunnel connection failed",
        "err_http_response_code_failure",
    ]
    return any(marker in text for marker in markers)


# ---------------------------------------------------------------------------
# Stage detection
# NOTE: Order matters  -  more specific checks come first.
# sin_clave MUST be checked before identity_form because the sin_clave page
# mentions "NIE" in its informational text, which could falsely trigger the
# identity_form check if the order were reversed.
# ---------------------------------------------------------------------------

def _detect_page_stage(page):
    page_text = _get_page_text(page)
    page_url = page.url.lower()

    # 1. Blocked / challenged
    block_marker = _detect_block_marker(page_text)
    if block_marker:
        return "blocked", page_text

    # 2. Appointment result page  (URL: /acCitar or similar, or "paso X de Y")
    if "/accitar" in page_url or "paso 1 de" in page_text:
        return "request_result", page_text

    # 3. Final action menu (Solicitar Cita / Consultar / Anular / Salir)
    if _detect_final_menu_actions(page_text):
        return "final_menu", page_text

    # 3b. Final action menu URL fallback (common post-identity page)
    if "/acvalidarentrada" in page_url and (
        _has_any_selector(page, ["input#btnEnviar", "input#btnConsultar", "form[name='procedimientos']"])
        or "opciones de la cita" in page_text
    ):
        return "final_menu", page_text

    # 4. Office + procedure selector  -  try element first
    if _has_any_selector(
        page,
        [
            "select[name='tramiteGrupo[0]']",
            "select[id='tramiteGrupo[0]']",
            "select#tramiteGrupo\\[0\\]",
        ],
    ):
        return "office_and_procedure", page_text

    # 5. Presentación sin Cl@ve choice page  -  check BEFORE identity_form
    if (
        "presentacion sin cl@ve" in page_text
        or "presentacion sin clave" in page_text
        or "presentation without cl@ve" in page_text
        or "presentacion con cl@ve" in page_text
    ):
        return "sin_clave", page_text

    # 6. Identity / NIE entry form
    if (
        ("tipo de documento" in page_text or "document type" in page_text)
        and ("n.i.e" in page_text or "nie" in page_text)
        and ("pais de nacionalidad" in page_text or "country of nationality" in page_text)
    ):
        return "identity_form", page_text

    # 7. Office + procedure selector  -  text-based fallback
    if (
        "selecciona oficina" in page_text
        or "select office" in page_text
        or "selecciona tramite" in page_text
        or "select procedure" in page_text
        or "tramites policia nacional" in page_text
    ):
        return "office_and_procedure", page_text

    # 8. Office + procedure selector  -  URL-based fallback
    if "/icpplustieb/citar" in page_url and (
        "provincia seleccionada" in page_text
        or "cualquier oficina" in page_text
        or "tramites policia nacional" in page_text
    ):
        return "office_and_procedure", page_text

    # 9. Province selector
    if _has_any_selector(page, ["select[name='form']", "select#form"]):
        return "province", page_text

    # 10. Province selector (text fallback)
    if "provincias disponibles" in page_text:
        return "province", page_text

    # 11. Landing / entry page
    if (
        "acceder al procedimiento" in page_text
        or "acceder al tramite" in page_text
    ):
        return "entry", page_text

    # 12. Explicit no-appointment message on any other page
    if _contains_any(page_text, NO_APPOINTMENT_MARKERS):
        return "no_appointment", page_text

    return "unknown", page_text


# ---------------------------------------------------------------------------
# Form helpers  -  use type() with delay for human-like typing
# ---------------------------------------------------------------------------

def _human_type(locator, value, page):
    """
    Type text character-by-character with randomised per-keystroke delay.
    Falls back to plain .fill() if .type() is unavailable.
    """
    try:
        locator.focus()
        locator.fill("")          # clear first
        locator.type(value, delay=random.randint(60, 180))
    except Exception:
        locator.fill(value)
    _human_pause(page)


def _fill_first_available_input(page, selectors, value):
    for selector in selectors:
        locator = page.locator(selector)
        if locator.count() < 1:
            continue
        try:
            _human_type(locator.first, value, page)
            return True
        except Exception:
            continue
    return False


def _select_nationality(page):
    selectors = [
        "select[name='txtPaisNac']",
        "select[name*='pais']",
        "select[id*='pais']",
    ]
    for selector in selectors:
        locator = page.locator(selector)
        if locator.count() < 1:
            continue
        try:
            locator.first.select_option(label=NATIONALITY, timeout=PAGE_TIMEOUT_MS)
            return True
        except Exception:
            pass
        try:
            matched = locator.first.evaluate(
                """
                (select, target) => {
                    const norm = (v) => (v || '').toLowerCase().normalize('NFD').replace(/[\\u0300-\\u036f]/g, '').trim();
                    const t = norm(target);
                    const opt = Array.from(select.options).find(o => norm(o.textContent).includes(t) || norm(o.value).includes(t));
                    if (!opt) return false;
                    select.value = opt.value;
                    select.dispatchEvent(new Event('change', { bubbles: true }));
                    return true;
                }
                """,
                NATIONALITY,
            )
            if matched:
                return True
        except Exception:
            continue

    return _select_option_contains(page, NATIONALITY, preferred_hints=["pais", "nac", "nationality"])


def _fill_identity_form(page):
    if not _fill_first_available_input(
        page,
        [
            "input[name='txtIdCitado']",
            "input[name*='id'][name*='citad']",
            "input[name*='nie']",
            "input[placeholder*='NIE']",
        ],
        NIE,
    ):
        return False, "NIE input not found"

    if not _fill_first_available_input(
        page,
        [
            "input[name='txtDesCitado']",
            "input[name*='des'][name*='citad']",
            "input[name*='nom']",
            "input[placeholder*='Nombre']",
            "input[placeholder*='Name']",
        ],
        NAME,
    ):
        return False, "Name input not found"

    if not _select_nationality(page):
        return False, f"Nationality '{NATIONALITY}' not found in dropdown"
    _human_pause(page)

    return True, "Identity form filled"


# ---------------------------------------------------------------------------
# Selection helpers
# ---------------------------------------------------------------------------

def _select_option_contains(page, keyword, preferred_hints=None, include_hints=None, exclude_hints=None):
    lowered_keyword = keyword.lower()
    preferred_hints = [hint.lower() for hint in (preferred_hints or [])]
    include_hints = [hint.lower() for hint in (include_hints or [])]
    exclude_hints = [hint.lower() for hint in (exclude_hints or [])]
    script = """
        ({ keyword, preferredHints, includeHints, excludeHints }) => {
            const normalize = (value) => (value || '').toLowerCase().normalize('NFD').replace(/[\\u0300-\\u036f]/g, '').trim();
            const selects = Array.from(document.querySelectorAll('select'));
            const candidates = [];

            for (const select of selects) {
                const parent = select.closest('tr, div, td, p, section, form') || select.parentElement;
                const parentText = normalize(parent ? parent.textContent : '');
                const attrs = normalize(
                    [
                        select.name,
                        select.id,
                        select.className,
                        select.getAttribute('aria-label'),
                        select.getAttribute('title'),
                        parentText,
                    ].join(' ')
                );

                if (includeHints.length > 0 && !includeHints.some((hint) => attrs.includes(hint))) {
                    continue;
                }

                if (excludeHints.some((hint) => attrs.includes(hint))) {
                    continue;
                }

                const option = Array.from(select.options).find((item) =>
                    normalize(item.textContent).includes(keyword)
                );
                if (option) {
                    let score = 1;
                    if (preferredHints.some((hint) => attrs.includes(hint))) {
                        score += 5;
                    }
                    candidates.push({ select, optionValue: option.value, score });
                }
            }

            if (!candidates.length) {
                return false;
            }

            candidates.sort((a, b) => b.score - a.score);
            const best = candidates[0];
            best.select.value = best.optionValue;
            best.select.dispatchEvent(new Event('change', { bubbles: true }));
            return true;
        }
    """
    return page.evaluate(
        script,
        {
            "keyword": lowered_keyword,
            "preferredHints": preferred_hints,
            "includeHints": include_hints,
            "excludeHints": exclude_hints,
        },
    )


def _select_police_procedure(page):
    selectors = [
        "select[name='tramiteGrupo[0]']",
        "select[id='tramiteGrupo[0]']",
        "select#tramiteGrupo\\[0\\]",
        "select[name^='tramiteGrupo']",
    ]

    keyword = _normalize_text(TARGET_PROCEDURE_TEXT)

    for selector in selectors:
        locator = page.locator(selector)
        if locator.count() < 1:
            continue

        try:
            selected = locator.first.evaluate(
                """
                (select, keyword) => {
                    const normalize = (value) => (value || '')
                        .normalize('NFKD')
                        .replace(/[\\u0300-\\u036f]/g, '')
                        .toLowerCase()
                        .replace(/\\s+/g, ' ')
                        .trim();

                    const match = Array.from(select.options).find((option) =>
                        normalize(option.textContent).includes(keyword)
                    );

                    if (!match) {
                        return false;
                    }

                    select.value = match.value;
                    select.dispatchEvent(new Event('change', { bubbles: true }));
                    return true;
                }
                """,
                keyword,
            )
            if selected:
                return True
        except Exception:
            continue

    return _select_option_contains(
        page,
        keyword,
        preferred_hints=["tramite", "policia", "grupo"],
    )


# ---------------------------------------------------------------------------
# Click helpers
# ---------------------------------------------------------------------------

def _click_accept(page):
    accept_locators = [
        "button:has-text('Aceptar')",
        "button:has-text('Accept')",
        "input[type='submit'][value='Aceptar']",
        "input[type='button'][value='Aceptar']",
        "input[type='submit'][value='Accept']",
        "input[type='button'][value='Accept']",
    ]

    for selector in accept_locators:
        locator = page.locator(selector)
        if locator.count() > 0:
            locator.first.click(timeout=ACTION_TIMEOUT_MS)
            return True

    text_candidates = ["Aceptar", "ACEPTAR", "Accept", "ACCEPT"]
    for text in text_candidates:
        locator = page.get_by_text(text, exact=True)
        if locator.count() > 0:
            locator.first.click(timeout=ACTION_TIMEOUT_MS)
            return True

    return False


def _click_sin_clave(page):
    candidates = [
        "Presentación sin Cl@ve",
        "Presentacion sin Cl@ve",
        "Presentación sin Clave",
        "Presentacion sin Clave",
        "Presentation without Cl@ve",
    ]

    for text in candidates:
        for role in ("button", "link"):
            locator = page.get_by_role(role, name=re.compile(re.escape(text), re.IGNORECASE))
            if locator.count() > 0:
                locator.first.click(timeout=ACTION_TIMEOUT_MS)
                return True

    for text in candidates:
        locator = page.get_by_text(text, exact=False)
        if locator.count() > 0:
            locator.first.click(timeout=ACTION_TIMEOUT_MS)
            return True

    clicked = page.evaluate(
        """
        () => {
            const norm = (v) => (v || '').toLowerCase().normalize('NFD').replace(/[\\u0300-\\u036f]/g, '').replace(/\\s+/g, ' ').trim();
            const needles = [
                'presentacion sin cl@ve',
                'presentacion sin clave',
                'presentation without cl@ve',
                'sin cl@ve',
                'sin clave',
            ];
            const tags = ['a', 'button', 'div', 'span', 'li', 'p',
                          'input[type="button"]', 'input[type="submit"]'];
            const nodes = Array.from(document.querySelectorAll(tags.join(',')));
            let best = null;
            let bestScore = 0;
            for (const node of nodes) {
                const text = norm(node.textContent || node.value || '');
                for (let i = 0; i < needles.length; i++) {
                    if (text.includes(needles[i])) {
                        const score = needles[i].length;
                        if (score > bestScore) {
                            best = node;
                            bestScore = score;
                        }
                        break;
                    }
                }
            }
            if (!best) return false;
            best.click();
            return true;
        }
        """
    )
    return bool(clicked)


def _click_request_appointment(page):
    request_patterns = [
        re.compile(r"solicitar\s+cita", re.IGNORECASE),
        re.compile(r"request\s+appointment", re.IGNORECASE),
    ]

    for pattern in request_patterns:
        for role in ("button", "link"):
            locator = page.get_by_role(role, name=pattern)
            if locator.count() > 0:
                locator.first.click(timeout=ACTION_TIMEOUT_MS)
                return True

    selector_candidates = [
        "button:has-text('Solicitar Cita')",
        "a:has-text('Solicitar Cita')",
        "input[type='submit'][value*='Solicitar Cita']",
        "input[type='button'][value*='Solicitar Cita']",   # ICP uses <input type="button">
        "input#btnEnviar",                                  # stable element ID on the final menu
        "text=/Request\\s+Appointment/i",
    ]
    for selector in selector_candidates:
        locator = page.locator(selector)
        if locator.count() > 0:
            locator.first.click(timeout=ACTION_TIMEOUT_MS)
            return True

    return False


def _click_access_procedure(page):
    access_name_patterns = [
        re.compile(r"acceder\s+al\s+proced", re.IGNORECASE),
        re.compile(r"acceder\s+al\s+tr[aá]mite", re.IGNORECASE),
        re.compile(r"acceder", re.IGNORECASE),
    ]

    for pattern in access_name_patterns:
        for role in ("link", "button"):
            locator = page.get_by_role(role, name=pattern)
            if locator.count() > 0:
                try:
                    locator.first.click(timeout=ACTION_TIMEOUT_MS)
                    return True
                except PlaywrightTimeoutError:
                    continue

    selector_candidates = [
        "text=/Acceder\\s+al\\s+Proced/i",
        "text=/Acceder\\s+al\\s+Tr[aá]mite/i",
        "a:has-text('Acceder')",
        "button:has-text('Acceder')",
    ]
    for selector in selector_candidates:
        locator = page.locator(selector)
        if locator.count() > 0:
            try:
                locator.first.click(timeout=ACTION_TIMEOUT_MS)
                return True
            except PlaywrightTimeoutError:
                continue

    clicked = page.evaluate(
        """
        () => {
            const norm = (v) => (v || '').toLowerCase().normalize('NFD').replace(/[\\u0300-\\u036f]/g, '');
            const nodes = Array.from(document.querySelectorAll('a, button, input[type="button"], input[type="submit"]'));
            const target = nodes.find((node) => {
                const text = norm(node.textContent || node.value || '');
                return text.includes('acceder') && (text.includes('proced') || text.includes('tramite'));
            });
            if (!target) return false;
            target.click();
            return true;
        }
        """
    )
    return bool(clicked)


# ---------------------------------------------------------------------------
# Proxy parsing  -  FIX: normalise scheme to http:// (Oxylabs requires http)
# ---------------------------------------------------------------------------

def _parse_proxy(proxy_value):
    if not proxy_value:
        return None

    proxy_value = proxy_value.strip()
    parsed = urlparse(proxy_value)
    if not parsed.scheme:
        parsed = urlparse(f"http://{proxy_value}")

    if not parsed.hostname or not parsed.port:
        raise ValueError("Invalid proxy format. Expected: http://user:pass@host:port")

    # FIX: Oxylabs (and virtually all CONNECT proxies) require http://, not https://.
    # Using https:// as the proxy scheme causes the connection to fail silently or
    # raise a TLS handshake error, making the bot appear to run without a proxy
    # and getting immediately rate-limited / blocked by the government site.
    scheme = "http"  # always force http for proxy transport layer

    proxy = {"server": f"{scheme}://{parsed.hostname}:{parsed.port}"}
    if parsed.username:
        proxy["username"] = unquote(parsed.username)
    if parsed.password:
        proxy["password"] = unquote(parsed.password)
    return proxy


# ---------------------------------------------------------------------------
# Debug artifacts
# ---------------------------------------------------------------------------

def _save_debug_artifacts(page, reason):
    if not SAVE_DEBUG_ARTIFACTS:
        return

    try:
        artifacts_dir = Path(DEBUG_ARTIFACTS_DIR)
        artifacts_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        safe_reason = "".join(ch if ch.isalnum() else "_" for ch in reason).strip("_") or "debug"
        screenshot_path = artifacts_dir / f"{timestamp}_{safe_reason}.png"
        html_path = artifacts_dir / f"{timestamp}_{safe_reason}.html"
        page.screenshot(path=str(screenshot_path), full_page=True)
        html_path.write_text(page.content(), encoding="utf-8")
        print(f"Checker: debug artifacts saved -> {screenshot_path} | {html_path}")
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def check_appointments(proxy_value=None):
    """Navigate the ICP appointment site and determine whether slots are available.

    Returns:
        (True,  reason_str)   -  appointment slots detected
        (False, reason_str)   -  no slots, or an error occurred
    """
    run_tag = datetime.now().strftime("%H%M%S%f")[-10:]

    with sync_playwright() as p:
        browser = None
        context = None
        proxy = None

        try:
            proxy = _parse_proxy(proxy_value) if proxy_value else None

            # ── Stealth browser launch ─────────────────────────────────────
            # --disable-blink-features=AutomationControlled removes the
            # "Chrome is being controlled by automated software" banner and,
            # more importantly, the associated JS flags that bot-detectors check.
            browser = p.chromium.launch(
                headless=HEADLESS,
                proxy=proxy,
                slow_mo=random.randint(40, 120),
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-infobars",
                    "--disable-extensions",
                    "--no-first-run",
                    "--disable-default-apps",
                    "--disable-component-update",
                ],
            )

            user_agent = random.choice(_USER_AGENTS)
            viewport_w = random.randint(1280, 1440)
            viewport_h = random.randint(800, 960)

            context = browser.new_context(
                locale="es-ES",
                timezone_id="Europe/Madrid",
                user_agent=user_agent,
                viewport={"width": viewport_w, "height": viewport_h},
                # Tell the server we accept Spanish content first
                extra_http_headers={
                    "Accept-Language": "es-ES,es;q=0.9,en-US;q=0.8,en;q=0.7",
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
                    "DNT": "1",
                },
            )

            # ── Inject stealth JS into every new page before any scripts run ──
            context.add_init_script(_STEALTH_INIT_SCRIPT)

            if proxy:
                preflight_ok, preflight_reason = _run_proxy_preflight(context, run_tag=run_tag)
                if not preflight_ok:
                    return False, preflight_reason

            page = context.new_page()
            page.set_default_timeout(PAGE_TIMEOUT_MS)

            _log(run_tag, f"launching with UA={user_agent[:60]}... proxy={'yes' if proxy else 'no'}")

            initial_response = page.goto(
                URL,
                wait_until="domcontentloaded",
                timeout=NAVIGATION_TIMEOUT_MS,
            )
            _wait_for_navigation(page, "initial landing", run_tag=run_tag)
            _human_pause(page)
            _wait_for_manual_allow(page)

            initial_marker = _detect_block_from_response(page, initial_response)
            if initial_marker:
                _save_debug_artifacts(page, "initial_block_or_challenge")
                return False, f"Blocked or challenged on initial landing ({initial_marker})"

            _wait_for_stage_ready(page, "initial stage readiness", run_tag=run_tag)
            _human_pause(page)

            max_stage_transitions = 12
            max_unknown_stage_retries = UNKNOWN_STAGE_MAX_RETRIES
            unknown_stage_retries = 0
            unknown_stage_total_wait_ms = 0
            unknown_stage_soft_reload_done = False

            for transition in range(max_stage_transitions):
                stage, page_text = _detect_page_stage(page)
                _log(
                    run_tag,
                    (
                        f"transition={transition + 1}/{max_stage_transitions} "
                        f"stage={stage} | url={page.url}"
                    ),
                )

                if stage != "unknown":
                    unknown_stage_retries = 0
                    unknown_stage_total_wait_ms = 0
                    unknown_stage_soft_reload_done = False

                # ── Blocked ────────────────────────────────────────────────
                if stage == "blocked":
                    marker = _detect_block_marker(page_text) or "unknown"
                    _save_debug_artifacts(page, "blocked_or_challenged")
                    return False, f"Blocked or challenged before flow completion ({marker})"

                # ── Appointment result page ────────────────────────────────
                if stage == "request_result":
                    marker = _detect_block_marker(page_text)
                    if marker:
                        _save_debug_artifacts(page, "request_result_blocked_or_challenged")
                        return False, f"Blocked or challenged on appointment result page ({marker})"

                    if _contains_any(page_text, NO_APPOINTMENT_MARKERS):
                        if _contains_any(page_text, CLAVE_ONLY_MARKERS):
                            return (
                                True,
                                "Appointments available via Cl@ve only "
                                "(not available without Cl@ve  -  log in with Cl@ve to book)",
                            )
                        return False, "No appointments currently available"

                    if _contains_any(page_text, AVAILABILITY_HINTS):
                        return True, "Appointment slots detected  -  book now!"

                    _save_debug_artifacts(page, "request_result_unknown")
                    return False, "Reached appointment page but availability state is unclear"

                # ── Final action menu ──────────────────────────────────────
                if stage == "final_menu":
                    # Extended pause before final request to avoid WAF detection
                    extended_pause_ms = random.randint(3000, 6000)
                    page.wait_for_timeout(extended_pause_ms)

                    if not _click_request_appointment(page):
                        _save_debug_artifacts(page, "request_appointment_button_missing")
                        return False, "Could not click 'Solicitar Cita' on final menu"

                    # Wait longer for response after submission to avoid timeouts
                    _wait_for_navigation(page, "after solicitar cita", run_tag=run_tag)
                    page.wait_for_timeout(2000)  # Extra buffer after navigation
                    _wait_for_stage_ready(page, "after solicitar cita", run_tag=run_tag)
                    _human_pause(page)
                    continue

                # ── Entry / landing page ───────────────────────────────────
                if stage == "entry":
                    if not _click_access_procedure(page):
                        _save_debug_artifacts(page, "entry_not_found")
                        return False, "Could not find landing entry button ('Acceder al Procedimiento')"
                    _wait_for_navigation(page, "after entry click", run_tag=run_tag)
                    _wait_for_stage_ready(page, "after entry click", run_tag=run_tag)
                    _human_pause(page)
                    continue

                # ── Province selector ──────────────────────────────────────
                if stage == "province":
                    if not _select_option_contains(page, TARGET_PROVINCE, preferred_hints=["prov", "sede"]):
                        _save_debug_artifacts(page, "province_not_found")
                        return False, f"Province '{TARGET_PROVINCE}' not found in dropdown"
                    _human_pause(page)

                    if not _click_accept(page):
                        _save_debug_artifacts(page, "province_accept_missing")
                        return False, "Accept button not found on province page"

                    _wait_for_navigation(page, "after province accept", run_tag=run_tag)
                    _wait_for_stage_ready(page, "after province accept", run_tag=run_tag)
                    _human_pause(page)
                    continue

                # ── Office + procedure selector ────────────────────────────
                if stage == "office_and_procedure":
                    if not _select_police_procedure(page):
                        _save_debug_artifacts(page, "procedure_not_found")
                        return False, f"Procedure containing '{TARGET_PROCEDURE_TEXT}' not found"
                    _log(run_tag, "procedure selected on TRAMITES POLICIA NACIONAL")
                    _human_pause(page)

                    if not _click_accept(page):
                        _save_debug_artifacts(page, "procedure_accept_missing")
                        return False, "Accept button not found on office/procedure page"

                    _wait_for_navigation(page, "after office/procedure accept", run_tag=run_tag)
                    _wait_for_stage_ready(page, "after office/procedure accept", run_tag=run_tag)
                    _human_pause(page)
                    continue

                # ── Presentación sin Cl@ve choice ──────────────────────────
                if stage == "sin_clave":
                    if not _click_sin_clave(page):
                        _save_debug_artifacts(page, "sin_clave_not_found")
                        return False, "'Presentación sin Cl@ve' option not found"

                    _wait_for_navigation(page, "after sin_clave click", run_tag=run_tag)
                    _wait_for_stage_ready(page, "after sin_clave click", run_tag=run_tag)
                    _human_pause(page)
                    continue

                # ── NIE / identity form ────────────────────────────────────
                if stage == "identity_form":
                    filled, fill_reason = _fill_identity_form(page)
                    if not filled:
                        _save_debug_artifacts(page, "identity_form_fill_failed")
                        return False, fill_reason

                    if not _click_accept(page):
                        _save_debug_artifacts(page, "identity_accept_missing")
                        return False, "Accept button not found on identity form"

                    _wait_for_navigation(page, "after identity form accept", run_tag=run_tag)
                    _wait_for_stage_ready(page, "after identity form accept", run_tag=run_tag)
                    _human_pause(page)
                    continue

                # ── Explicit no-appointment page ───────────────────────────
                if stage == "no_appointment":
                    return False, "No appointments currently available"

                # ── Generic checks before declaring unknown ────────────────
                if _contains_any(page_text, NO_APPOINTMENT_MARKERS):
                    return False, "No appointments currently available"

                if _contains_any(page_text, AVAILABILITY_HINTS):
                    return True, "Appointment slots detected"

                if (
                    stage == "unknown"
                    and unknown_stage_retries < max_unknown_stage_retries
                    and unknown_stage_total_wait_ms < UNKNOWN_STAGE_MAX_TOTAL_MS
                ):
                    unknown_stage_retries += 1

                    if UNKNOWN_STAGE_EXP_BACKOFF:
                        retry_wait_ms = min(
                            UNKNOWN_STAGE_RETRY_BASE_MS * (2 ** (unknown_stage_retries - 1)),
                            UNKNOWN_STAGE_RETRY_MAX_MS,
                        )
                    else:
                        retry_wait_ms = min(
                            UNKNOWN_STAGE_RETRY_BASE_MS
                            + (unknown_stage_retries - 1) * UNKNOWN_STAGE_RETRY_STEP_MS,
                            UNKNOWN_STAGE_RETRY_MAX_MS,
                        )

                    remaining_budget_ms = max(0, UNKNOWN_STAGE_MAX_TOTAL_MS - unknown_stage_total_wait_ms)
                    if remaining_budget_ms <= 0:
                        break

                    retry_wait_ms = min(retry_wait_ms, remaining_budget_ms)

                    _log(
                        run_tag,
                        (
                            f"stage unknown retry {unknown_stage_retries}/{max_unknown_stage_retries} "
                            f"after {retry_wait_ms}ms "
                            f"(budget left {remaining_budget_ms}ms)"
                        ),
                    )
                    _wait_for_navigation(
                        page,
                        f"unknown-stage retry {unknown_stage_retries}",
                        run_tag=run_tag,
                    )
                    page.wait_for_timeout(retry_wait_ms)
                    unknown_stage_total_wait_ms += retry_wait_ms
                    continue

                if stage == "unknown" and not unknown_stage_soft_reload_done:
                    unknown_stage_soft_reload_done = True
                    _log(run_tag, "stage remained unknown  -  performing one soft reload")
                    try:
                        reload_response = page.reload(
                            wait_until="domcontentloaded",
                            timeout=NAVIGATION_TIMEOUT_MS,
                        )
                    except PlaywrightTimeoutError:
                        reload_response = None

                    _wait_for_navigation(page, "after unknown-stage soft reload", run_tag=run_tag)
                    reload_marker = _detect_block_from_response(page, reload_response)
                    if reload_marker:
                        _save_debug_artifacts(page, "unknown_stage_soft_reload_blocked")
                        return False, f"Blocked or challenged after soft reload ({reload_marker})"

                    _wait_for_stage_ready(page, "after unknown-stage soft reload", run_tag=run_tag)
                    continue

                _save_debug_artifacts(page, "stage_detection_failed")
                return (
                    False,
                    (
                        f"Unknown page stage reached ({page.url}) after {unknown_stage_retries} retries "
                        f"and {unknown_stage_total_wait_ms}ms unknown-stage wait"
                    ),
                )

            # Loop exhausted without reaching a terminal state
            page_text = _get_page_text(page)
            if _contains_any(page_text, NO_APPOINTMENT_MARKERS):
                if _contains_any(page_text, CLAVE_ONLY_MARKERS):
                    return (
                        True,
                        "Appointments available via Cl@ve only "
                        "(not available without Cl@ve  -  log in with Cl@ve to book)",
                    )
                return False, "No appointments currently available"

            actions = _detect_final_menu_actions(page_text)
            if actions:
                return False, f"Stuck at final menu  -  request result not resolved ({', '.join(actions)})"

            if _contains_any(page_text, AVAILABILITY_HINTS):
                return True, "Appointment slots detected"

            _save_debug_artifacts(page, "max_stage_transitions_exceeded")
            return False, "Flow did not reach a terminal state within the allowed transitions"

        except PlaywrightTimeoutError as exc:
            if context and context.pages:
                try:
                    page = context.pages[-1]
                    page_text = _get_page_text(page)
                    marker = _detect_block_marker(page_text)
                    _save_debug_artifacts(page, "timeout")
                    if marker:
                        return False, f"Timeout under challenge/block page ({marker})"
                except Exception:
                    pass
            return False, f"Timeout: {exc}"

        except Exception as exc:
            if proxy and _looks_like_proxy_auth_failure(str(exc)):
                return (
                    False,
                    "Proxy authentication failed (HTTP 407) while opening target site. "
                    "Check Oxylabs username/password and account access",
                )
            if context and context.pages:
                try:
                    _save_debug_artifacts(context.pages[-1], "exception")
                except Exception:
                    pass
            return False, f"Check failed: {exc}"

        finally:
            if context:
                context.close()
            if browser:
                browser.close()