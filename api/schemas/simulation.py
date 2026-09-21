from pydantic import BaseModel, Field
from typing import Optional


# ==============================================================
# REALTIME START REQUEST
# ==============================================================

class RealtimeStartRequest(BaseModel):
    """Configuration for starting the real-time simulation loop."""

    tick_interval_seconds: float = Field(
        default=1.0,
        ge=0.05,
        le=60.0,
        description="Wall-clock seconds per simulation tick.",
    )
    minutes_per_tick: float = Field(
        default=1.0 / 60.0,
        ge=0.0001,
        le=60.0,
        description="Simulated minutes advanced per tick.",
    )


# ==============================================================
# REALTIME STATUS RESPONSE
# ==============================================================

class RealtimeStatusResponse(BaseModel):
    """Current status and telemetry of the real-time simulation loop."""

    status: str
    is_running: bool
    current_time: int
    tick_interval_seconds: float
    minutes_per_tick: float
    speed_multiplier: float
    ticks_processed: int
    started_at: Optional[str] = None
    last_error: Optional[str] = None


# ==============================================================
# REALTIME CONTROL RESPONSE
# ==============================================================

class RealtimeControlResponse(BaseModel):
    """Response returned upon start/stop lifecycle actions."""

    status: str
    message: str
    time: int
