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
        # Use JS to reconstruct code blocks correctly (avoiding "Copy code" text and missing backticks)
        return self.page.evaluate("""
            (selector) => {
                const responses = document.querySelectorAll(selector);
                if (responses.length === 0) return "";
                const el = responses[responses.length - 1];
                
                // Clone node to modify it for extraction without affecting UI
                const clone = el.cloneNode(true);
                
                // Find all pre elements (code blocks)
                const pres = clone.querySelectorAll('pre');
                pres.forEach(pre => {
                    const code = pre.querySelector('code');
                    if (code) {
                        // Try to find the language label if it exists in the header
                        // The structure is usually pre > div > div (header) + div (code)
                        // The header usually contains the language name and "Copy code" button 
                        let lang = "bash"; // default
                        
                        // Attempt to clean up the header text which acts as pollution
                        // We essentially just value the 'code' element's text.
                        
                        const newContent = document.createTextNode(`\n\`\`\`bash\n${code.innerText}\n\`\`\`\n`);
                        pre.replaceWith(newContent);
                    }
                });
                
                return clone.innerText;
            }
        """, selectors['response_container'])

    def is_limit_reached(self) -> bool:
        selectors = self.config['selectors']
        error_selector = selectors.get('error_message')
        if error_selector and self.page.query_selector(error_selector):
            return True
        # Check text content for "limit" keywords if needed
        return False

    def is_streaming_finished(self) -> bool:
        """ChatGPT specific: Wait until the send button appears/is enabled logic."""
        selectors = self.config['selectors']
        submit_btn = selectors['submit_button']
        
        # Simple check: Can we find the send button enabled?
        # In ChatGPT, when generating, the button is usually a 'Stop' button or hidden/disabled.
        btn = self.page.query_selector(submit_btn)
        if btn and not btn.is_disabled():
            return True
        return False
