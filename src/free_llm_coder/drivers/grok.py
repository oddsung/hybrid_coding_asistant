import time
from .base import BaseDriver

class GrokDriver(BaseDriver):
    def send_message(self, message: str) -> None:
        # Snapshot response count so wait_for_response can detect this turn's reply.
        self.mark_message_sent()
        selectors = self.config['selectors']
        input_selector = selectors['input_area']
        submit_selector = selectors['submit_button']
        
        # Wait for input to be ready
        try:
            self.page.wait_for_selector(input_selector, timeout=15000)
        except:
            # Fallback if preferred selector fails
            input_selector = "textarea"
            self.page.wait_for_selector(input_selector, timeout=5000)

        # Fill input
        self.page.fill(input_selector, message)
        time.sleep(1) # Small delay for UI updates
        
        # Click submit - use JavaScript to be more robust for dynamic buttons
        try:
            self.page.evaluate(f'''(sel) => {{
                const btn = document.querySelector(sel) || 
                            Array.from(document.querySelectorAll('button')).find(b => b.querySelector('svg'));
                if (btn) btn.click();
                else throw new Error("Send button not found");
            }}''', submit_selector)
        except Exception as e:
            print(f"[Warning] Failed to click send button via JS: {e}")
            # Fallback to normal click
            self.page.click(submit_selector)
            
        time.sleep(2) # Wait for UI to transition to 'generating' state

    def get_last_response(self) -> str:
        # Grok uses data-testid="messageGroup" or message-content classes.
        # Trust the configured selector to target the latest assistant reply.
        selector = self.config['selectors'].get('response_container', "div[data-testid='messageGroup']")
        return self._extract_last_response(selector)

    def is_streaming_finished(self) -> bool:
        """Grok specific: Check if generation is happening."""
        # Grok usually changes the send button to a stop button or disables it
        selectors = self.config['selectors']
        submit_btn = self.page.query_selector(selectors['submit_button'])
        
        # If the button is disabled or has a stop icon, it's still streaming
        if submit_btn:
            # Check for common stop icon patterns or disabled state
            is_disabled = submit_btn.get_attribute('disabled') is not None or "disabled" in submit_btn.get_attribute('class', '')
            if is_disabled:
                return False
            
            # Check for stop icon (often a rect or different SVG path)
            inner_html = submit_btn.inner_html()
            if "rect" in inner_html.lower() or "stop" in inner_html.lower():
                return False
            
            return True
            
        return False
