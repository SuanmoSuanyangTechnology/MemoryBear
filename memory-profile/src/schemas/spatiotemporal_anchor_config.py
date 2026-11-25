"""Wire models for spatiotemporal anchor configuration."""

from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictBool


class SpatiotemporalAnchorSettings(BaseModel):
    spatiotemporal_enabled: StrictBool
    spatiotemporal_auto_extract: StrictBool
    scene_merge_window_minutes: int = Field(strict=True, ge=30, le=1440)
    scene_location_normalize_enabled: StrictBool
    geo_coordinate_enabled: StrictBool
    temporal_chain_enabled: StrictBool
    temporal_chain_interval_minutes: int = Field(strict=True, ge=15, le=1440)
    temporal_chain_rebuild_limit: int = Field(strict=True, ge=50, le=1000)


class SpatiotemporalAnchorUpdate(SpatiotemporalAnchorSettings):
    model_config = ConfigDict(extra="forbid")

    config_id: UUID


class SpatiotemporalAnchorData(SpatiotemporalAnchorSettings):
    config_id: UUID
    is_default: bool
