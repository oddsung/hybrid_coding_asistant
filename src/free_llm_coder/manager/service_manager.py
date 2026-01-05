from typing import List, Optional, Dict
from ..drivers.base import BaseDriver
from ..drivers.chatgpt import ChatGPTDriver
from ..drivers.gemini import GeminiDriver
from ..drivers.qwen import QwenDriver
from ..drivers.grok import GrokDriver
from ..drivers.deepseek import DeepSeekDriver
from playwright.sync_api import sync_playwright, Playwright
# from ..drivers.deepseek import DeepSeekDriver # Future implementation

class ServiceManager:
    def __init__(self, config: dict, preferred_service: Optional[str] = None):
        self.config = config
        
        # Sort by priority first
        services = sorted(config['services'], key=lambda x: x['priority'])
        
        # If preferred_service is specified, move it to the front
        if preferred_service:
            preferred = next((s for s in services if s['name'] == preferred_service), None)
            if preferred:
                services.remove(preferred)
                services.insert(0, preferred)
            else:
                print(f"[Warning] Preferred service '{preferred_service}' not found in configuration. Using default order.")
                
        self.services_config = services
        self.drivers: Dict[str, BaseDriver] = {}
        self.active_service_index = 0
        self.user_data_base = config['browser'].get('user_data_dir', './user_data')
        self.headless = config['browser'].get('headless', False)
        self.playwright: Optional[Playwright] = None


    def _get_playwright(self) -> Playwright:
        if not self.playwright:
            self.playwright = sync_playwright().start()
        return self.playwright


    def _create_driver(self, service_cfg: dict) -> BaseDriver:
        name = service_cfg['name']
        user_data_dir = f"{self.user_data_base}/{name}"
        
        if name == 'chatgpt':
            return ChatGPTDriver(service_cfg, user_data_dir, self.headless)
        elif name == 'gemini':
            return GeminiDriver(service_cfg, user_data_dir, self.headless)
        elif name == 'qwen':
            return QwenDriver(service_cfg, user_data_dir, self.headless)
        elif name == 'grok':
            return GrokDriver(service_cfg, user_data_dir, self.headless)
        elif name == 'deepseek':
            return DeepSeekDriver(service_cfg, user_data_dir, self.headless)
        
        raise ValueError(f"Unknown service driver: {name}")

    def get_active_driver(self) -> BaseDriver:
        """Returns the current active driver, initializing it if necessary."""
        if self.active_service_index >= len(self.services_config):
            raise RuntimeError("All services are currently unavailable or limited.")

        current_cfg = self.services_config[self.active_service_index]
        name = current_cfg['name']

        if name not in self.drivers:
            print(f"[System] Initializing driver for {name}...")
            driver = self._create_driver(current_cfg)
            driver.start_browser(self._get_playwright())
            driver.navigate()
            self.drivers[name] = driver

        return self.drivers[name]

    def rotate_service(self):
        """Switch to the next available service."""
        print(f"[System] API limit or error detected. Rotating service from {self.services_config[self.active_service_index]['name']}...")
        
        # Close current driver to release browser locks and resources
        current_name = self.services_config[self.active_service_index]['name']
        if current_name in self.drivers:
            self.drivers[current_name].close()
            del self.drivers[current_name]

        self.active_service_index += 1
        
        if self.active_service_index >= len(self.services_config):
            print("[System] CRITICAL: All services exhausted.")
            # Reset or handle complete failure
            return

        # Note: We don't initialize the next driver here to avoid duplicate initialization 
        # when called from main.py's continue loop.
        print(f"[System] Will switch to {self.services_config[self.active_service_index]['name']}.")

    def close_all(self):
        for driver in self.drivers.values():
            driver.close()
        if self.playwright:
            self.playwright.stop()
            self.playwright = None
