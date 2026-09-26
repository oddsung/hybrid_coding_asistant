from .base import BaseDriver
import time

class ChatGPTDriver(BaseDriver):
    def send_message(self, message: str):
        # Snapshot response count so wait_for_response can detect this turn's reply.
        self.mark_message_sent()
        selectors = self.config['selectors']
        # Wait for input area
        self.page.wait_for_selector(selectors['input_area'])

        # ChatGPT's input is a ProseMirror contenteditable; fill() mangles
        # composed characters (Hangul/CJK) there, so use the verified
        # insert_text path from BaseDriver.
        self.type_message(selectors['input_area'], message)
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
                        
                        const newContent = document.createTextNode(`\n\\`\\`\\`bash\n${code.innerText}\n\\`\\`\\`\n`);
                        pre.replaceWith(newContent);
                    }
                });
                
                return clone.innerText;
            }
        """, selectors['response_container'])

    def is_streaming_finished(self) -> bool:
        """ChatGPT specific: the stop button exists only WHILE generating.

        The send button is not a reliable "finished" signal: with an empty
        composer ChatGPT shows a voice button instead, so waiting for the
        send button after a completed answer stalls until the timeout.
        """
        if self.page.query_selector("button[data-testid='stop-button']"):
            return False
        btn = self.page.query_selector(self.config['selectors']['submit_button'])
        if btn is not None:
            return not btn.is_disabled()
        # No stop and no send button (empty composer after completion):
        # treat as finished and let the text-stability window decide.
        return True
