import time
from typing import Optional
from .base import BaseDriver

class GrokDriver(BaseDriver):
    def send_message(self, message: str) -> None:
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
        # Grok uses data-testid="messageGroup" or message-content classes
        # Let's try to be broad but focused on the latest assistant reply
        selector = self.config['selectors'].get('response_container', "div[data-testid='messageGroup']")
        
        return self.page.evaluate("""
            (selector) => {
                const responses = document.querySelectorAll(selector);
                if (responses.length === 0) return "";
                // Get the last assistant message (Grok usually alternates user/assistant)
                // We'll trust the selector to be specific enough or filter here
                const el = responses[responses.length - 1];
                
                function getCleanText(node) {
                    if (node.nodeType === 3) {
                        return node.textContent.replace(/\\u00A0/g, ' ');
                    }
                    if (node.nodeType !== 1) return "";
                    
                    // Grok might have its own code block markers
                    // For now, use a generic expansion
                    
                    if (node.tagName === 'BR') return '\\n';
                    
                    let text = "";
                    for (let child of node.childNodes) {
                        text += getCleanText(child);
                    }
                    
                    const style = window.getComputedStyle(node);
                    if (style.display === 'block' || style.display === 'flex' || node.tagName === 'P' || node.tagName === 'DIV') {
                        if (text && !text.endsWith('\\n')) text += '\\n';
                    }
                    return text;
                }
                
                return getCleanText(el).trim();
            }
        """, selector)

    def is_limit_reached(self) -> bool:
        selectors = self.config['selectors']
        limit_msg_selector = selectors.get('limit_message')
        if limit_msg_selector and self.page.query_selector(limit_msg_selector):
            return True
        return False

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
