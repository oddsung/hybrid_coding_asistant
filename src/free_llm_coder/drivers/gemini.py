from .base import BaseDriver
from ..logging_setup import get_logger
import time

log = get_logger("driver.gemini")

class GeminiDriver(BaseDriver):
    def send_message(self, message: str):
        # Snapshot response count so wait_for_response can detect this turn's reply.
        self.mark_message_sent()
        selectors = self.config['selectors']
        # Wait for input area
        self.page.wait_for_selector(selectors['input_area'])

        # Gemini's input is a Quill contenteditable: insert via the verified
        # insert_text path (IME/Unicode-safe, and instant even for a large
        # project context, unlike keyboard.type).
        self.type_message(selectors['input_area'], message)
        time.sleep(1)

        # Click submit. Gemini localizes aria-labels (e.g. Korean UI), so the
        # configured selector may not match; Enter submits regardless of locale.
        try:
            self.page.click(selectors['submit_button'], timeout=5000)
        except Exception:
            log.debug("submit click failed; falling back to Enter")
            self.page.focus(selectors['input_area'])
            self.page.keyboard.press("Enter")

    def get_last_response(self) -> str:
        selectors = self.config['selectors']
        # Gemini responses might be multiple chunks, get the last one or accumulate
        responses = self.page.query_selector_all(selectors['response_container'])
        if not responses:
            return ""
        text = responses[-1].inner_text()
        # Drop the leading accessibility label ("Gemini의 응답" /
        # "Response from Gemini") that inner_text picks up.
        lines = text.split("\n")
        if lines and "gemini" in lines[0].lower() and len(lines[0]) < 40:
            text = "\n".join(lines[1:]).lstrip("\n")
        return text

    def is_streaming_finished(self) -> bool:
        selectors = self.config['selectors']
        submit_btn = selectors['submit_button']

        # Gemini: Check if send button is visible and enabled
        btn = self.page.query_selector(submit_btn)
        if btn is None:
            # Localized UI may rename the button; defer to the text-stability
            # window in wait_for_response instead of stalling to the timeout.
            return True
        if btn.is_visible() and not btn.is_disabled():
            return True
        return False
