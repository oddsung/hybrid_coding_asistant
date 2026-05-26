import time
from typing import Optional, Dict
from ..drivers.base import BaseDriver
from ..drivers import DRIVER_REGISTRY
from ..logging_setup import get_logger
from playwright.sync_api import sync_playwright, Playwright

log = get_logger("service_manager")

# Circuit-breaker tuning.
FAILURE_THRESHOLD = 3       # consecutive failures before a service is "opened"
COOLDOWN_SECONDS = 300      # how long an opened service stays skipped


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
                log.warning("preferred service '%s' not found in config; using default order", preferred_service)

        self.services_config = services
        self.drivers: Dict[str, BaseDriver] = {}
        self.active_service_index = 0
        self.user_data_base = config['browser'].get('user_data_dir', './user_data')
        self.headless = config['browser'].get('headless', False)
        self.playwright: Optional[Playwright] = None

        # Per-service circuit breaker: state is "closed" (healthy),
        # "open" (skipped during cooldown), or "half-open" (one trial allowed).
        self.breakers: Dict[str, dict] = {
            svc['name']: {"failures": 0, "opened_at": 0.0, "state": "closed"}
            for svc in self.services_config
        }

    # ------------------------------------------------------------------ #
    # Circuit breaker
    # ------------------------------------------------------------------ #
    def _is_available(self, name: str) -> bool:
        """Whether a service may be tried now. Transitions open -> half-open
        once the cooldown elapses."""
        breaker = self.breakers.get(name)
        if breaker is None:
            return True
        if breaker['state'] == 'open':
            if time.time() - breaker['opened_at'] >= COOLDOWN_SECONDS:
                breaker['state'] = 'half-open'
                return True
            return False
        return True  # closed or half-open

    def record_failure(self, name: str):
        """Count a failure; open the breaker after repeated failures, or
        immediately if a half-open trial failed."""
        breaker = self.breakers.get(name)
        if breaker is None:
            return
        breaker['failures'] += 1
        if breaker['state'] == 'half-open' or breaker['failures'] >= FAILURE_THRESHOLD:
            breaker['state'] = 'open'
            breaker['opened_at'] = time.time()
            log.info("circuit opened for '%s' (cooldown %ss)", name, COOLDOWN_SECONDS)

    def record_success(self, name: str):
        """Reset a service's breaker after a successful exchange."""
        breaker = self.breakers.get(name)
        if breaker is not None:
            breaker['failures'] = 0
            breaker['state'] = 'closed'

    def mark_success(self):
        """Record success for the currently-active service."""
        if self.active_service_index < len(self.services_config):
            self.record_success(self.services_config[self.active_service_index]['name'])

    def _first_available_index(self, start: int) -> Optional[int]:
        for i in range(start, len(self.services_config)):
            if self._is_available(self.services_config[i]['name']):
                return i
        return None

    def reset_rotation(self):
        """Re-select the highest-priority available service. Call once per
        prompt so services whose cooldown elapsed become eligible again."""
        idx = self._first_available_index(0)
        self.active_service_index = idx if idx is not None else len(self.services_config)

    # ------------------------------------------------------------------ #
    # Driver lifecycle
    # ------------------------------------------------------------------ #
    def _get_playwright(self) -> Playwright:
        if not self.playwright:
            self.playwright = sync_playwright().start()
        return self.playwright

    def _create_driver(self, service_cfg: dict) -> BaseDriver:
        name = service_cfg['name']
        user_data_dir = f"{self.user_data_base}/{name}"

        driver_cls = DRIVER_REGISTRY.get(name)
        if driver_cls is None:
            raise ValueError(
                f"Unknown service driver: '{name}'. "
                f"Known drivers: {', '.join(sorted(DRIVER_REGISTRY))}"
            )
        return driver_cls(service_cfg, user_data_dir, self.headless)

    def get_active_driver(self) -> BaseDriver:
        """Returns the current active driver, initializing it if necessary."""
        if self.active_service_index >= len(self.services_config):
            raise RuntimeError("All services are currently unavailable or limited.")

        current_cfg = self.services_config[self.active_service_index]
        name = current_cfg['name']

        if name not in self.drivers:
            log.info("initializing driver for '%s'", name)
            driver = self._create_driver(current_cfg)
            driver.start_browser(self._get_playwright())
            driver.navigate()
            self.drivers[name] = driver

        return self.drivers[name]

    def get_active(self) -> tuple:
        """Return ``(driver, service_name)`` for the active service.

        Lets callers avoid indexing ``services_config`` directly, which is
        error-prone when ``get_active_driver`` raises before the name is read.
        """
        driver = self.get_active_driver()
        name = self.services_config[self.active_service_index]['name']
        return driver, name

    def is_exhausted(self) -> bool:
        """True when no service is currently available to try."""
        return self.active_service_index >= len(self.services_config)

    def rotate_service(self):
        """Record a failure for the current service and switch to the next
        available one (skipping services with an open circuit breaker)."""
        if self.active_service_index < len(self.services_config):
            current_name = self.services_config[self.active_service_index]['name']
            log.info("rotating away from '%s'", current_name)
            self.record_failure(current_name)

            # Close current driver to release browser locks and resources.
            if current_name in self.drivers:
                self.drivers[current_name].close()
                del self.drivers[current_name]

        idx = self._first_available_index(self.active_service_index + 1)
        if idx is None:
            self.active_service_index = len(self.services_config)
            log.info("no more available services for this prompt")
        else:
            self.active_service_index = idx
            log.info("switching to '%s'", self.services_config[idx]['name'])

    def new_chat_all(self):
        """Start a fresh conversation on every initialized driver."""
        for name, driver in self.drivers.items():
            try:
                driver.new_chat()
            except Exception as e:
                log.warning("new_chat failed for '%s': %s", name, e)

    def close_all(self):
        for driver in self.drivers.values():
            driver.close()
        self.drivers.clear()
        if self.playwright:
            self.playwright.stop()
            self.playwright = None
