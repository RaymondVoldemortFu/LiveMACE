import os
import logging
from typing import Optional, Dict
import requests
import urllib.parse
import uuid

# Load environment variables (dotenv is loaded in main.py/startup, but good to ensure)
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

class ProxyConfig:
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(ProxyConfig, cls).__new__(cls)
            cls._instance._init_config()
        return cls._instance

    def _init_config(self):
        self.host = os.getenv("BRIGHTDATA_HOST")
        self.port = os.getenv("BRIGHTDATA_PORT")
        self.user = os.getenv("BRIGHTDATA_USER")
        self.password = os.getenv("BRIGHTDATA_PASS")
        self.enabled = all([self.host, self.port, self.user, self.password])

        if self.enabled:
            # URL encode user and password to handle special characters
            user_encoded = urllib.parse.quote_plus(self.user)
            pass_encoded = urllib.parse.quote_plus(self.password)

            # Construct proxy URL
            # Format: http://user:pass@host:port
            self.proxy_url = f"http://{user_encoded}:{pass_encoded}@{self.host}:{self.port}"
            logger.info(f"Proxy configuration loaded (Bright Data). Host: {self.host}")
        else:
            self.proxy_url = None
            logger.info("Proxy configuration not found or incomplete. Direct connection will be used.")

    def get_proxy_url(self) -> Optional[str]:
        return self.proxy_url

    def _build_session_username(self, session_id: str) -> str:
        """
        Build Bright Data session username.
        If username already contains a session, replace it; otherwise append.
        """
        if not self.user:
            return ""
        if "-session-" in self.user:
            return self.user.split("-session-")[0] + f"-session-{session_id}"
        return f"{self.user}-session-{session_id}"

    def get_proxy_url_with_session(self, session_id: Optional[str] = None) -> Optional[str]:
        if not self.enabled or not self.user or not self.password:
            return None
        if not session_id:
            session_id = uuid.uuid4().hex[:8]
        session_user = self._build_session_username(session_id)
        user_encoded = urllib.parse.quote_plus(session_user)
        pass_encoded = urllib.parse.quote_plus(self.password)
        return f"http://{user_encoded}:{pass_encoded}@{self.host}:{self.port}"

    def get_proxy_dict(self) -> Dict[str, str]:
        if self.enabled and self.proxy_url:
            return {
                "http": self.proxy_url,
                "https": self.proxy_url
            }
        return {}
    
    def check_proxy_availability(self) -> bool:
        """Check if proxy is working by making a request to a reliable endpoint"""
        if not self.enabled or not self.proxy_url:
            return True # Not enabled means "direct connection" is available
            
        try:
            logger.info(f"Checking proxy availability to {self.host}...")
            proxies = self.get_proxy_dict()
            # Use a reliable IP checking service or Google/Yahoo
            # timeout=10 to fail fast if proxy is down
            # Use verify=False to avoid SSL errors with proxy certificates in dev/test,
            # though requests usually handles CONNECT tunnels fine.
            # Using httpbin.org/ip is lightweight and returns the IP, confirming proxy usage.
            response = requests.get("http://httpbin.org/ip", proxies=proxies, timeout=15)
            
            if response.status_code == 200:
                origin = response.json().get("origin", "")
                logger.info(f"Proxy connection successful. Visible IP: {origin}")
                return True
            else:
                logger.error(f"Proxy check failed with status code: {response.status_code}")
                return False
        except Exception as e:
            logger.error(f"Proxy check failed: {e}")
            return False

    def setup_global_proxy(self):
        """Set proxy as environment variables for global use"""
        if self.enabled and self.proxy_url:
            os.environ["HTTP_PROXY"] = self.proxy_url
            os.environ["HTTPS_PROXY"] = self.proxy_url
            # os.environ["ALL_PROXY"] = self.proxy_url # ALL_PROXY can sometimes cause issues with certain tools if protocol not specified

            # Configure CA bundle for SSL verification
            ca_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "ca.crt"))
            os.environ["REQUESTS_CA_BUNDLE"] = ca_path
            os.environ["SSL_CERT_FILE"] = ca_path
            logger.info("Global proxy env set. CA bundle: %s", ca_path)

# Global instance
proxy_config = ProxyConfig()
