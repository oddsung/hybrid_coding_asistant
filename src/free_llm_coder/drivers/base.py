from abc import ABC, abstractmethod
from playwright.sync_api import Playwright, Browser, BrowserContext, Page, sync_playwright
import time
from typing import Optional

class BaseDriver(ABC):
    def __init__(self, service_config: dict, user_data_dir: str, headless: bool = False):
        self.config = service_config
        self.user_data_dir = user_data_dir
        self.headless = headless
        self.playwright: Optional[Playwright] = None
        self.browser: Optional[BrowserContext] = None
        self.page: Optional[Page] = None

    def start_browser(self):
        self.playwright = sync_playwright().start()
        # Use persistent context to save login session
        self.browser = self.playwright.chromium.launch_persistent_context(
            user_data_dir=self.user_data_dir,
            headless=self.headless,
            channel="chrome", # Try to use installed Chrome if available
            args=[
                "--no-sandbox", 
                "--disable-setuid-sandbox",
                "--disable-blink-features=AutomationControlled" 
            ],
            ignore_default_args=["--enable-automation"]
        )
        
        if self.browser.pages:
            self.page = self.browser.pages[0]
        else:
            self.page = self.browser.new_page()
            
        # stealth bypass for webdriver detection
        self.page.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")

    def navigate(self):
        if not self.page:
            self.start_browser()
        self.page.goto(self.config['url'])
        try:
            self.page.wait_for_load_state("networkidle", timeout=10000)
        except:
            pass # Ignore networkidle timeout and proceed if page is somewhat loaded

    def close(self):
        if self.browser:
            self.browser.close()
        if self.playwright:
            self.playwright.stop()

    @abstractmethod
    def send_message(self, message: str):
        """Send a message to the chat interface."""
        pass

    @abstractmethod
    def get_last_response(self) -> str:
        """Retrieve the last response from the chat interface."""
        pass

    @abstractmethod
    def is_limit_reached(self) -> bool:
        """Check if the usage limit has been reached."""
        pass



    def wait_for_response(self, timeout: int = 120) -> str:
        """Wait for response generation to complete and return it."""
        start_time = time.time()
        last_text_len = 0
        stable_count = 0
        
        while (time.time() - start_time) < timeout:
            if self.is_streaming_finished():
                # Double check stability
                current_text = self.get_last_response()
                if len(current_text) == last_text_len and len(current_text) > 0:
                    stable_count += 1
                else:
                    stable_count = 0
                    last_text_len = len(current_text)
                
                # If finished signal is distinct, we can return early
                # But to be safe, wait for a bit of stability
                if stable_count > 2: 
                    return current_text
            
            time.sleep(1)
            
        return self.get_last_response()

    @abstractmethod
    def is_streaming_finished(self) -> bool:
        """Check if the LLM has finished streaming the response."""
        pass
