from .base import BaseDriver
import time

class GeminiDriver(BaseDriver):
    def send_message(self, message: str):
        selectors = self.config['selectors']
        # Wait for input area
        self.page.wait_for_selector(selectors['input_area'])
        
        # Focus and Type
        self.page.click(selectors['input_area'])
        self.page.keyboard.type(message)
        time.sleep(1)
        
        # Click submit
        self.page.click(selectors['submit_button'])

    def get_last_response(self) -> str:
        selectors = self.config['selectors']
        # Gemini responses might be multiple chunks, get the last one or accumulate
        responses = self.page.query_selector_all(selectors['response_container'])
        if responses:
            return responses[-1].inner_text()
        return ""

    def is_limit_reached(self) -> bool:
        # Check for textual indicators
        content = self.page.content()
        return "reached your limit" in content or "capacity" in content
