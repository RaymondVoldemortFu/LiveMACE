import os
import dotenv

dotenv.load_dotenv()


class SystemTimeConfig:
    # System time delay in minutes (use DELTA_T_MINUTES or DELTA_T env var)
    DELTA_T_MINUTES = float(os.getenv("DELTA_T_MINUTES", os.getenv("DELTA_T", "0")))
