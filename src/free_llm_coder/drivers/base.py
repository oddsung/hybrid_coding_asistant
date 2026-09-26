from abc import ABC, abstractmethod
from pathlib import Path
from playwright.sync_api import Playwright, BrowserContext, Page
import re
import sys
import time
from typing import Optional

# URL fragments that mean the service bounced us to a login page.
_LOGIN_URL_RE = re.compile(r"(sign[-_]?in|/login|/auth\b|accounts\.google)", re.IGNORECASE)

# Menu entries once a model/mode picker is open. Services can override via
# selectors.model_item / selectors.mode_item.
_DEFAULT_PICKER_ITEM_SELECTOR = "[role='option'], [role='menuitem'], [role='menuitemradio']"

# Collect visible picker entries: first text line is the option's name;
# aria-selected/aria-checked or a "selected" class marks the current one.
_PICKER_ITEMS_JS = """
    (selector) => {
        const rows = [];
        document.querySelectorAll(selector).forEach(el => {
            const r = el.getBoundingClientRect();
            if (r.width === 0 || r.height === 0) return;
            const name = (el.innerText || '').split('\\n')[0].trim();
            if (!name) return;
            const cls = (el.className && el.className.toString ? el.className.toString() : '');
            const selected = el.getAttribute('aria-selected') === 'true' ||
                             el.getAttribute('aria-checked') === 'true' ||
                             /(^|[ _-])selected([ _-]|$)/.test(cls);
            rows.push({name, selected});
        });
        return rows;
    }
"""

# Click the entry whose name matches (exact first, then substring).
_PICKER_CLICK_JS = """
    ([selector, wanted]) => {
        const norm = s => s.toLowerCase().replace(/\\s+/g, ' ').trim();
        const w = norm(wanted);
        const els = [...document.querySelectorAll(selector)].filter(el => {
            const r = el.getBoundingClientRect();
            return r.width > 0 && r.height > 0 && (el.innerText || '').trim();
        });
        const nameOf = el => (el.innerText || '').split('\\n')[0].trim();
        let hit = els.find(el => norm(nameOf(el)) === w) ||
                  els.find(el => norm(nameOf(el)).includes(w));
        if (!hit) return null;
        const name = nameOf(hit);
        hit.click();
        return name;
    }
"""

from ..config_schema import COMMON_LIMIT_KEYWORDS, COMMON_CHALLENGE_KEYWORDS
from ..logging_setup import get_logger

log = get_logger("driver")

# Shared DOM-to-text extraction used by drivers whose responses are plain
# rich-text (no special code-block widget). It walks the last response
# element, normalizes non-breaking spaces, turns <br>/block elements into
# newlines, and drops UI-only buttons/icons. Drivers with a custom code
# editor (e.g. Qwen's Monaco) keep their own extractor instead.
GENERIC_CLEAN_TEXT_JS = """
    (selector) => {
        const responses = document.querySelectorAll(selector);
        if (responses.length === 0) return "";
        const el = responses[responses.length - 1];

        function getCleanText(node) {
            if (node.nodeType === 3) {
                return node.textContent.replace(/\\u00A0/g, ' ');
            }
            if (node.nodeType !== 1) return "";

            // Skip UI-only buttons / icon buttons that pollute the text.
            if (node.tagName === 'BUTTON' ||
                (node.classList && node.classList.contains('ds-icon-button'))) {
                return "";
            }
            if (node.tagName === 'BR') return '\\n';

            let text = "";
            for (let child of node.childNodes) {
                text += getCleanText(child);
            }

            const style = window.getComputedStyle(node);
            if (style.display === 'block' || style.display === 'flex' ||
                node.tagName === 'P' || node.tagName === 'DIV' || node.tagName === 'PRE') {
                if (text && !text.endsWith('\\n')) text += '\\n';
            }
            return text;
        }

        return getCleanText(el).trim();
    }
"""


class BaseDriver(ABC):
    def __init__(self, service_config: dict, user_data_dir: str, headless: bool = False):
        self.config = service_config
        self.user_data_dir = user_data_dir
        self.headless = headless
        self.playwright: Optional[Playwright] = None
        self.browser: Optional[BrowserContext] = None
        self.page: Optional[Page] = None
        # Number of response containers present right before the last message was sent.
        # Used to detect when a brand-new response (this turn's) has appeared.
        self._response_count_before = 0
        # Last model applied via select_model (None = the service's default).
        self.current_model: Optional[str] = None

    def start_browser(self, playwright: Playwright):
        self.playwright = playwright
        launch_kwargs = dict(
            user_data_dir=self.user_data_dir,
            headless=self.headless,
            channel="chrome",  # Try to use installed Chrome if available
            args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-blink-features=AutomationControlled",
            ],
            ignore_default_args=["--enable-automation"],
        )
        # Use persistent context to save login session
        self.browser = self.playwright.chromium.launch_persistent_context(**launch_kwargs)
        self._adopt_page()

        # Headless Chrome announces itself in the user agent
        # ("HeadlessChrome/..."), which bot checks reject immediately.
        # Relaunch once with the masked UA -- keeping the real version string.
        if self.headless:
            try:
                ua = self.page.evaluate("navigator.userAgent") or ""
            except Exception:
                ua = ""
            if "HeadlessChrome" in ua:
                log.info("[%s] masking HeadlessChrome user agent",
                         self.config.get('name', '?'))
                self.browser.close()
                launch_kwargs["user_agent"] = ua.replace("HeadlessChrome", "Chrome")
                self.browser = self.playwright.chromium.launch_persistent_context(**launch_kwargs)
                self._adopt_page()

    def _adopt_page(self):
        if self.browser.pages:
            self.page = self.browser.pages[0]
        else:
            self.page = self.browser.new_page()
        # stealth bypass for webdriver detection
        self.page.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")

    def save_debug_snapshot(self, directory) -> Optional[str]:
        """Screenshot the current page for post-mortem debugging.

        Invaluable in headless mode, where a login wall or bot challenge is
        otherwise invisible. Returns the saved path, or None on failure.
        """
        try:
            Path(directory).mkdir(parents=True, exist_ok=True)
            name = self.config.get('name', 'service')
            path = Path(directory) / f"{name}-{time.strftime('%Y%m%d-%H%M%S')}.png"
            self.page.screenshot(path=str(path))
            log.info("[%s] snapshot saved to %s (url=%s, title=%s)",
                     name, path, self.page.url, self.page.title())
            return str(path)
        except Exception as e:
            log.debug("snapshot failed: %s", e)
            return None

    def navigate(self):
        if not self.page:
            if self.playwright:
                self.start_browser(self.playwright)
            else:
                raise RuntimeError("Browser not started; call start_browser() first.")
        self.page.goto(self.config['url'])
        try:
            self.page.wait_for_load_state("networkidle", timeout=10000)
        except:
            pass # Ignore networkidle timeout and proceed if page is somewhat loaded
        self._dismiss_banners()

    # ------------------------------------------------------------------ #
    # Model / mode pickers (config-driven, live -- nothing is hardcoded)
    #
    # A service opts in by configuring selectors.model_menu (button that
    # opens the picker) and optionally selectors.model_item (menu entries;
    # a role-based default covers most UIs). Same for mode_menu/mode_item.
    # The option list is read from the live page every time, so new models
    # appear and retired ones disappear without any code or config change.
    # ------------------------------------------------------------------ #
    def supports_picker(self, kind: str = "model") -> bool:
        return bool((self.config.get('selectors') or {}).get(f'{kind}_menu'))

    def _picker_selectors(self, kind: str):
        selectors = self.config.get('selectors') or {}
        return (selectors.get(f'{kind}_menu'),
                selectors.get(f'{kind}_item') or _DEFAULT_PICKER_ITEM_SELECTOR)

    def _open_picker(self, menu_selector: str) -> bool:
        self._dismiss_banners()
        try:
            self.page.click(menu_selector, timeout=5000)
            time.sleep(1.0)  # let the menu render
            return True
        except Exception as e:
            log.warning("[%s] could not open picker '%s': %s",
                        self.config.get('name', '?'), menu_selector, e)
            return False

    def _close_picker(self):
        try:
            self.page.keyboard.press("Escape")
            time.sleep(0.3)
        except Exception:
            pass

    def list_picker(self, kind: str = "model") -> list:
        """Live option list: ``[{'name': str, 'selected': bool}, ...]``.

        Empty when the service has no ``{kind}_menu`` selector configured or
        the menu could not be read.
        """
        menu_sel, item_sel = self._picker_selectors(kind)
        if not menu_sel:
            return []
        if not self._open_picker(menu_sel):
            return []
        try:
            items = self.page.evaluate(_PICKER_ITEMS_JS, item_sel) or []
        except Exception:
            items = []
        self._close_picker()
        return items

    def select_picker_item(self, name: str, kind: str = "model") -> Optional[str]:
        """Pick the option matching ``name`` (exact, then substring).

        Returns the actual option name that was clicked, or None.
        """
        menu_sel, item_sel = self._picker_selectors(kind)
        if not menu_sel or not self._open_picker(menu_sel):
            return None
        try:
            clicked = self.page.evaluate(_PICKER_CLICK_JS, [item_sel, name])
        except Exception:
            clicked = None
        if clicked:
            time.sleep(0.8)  # let the UI apply the change
            log.info("[%s] selected %s '%s'", self.config.get('name', '?'), kind, clicked)
            if kind == "model":
                self.current_model = clicked
        else:
            self._close_picker()
            log.warning("[%s] no %s option matching '%s'",
                        self.config.get('name', '?'), kind, name)
        return clicked

    def list_models(self) -> list:
        items = self.list_picker("model")
        # Remember the currently-selected model so UIs can display it.
        current = next((it['name'] for it in items if it.get('selected')), None)
        if current:
            self.current_model = current
        return items

    def select_model(self, name: str) -> Optional[str]:
        return self.select_picker_item(name, "model")

    def list_modes(self) -> list:
        return self.list_picker("mode")

    def select_mode(self, name: str) -> Optional[str]:
        return self.select_picker_item(name, "mode")

    def login_required(self) -> bool:
        """True when the page was redirected to a login/sign-in URL."""
        try:
            return bool(_LOGIN_URL_RE.search(self.page.url or ""))
        except Exception:
            return False

    def _dismiss_banners(self):
        """Click away overlays named in ``dismiss_selectors`` (cookie consent
        banners etc.) -- they can intercept clicks page-wide."""
        for sel in (self.config.get('dismiss_selectors') or []):
            try:
                if self.page.query_selector(sel):
                    self.page.click(sel, timeout=2000)
                    log.debug("[%s] dismissed overlay via '%s'",
                              self.config.get('name', '?'), sel)
                    time.sleep(0.3)
            except Exception:
                pass

    def close(self):
        if self.browser:
            self.browser.close()
        # Do NOT stop playwright here, as it might be shared

    def _read_input_text(self, selector: str) -> str:
        """Current text of the input element (works for textarea and
        contenteditable alike)."""
        try:
            return self.page.evaluate(
                """(sel) => {
                    const el = document.querySelector(sel);
                    if (!el) return '';
                    return el.value !== undefined ? el.value : (el.innerText || '');
                }""",
                selector,
            ) or ""
        except Exception:
            return ""

    def type_message(self, input_selector: str, message: str):
        """Put ``message`` into the input reliably, replacing any content.

        Uses ``keyboard.insert_text`` -- the browser's real text-input path,
        same as a paste -- because rich editors (ProseMirror on chatgpt.com,
        Quill on gemini, Lexical, ...) can drop or mangle composed characters
        (Hangul, CJK) when text is set via ``page.fill``. The result is
        verified against the intended message and ``fill`` is used as a
        last-resort fallback if the insert visibly lost content.
        """
        select_all = "Meta+a" if sys.platform == "darwin" else "Control+a"
        self._dismiss_banners()
        try:
            self.page.click(input_selector, timeout=5000)
        except Exception:
            # An overlay may intercept pointer events; JS focus bypasses
            # hit-testing entirely.
            log.debug("[%s] input click intercepted; focusing via JS",
                      self.config.get('name', '?'))
            self.page.evaluate(
                "(sel) => { const el = document.querySelector(sel); if (el) el.focus(); }",
                input_selector,
            )
        self.page.keyboard.press(select_all)
        self.page.keyboard.insert_text(message)

        # Verify: editors normalize whitespace, so compare loosely by length.
        got = self._read_input_text(input_selector)
        if len(got) >= 0.9 * len(message):
            return
        log.warning("[%s] insert_text lost content (%d of %d chars); retrying with fill",
                    self.config.get('name', '?'), len(got), len(message))
        try:
            self.page.fill(input_selector, message)
        except Exception as e:
            log.warning("[%s] fill fallback failed: %s", self.config.get('name', '?'), e)

    def new_chat(self):
        """Start a fresh conversation on this service.

        Clicks the configured ``selectors.new_chat_button`` if present,
        otherwise just reloads the service URL.
        """
        selector = (self.config.get('selectors', {}) or {}).get('new_chat_button')
        if selector:
            try:
                btn = self.page.query_selector(selector)
                if btn:
                    btn.click()
                    time.sleep(1)
                    self._response_count_before = 0
                    return
            except Exception:
                pass
        # Fallback: reload the base URL for a clean conversation.
        self.navigate()
        self._response_count_before = 0

    @abstractmethod
    def send_message(self, message: str):
        """Send a message to the chat interface."""
        pass

    @abstractmethod
    def get_last_response(self) -> str:
        """Retrieve the last response from the chat interface."""
        pass

    def _page_text_excluding_responses(self) -> str:
        """Visible page text with the assistant response area removed.

        The LLM's own answer may legitimately mention phrases like "rate
        limit"; dropping the response area before keyword-scanning avoids
        treating that as a real usage limit.
        """
        selector = self._response_count_selector()
        try:
            return self.page.evaluate(
                """
                (selector) => {
                    const clone = document.body.cloneNode(true);
                    clone.querySelectorAll(selector).forEach(el => el.remove());
                    return clone.innerText || "";
                }
                """,
                selector,
            ) or ""
        except Exception:
            return ""

    def detect_limit(self) -> Optional[str]:
        """Detect a usage/rate limit and report how strong the evidence is.

        Returns one of:

        - ``"selector"``: an explicit error/limit element matched -- strong.
        - ``"service_keyword"``: a keyword curated for THIS service in
          ``limit_indicators.keywords`` matched -- strong.
        - ``"common_keyword"``: only a generic phrase from
          :data:`COMMON_LIMIT_KEYWORDS` matched -- weak. Pages legitimately
          contain such phrases (search-result citations, help links), so
          callers should only act on this when the response also failed.
        - ``None``: no limit evidence.
        """
        selectors = self.config.get('selectors', {}) or {}
        indicators = self.config.get('limit_indicators', {}) or {}

        # 1. Explicit error / limit selectors.
        candidate_selectors = []
        if selectors.get('error_message'):
            candidate_selectors.append(selectors['error_message'])
        candidate_selectors.extend(indicators.get('selectors', []) or [])
        for sel in candidate_selectors:
            try:
                if self.page.query_selector(sel):
                    log.info("[%s] limit detected via selector '%s'",
                             self.config.get('name', '?'), sel)
                    return "selector"
            except Exception:
                pass

        # 2. Text keywords scanned outside the response area. Service-curated
        #    keywords are checked first (stronger evidence than the generic list).
        service_kws = list(indicators.get('keywords', []) or [])
        common_kws = list(COMMON_LIMIT_KEYWORDS)
        if service_kws or common_kws:
            page_text = self._page_text_excluding_responses().lower()
            for kw in service_kws:
                if kw.lower() in page_text:
                    log.info("[%s] limit detected via service keyword '%s'",
                             self.config.get('name', '?'), kw)
                    return "service_keyword"
            for kw in common_kws:
                if kw.lower() in page_text:
                    log.info("[%s] limit hinted via common keyword '%s'",
                             self.config.get('name', '?'), kw)
                    return "common_keyword"
        return None

    def is_limit_reached(self) -> bool:
        """True when any limit evidence (of any strength) is present."""
        return self.detect_limit() is not None



    def _response_count_selector(self) -> str:
        """Selector used to count assistant response elements.

        Override in a driver when the configured ``response_container`` is not
        a reliable per-response counter.
        """
        return self.config['selectors']['response_container']

    def _extract_last_response(self, selector: str) -> str:
        """Extract clean text of the last response matching ``selector``.

        Uses the shared :data:`GENERIC_CLEAN_TEXT_JS` walker. Suitable for
        drivers without a custom code-block widget.
        """
        try:
            return self.page.evaluate(GENERIC_CLEAN_TEXT_JS, selector)
        except Exception:
            return ""

    def _count_responses(self) -> int:
        """Count response containers currently present on the page."""
        try:
            return len(self.page.query_selector_all(self._response_count_selector()))
        except Exception:
            return 0

    def mark_message_sent(self):
        """Snapshot the response count just before submitting a message.

        Drivers MUST call this at the start of ``send_message`` so that
        ``wait_for_response`` can tell this turn's reply apart from the
        previous turn's (which is still in the DOM).
        """
        self._response_count_before = self._count_responses()
        log.debug("[%s] mark_message_sent: prior response count = %d",
                  self.config.get('name', '?'), self._response_count_before)

    def _wait_message_sent(self, timeout: float = 3.0) -> bool:
        """Poll until a new response container appears (message was accepted)."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self._count_responses() > self._response_count_before:
                return True
            time.sleep(0.5)
        return False

    def _challenge_visible(self) -> bool:
        """Whether a human-verification challenge (slider captcha etc.) is
        shown. The tool never tries to defeat these -- it reports them so
        the user can complete the check in a visible browser window."""
        page_text = self._page_text_excluding_responses().lower()
        return any(kw.lower() in page_text for kw in COMMON_CHALLENGE_KEYWORDS)

    # If the response text stops changing for this long, the answer is done
    # regardless of what is_streaming_finished() claims. This is the safety
    # net for drivers whose finish detection breaks when the site's UI
    # changes (the observed failure mode: a completed answer sitting there
    # while we wait out the full timeout).
    STALL_RETURN_SECONDS = 15

    def wait_for_response(self, timeout: int = 120, on_update=None) -> str:
        """Wait for THIS turn's response to finish generating and return it.

        Unlike a naive "streaming finished" check, this first waits for a new
        response container to appear so it never returns the previous turn's
        reply. ``on_update`` is an optional callback invoked with the partial
        text as it grows (used for live streaming display).

        Completion is decided by the driver's ``is_streaming_finished`` plus a
        short text-stability window; independent of that, a non-empty answer
        that has not changed for ``STALL_RETURN_SECONDS`` is returned as-is.

        If the site raises a human-verification challenge: in headless mode
        this raises immediately (the user cannot see it -- rotate away); in
        headful mode it logs a warning and keeps waiting so the user can
        complete the check in the browser window.
        """
        start_time = time.time()
        last_text_len = 0
        stable_count = 0
        streaming_started = False
        iterations = 0
        challenge_warned = False
        last_change_time = time.time()

        while (time.time() - start_time) < timeout:
            iterations += 1
            if iterations % 5 == 0 and self._challenge_visible():
                name = self.config.get('name', '?')
                if self.headless:
                    raise RuntimeError(
                        f"{name} is showing a human-verification challenge "
                        f"(cannot be completed headless). Run with --headful "
                        f"or set 'headless: false' for this service and "
                        f"complete the check manually."
                    )
                if not challenge_warned:
                    log.warning("[%s] human-verification challenge visible; "
                                "please complete it in the browser window", name)
                    challenge_warned = True
            # Phase A: wait until a brand-new response container shows up.
            if not streaming_started:
                if self._count_responses() > self._response_count_before:
                    streaming_started = True
                    log.debug("[%s] streaming started", self.config.get('name', '?'))
                else:
                    time.sleep(1)
                    continue

            # Phase B: a new container exists; track its text until stable.
            current_text = self.get_last_response()
            if on_update and current_text:
                try:
                    on_update(current_text)
                except Exception:
                    pass

            if len(current_text) != last_text_len:
                last_change_time = time.time()

            if self.is_streaming_finished():
                if len(current_text) == last_text_len and len(current_text) > 0:
                    stable_count += 1
                else:
                    stable_count = 0
                    last_text_len = len(current_text)

                # Require a short window of stability before returning.
                if stable_count > 2:
                    log.debug("[%s] response stable; returning %d chars",
                              self.config.get('name', '?'), len(current_text))
                    return current_text
            else:
                stable_count = 0
                last_text_len = len(current_text)

                # Safety net: the driver still claims "generating", but the
                # text has been frozen for a while -- finish detection is
                # probably broken (site UI changed). Return what we have.
                if (current_text
                        and time.time() - last_change_time >= self.STALL_RETURN_SECONDS):
                    log.warning(
                        "[%s] finish detection never fired but the answer has "
                        "been stable for %ds; returning %d chars (check "
                        "is_streaming_finished / selectors)",
                        self.config.get('name', '?'), self.STALL_RETURN_SECONDS,
                        len(current_text))
                    return current_text

            time.sleep(1)

        log.warning("[%s] wait_for_response hit %ds timeout", self.config.get('name', '?'), timeout)
        return self.get_last_response()

    @abstractmethod
    def is_streaming_finished(self) -> bool:
        """Check if the LLM has finished streaming the response."""
        pass
