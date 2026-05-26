import time
from .base import BaseDriver

class QwenDriver(BaseDriver):
    # Qwen renders assistant replies inside .qwen-chat-message-assistant only,
    # so count those to avoid counting the user's own messages.
    def _response_count_selector(self) -> str:
        return ".qwen-chat-message-assistant .qwen-markdown"

    def send_message(self, message: str):
        # Snapshot response count so wait_for_response can detect this turn's reply.
        self.mark_message_sent()
        selectors = self.config['selectors']
        # Wait for input area
        self.page.wait_for_selector(selectors['input_area'])
        
        # Fill message
        self.page.fill(selectors['input_area'], message)
        time.sleep(1) # Small delay
        
        # Click submit
        self.page.click(selectors['submit_button'])
        time.sleep(2) # Wait for UI to transition to 'generating' state (stop button to appear)

    def get_last_response(self) -> str:
        # Use more specific selector to avoid user messages
        # Qwen's latest assistant message container is .qwen-chat-message-assistant
        selector = ".qwen-chat-message-assistant .qwen-markdown"
        
        return self.page.evaluate("""
            (selector) => {
                const responses = document.querySelectorAll(selector);
                if (responses.length === 0) return "";
                const el = responses[responses.length - 1];
                
                function getCleanText(node) {
                    if (node.nodeType === 3) { // Text node
                        // Normalize non-breaking spaces to regular spaces (crucial for shell commands)
                        return node.textContent.replace(/\\u00A0/g, ' ');
                    }
                    if (node.nodeType !== 1) return "";
                    
                    // Handle Monaco Code Blocks
                    if (node.classList.contains('qwen-markdown-code')) {
                        const header = node.querySelector('.qwen-markdown-code-header');
                        const viewLines = node.querySelector('.view-lines');
                        
                        let language = 'bash'; // Default
                        if (header) {
                            // Header might contain language name + copy/download buttons
                            const headerText = header.innerText.trim();
                            if (headerText) {
                                language = headerText.split('\\n')[0].replace(/\\u00A0/g, ' ').trim().toLowerCase();
                            }
                        }
                        
                        let code = "";
                        if (viewLines) {
                            // Iterate through lines to preserve newlines. 
                            // Monaco renders each line in a div.
                            const lines = Array.from(viewLines.querySelectorAll('.view-line'));
                            code = lines.map(line => line.innerText.replace(/\\u00A0/g, ' ')).join('\\n');
                        }
                        
                        return '\\n```' + language + '\\n' + code + '\\n```\\n';
                    }
                    
                    if (node.tagName === 'BR') return '\\n';
                    
                    let text = "";
                    for (let child of node.childNodes) {
                        text += getCleanText(child);
                    }
                    
                    // Basic layout preservation for block elements
                    const style = window.getComputedStyle(node);
                    if (style.display === 'block' || style.display === 'flex' || node.tagName === 'P' || node.tagName === 'DIV') {
                        if (text && !text.endsWith('\\n')) text += '\\n';
                    }
                    return text;
                }
                
                return getCleanText(el).trim();
            }
        """, selector)

    def is_streaming_finished(self) -> bool:
        """Qwen specific: Check if generation is happening."""
        # Check for stop-button or any indicator of generation
        # The send button container usually has .stop-button when generating.
        generating = self.page.query_selector('.stop-button')
        if generating:
            return False
            
        # Check for send button being visible and enabled
        send_btn = self.page.query_selector('.send-button:not(.disabled)')
        if send_btn and send_btn.is_visible():
            return True
            
        return False
