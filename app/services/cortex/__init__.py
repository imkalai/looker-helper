from sqlalchemy.orm import Session

from app.config import get_settings
from app.services.cortex.base import CortexClient, CortexError

CORTEX_SETTING_KEY = "cortex"


def cortex_config(db: Session) -> dict:
    from app.models import AppSetting
    from app.security import decrypt

    row = db.get(AppSetting, CORTEX_SETTING_KEY)
    config = dict(row.value) if row else {}
    config["api_key"] = decrypt(config.pop("api_key_enc", ""))
    return config


def get_cortex(db: Session) -> CortexClient:
    if get_settings().cortex_mode == "real":
        from app.services.cortex.real import RealCortex

        return RealCortex(cortex_config(db))
    from app.services.cortex.mock import MockCortex

    return MockCortex()


__all__ = ["CortexClient", "CortexError", "get_cortex", "cortex_config", "CORTEX_SETTING_KEY"]
