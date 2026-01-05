import time
from .base import BaseDriver

class DeepSeekDriver(BaseDriver):
    def send_message(self, message: str) -> None:
        selectors = self.config['selectors']
        input_selector = selectors['input_area']
        submit_selector = selectors['submit_button']
        
        # Ensure focus and type to trigger input events
        try:
            self.page.wait_for_selector(input_selector, timeout=15000)
            self.page.focus(input_selector)
        except:
            input_selector = "textarea"
            self.page.wait_for_selector(input_selector, timeout=5000)
            self.page.focus(input_selector)
            
        # Clear if any (though usually empty)
        # self.page.fill(input_selector, "")
        
        # Simplified approach: Use execCommand to "paste" (enables button) 
        # and then press Enter to send.
        try:
            # 1. Focus and insert text using execCommand (most reliable for state update)
            self.page.evaluate(f'''(msg) => {{
                const textarea = document.querySelector('textarea');
                if (textarea) {{
                    textarea.focus();
                    textarea.select();
                    textarea.value = '';
                    document.execCommand('insertText', false, msg);
                    
                    // Dispatch events just in case
                    textarea.dispatchEvent(new Event('input', {{ bubbles: true }}));
                }}
            }}''', message)
            
            time.sleep(0.5) # Brief pause for UI to register
            
            # 2. Press Enter to trigger the send functionality
            self.page.keyboard.press("Enter")
            
            # 3. Fallback: Try clicking the button via JS if Enter didn't work 
            # (though Enter is usually the most reliable once text is inserted)
            self.page.evaluate(f'''() => {{
                const sendBtn = Array.from(document.querySelectorAll('div[role="button"]'))
                                     .find(b => (b.innerHTML.includes('svg') || b.innerHTML.includes('path')) && !b.innerText.includes('?'));
                if (sendBtn && !sendBtn.classList.contains('ds-icon-button--disabled')) {{
                    sendBtn.click();
                }}
            }}''')
        except Exception as e:
            print(f"[Warning] DeepSeek send failure: {e}")
            self.page.fill(input_selector, message)
            self.page.keyboard.press("Enter")

        time.sleep(2) # Wait for UI to transition

    def get_last_response(self) -> str:
        # DeepSeek uses .ds-markdown for responses
        selector = ".ds-markdown"
        
        return self.page.evaluate("""
            (selector) => {
                const responses = document.querySelectorAll(selector);
                if (responses.length === 0) return "";
                const el = responses[responses.length - 1];
                
                function getCleanText(node) {
                    if (node.nodeType === 3) {
                        return node.textContent.replace(/\\u00A0/g, ' ');
                    }
                    if (node.nodeType !== 1) return "";
                    
                    // Skip buttons (Copy/Download UI) and any element with 'ds-icon-button' class
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
                    // Ensure block elements (div, p, pre, code blocks) get newlines
                    if (style.display === 'block' || style.display === 'flex' || 
                        node.tagName === 'P' || node.tagName === 'DIV' || node.tagName === 'PRE') {
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
        """DeepSeek specific: Check if generation is happening."""
        # DeepSeek shows a square stop icon or "Stop generating" text when active.
        # We also check if the send button is disabled/hidden which often happens during gen.
        generating = self.page.evaluate("""() => {
            const btns = Array.from(document.querySelectorAll('div[role="button"]'));
            return btns.some(btn => {
                const html = btn.innerHTML.toLowerCase();
                const text = btn.innerText.toLowerCase();
                // Check for stop icon (rect) or specific text
                return (html.includes('rect') || html.includes('process') || text.includes('stop')) && !text.includes('?');
            });
        }""")
        
        return not generating
