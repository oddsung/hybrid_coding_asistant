import time
from .base import BaseDriver

class DeepSeekDriver(BaseDriver):
    def send_message(self, message: str) -> None:
        # Snapshot response count so wait_for_response can detect this turn's reply.
        self.mark_message_sent()

        selectors = self.config['selectors']
        input_selector = selectors['input_area']

        # Ensure focus and type to trigger input events
        try:
            self.page.wait_for_selector(input_selector, timeout=15000)
            self.page.focus(input_selector)
        except Exception:
            input_selector = "textarea"
            self.page.wait_for_selector(input_selector, timeout=5000)
            self.page.focus(input_selector)

        try:
            # 1. Insert text via execCommand (most reliable for state update,
            #    this is what enables the send button).
            self.page.evaluate('''(msg) => {
                const textarea = document.querySelector('textarea');
                if (textarea) {
                    textarea.focus();
                    textarea.select();
                    textarea.value = '';
                    document.execCommand('insertText', false, msg);
                    textarea.dispatchEvent(new Event('input', { bubbles: true }));
                }
            }''', message)
            time.sleep(0.5)  # Brief pause for UI to register

            # 2. Primary: click the send button. We deliberately do NOT also
            #    press Enter here -- doing both can submit the message twice.
            self.page.evaluate('''() => {
                const sendBtn = Array.from(document.querySelectorAll('div[role="button"]'))
                                     .find(b => (b.innerHTML.includes('svg') || b.innerHTML.includes('path')) && !b.innerText.includes('?'));
                if (sendBtn && !sendBtn.classList.contains('ds-icon-button--disabled')) {
                    sendBtn.click();
                }
            }''')

            # 3. Fallback: only if the click did not actually send, press Enter once.
            if not self._wait_message_sent(timeout=3):
                self.page.keyboard.press("Enter")
        except Exception as e:
            print(f"[Warning] DeepSeek send failure: {e}")
            self.page.fill(input_selector, message)
            self.page.keyboard.press("Enter")

        time.sleep(1)  # Wait for UI to transition

    def get_last_response(self) -> str:
        # DeepSeek uses .ds-markdown for responses; the shared walker already
        # skips its Copy/Download icon buttons.
        return self._extract_last_response(".ds-markdown")

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
