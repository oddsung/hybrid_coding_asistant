from abc import ABC, abstractmethod
from playwright.sync_api import Playwright, BrowserContext, Page
import time
from typing import Optional

from ..config_schema import COMMON_LIMIT_KEYWORDS
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

    def start_browser(self, playwright: Playwright):
        self.playwright = playwright
        # Use persistent context to save login session
        self.browser = self.playwright.chromium.launch_persistent_context(
            user_data_dir=self.user_data_dir,
            headless=self.headless,
            channel="chrome", # Try to use installed Chrome if available
            args=[
                "--no-sandbox", 
                "--disable-setuid-sandbox",
                "--disable-blink-features=AutomationControlled" 
            ],
            ignore_default_args=["--enable-automation"]
        )
        
        if self.browser.pages:
            self.page = self.browser.pages[0]
        else:
            self.page = self.browser.new_page()
            
        # stealth bypass for webdriver detection
        self.page.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")

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

    def close(self):
        if self.browser:
            self.browser.close()
        # Do NOT stop playwright here, as it might be shared

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

    def is_limit_reached(self) -> bool:
        """Detect a usage/rate limit, generic across services.

        Uses the service's ``selectors.error_message`` and the optional
        ``limit_indicators`` block (CSS selectors + text keywords) from config,
        combined with :data:`COMMON_LIMIT_KEYWORDS`. Configuring detectors in
        config means a UI change can be fixed without touching code.
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
                    return True
            except Exception:
                pass

        # 2. Text keywords scanned outside the response area.
        keywords = list(COMMON_LIMIT_KEYWORDS) + list(indicators.get('keywords', []) or [])
        if keywords:
            page_text = self._page_text_excluding_responses().lower()
            for kw in keywords:
                if kw.lower() in page_text:
                    log.info("[%s] limit detected via keyword '%s'",
                             self.config.get('name', '?'), kw)
                    return True
        return False



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

    def wait_for_response(self, timeout: int = 120, on_update=None) -> str:
        """Wait for THIS turn's response to finish generating and return it.

        Unlike a naive "streaming finished" check, this first waits for a new
        response container to appear so it never returns the previous turn's
        reply. ``on_update`` is an optional callback invoked with the partial
        text as it grows (used for live streaming display).
        """
        start_time = time.time()
        last_text_len = 0
        stable_count = 0
        streaming_started = False

        while (time.time() - start_time) < timeout:
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

            time.sleep(1)

        log.warning("[%s] wait_for_response hit %ds timeout", self.config.get('name', '?'), timeout)
        return self.get_last_response()

    @abstractmethod
    def is_streaming_finished(self) -> bool:
        """Check if the LLM has finished streaming the response."""
        pass
