"""Config-only driver for any chat UI describable by CSS selectors.

This driver has no site-specific code: everything it needs comes from the
service's config entry. That makes it the extension point for "as many free
services as possible" -- adding a new site is a config edit, not a code change:

    services:
      - name: mistral
        url: https://chat.mistral.ai
        priority: 8
        driver: generic
        selectors:
          input_area: "textarea"
          submit_button: "button[type='submit']"
          response_container: ".assistant-message"
          # Optional: element that exists only WHILE the reply is generating
          # (e.g. a stop button). Without it, completion falls back to the
          # text-stability window in BaseDriver.wait_for_response.
          generating_indicator: ".stop-button"

Verify selectors against the live site with `flc doctor -s <name>`.
"""
import time

from .base import BaseDriver
from ..logging_setup import get_logger

log = get_logger("driver.generic")


class GenericDriver(BaseDriver):
    def send_message(self, message: str) -> None:
        # Snapshot response count so wait_for_response can detect this turn's reply.
        self.mark_message_sent()
        selectors = self.config['selectors']
        input_selector = selectors['input_area']

        self.page.wait_for_selector(input_selector, timeout=15000)

        # Verified insert_text path from BaseDriver: works for <textarea> and
        # rich contenteditable editors alike, without mangling Hangul/CJK.
        self.type_message(input_selector, message)
        time.sleep(0.5)

        # Prefer the configured submit button; if the click did not actually
        # send (no new response container appears), try Enter once.
        submitted = False
        submit_selector = selectors.get('submit_button')
        if submit_selector:
            try:
                self.page.click(submit_selector, timeout=5000)
                submitted = self._wait_message_sent(timeout=3)
            except Exception as e:
                log.debug("[%s] submit click failed: %s", self.config.get('name', '?'), e)
        if not submitted:
            self.page.focus(input_selector)
            self.page.keyboard.press("Enter")
        time.sleep(1)

    def get_last_response(self) -> str:
        return self._extract_last_response(self.config['selectors']['response_container'])

    def is_streaming_finished(self) -> bool:
        """Generic completion check.

        If the config names a ``generating_indicator`` (an element present only
        while the reply streams), its absence means finished. Otherwise report
        finished and let wait_for_response's text-stability window decide.
        """
        indicator = (self.config.get('selectors') or {}).get('generating_indicator')
        if indicator:
            try:
                if self.page.query_selector(indicator):
                    return False
            except Exception:
                pass
        return True
