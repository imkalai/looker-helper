from app.config import get_settings
from app.services.metadata.base import MetadataError, MetadataProvider


def get_metadata_provider() -> MetadataProvider:
    if get_settings().metadata_mode == "live":
        from app.services.metadata.live import LiveBigQuery

        return LiveBigQuery()
    from app.services.metadata.demo import DemoMetadata

    return DemoMetadata()


__all__ = ["MetadataError", "MetadataProvider", "get_metadata_provider"]
