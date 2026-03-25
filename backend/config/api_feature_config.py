import os

import dotenv

dotenv.load_dotenv()


def _env_bool(key: str, default: str = "true") -> bool:
    return os.getenv(key, default).strip().lower() in {"1", "true", "yes", "on"}


class ApiFeatureConfig:
    # Deployment switch: controls account creation related APIs.
    ENABLE_ACCOUNT_CREATION_API = _env_bool("ENABLE_ACCOUNT_CREATION_API", "false")
    # Deployment switch: controls account update related APIs.
    ENABLE_ACCOUNT_UPDATE_API = _env_bool("ENABLE_ACCOUNT_UPDATE_API", "false")
    # Deployment switch: controls manual order placement API.
    ENABLE_MANUAL_ORDER_API = _env_bool("ENABLE_MANUAL_ORDER_API", "false")
