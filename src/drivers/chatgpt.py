from .base import BaseDriver
import time

class ChatGPTDriver(BaseDriver):
    def send_message(self, message: str):
        selectors = self.config['selectors']
        # Wait for input area
        self.page.wait_for_selector(selectors['input_area'])
        
        # Fill message
        self.page.fill(selectors['input_area'], message)
        time.sleep(1) # Small delay
        
        # Click submit
        self.page.click(selectors['submit_button'])

    def get_last_response(self) -> str:
        selectors = self.config['selectors']
        # Return the last element matching the response container
        responses = self.page.query_selector_all(selectors['response_container'])
        if responses:
            return responses[-1].inner_text()
        return ""

    def is_limit_reached(self) -> bool:
        selectors = self.config['selectors']
        error_selector = selectors.get('error_message')
        if error_selector and self.page.query_selector(error_selector):
            return True
        # Check text content for "limit" keywords if needed
        return False
