"""SQLAlchemy ORM models.

Importing this package registers every model on ``Base.metadata``, which is
what Alembic's autogenerate compares against. A model that is not imported here
is invisible to migrations.
"""

from app.models.enums import (
    AdapterType,
    AlertStatus,
    AlertType,
    AuditResult,
    CameraStatus,
    CameraType,
    DepartmentCode,
    Priority,
    Protocol,
    Role,
    VehicleType,
    VmsVendor,
    WatchlistCategory,
)
from app.models.intelligence import Alert, Detection, VehicleTrack, Watchlist
from app.models.registry import Camera, CameraHealth, Department, VmsInstance
from app.models.security import AuditLog, User

__all__ = [
    "AdapterType",
    "Alert",
    "AlertStatus",
    "AlertType",
    "AuditLog",
    "AuditResult",
    "Camera",
    "CameraHealth",
    "CameraStatus",
    "CameraType",
    "Department",
    "DepartmentCode",
    "Detection",
    "Priority",
    "Protocol",
    "Role",
    "User",
    "VehicleTrack",
    "VehicleType",
    "VmsInstance",
    "VmsVendor",
    "Watchlist",
    "WatchlistCategory",
]
