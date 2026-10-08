"""
Device router: ADB 设备状态查询。
"""

import sys
from pathlib import Path

from fastapi import APIRouter

from api.models.schemas import DeviceInfo, DeviceStatusResponse

# 加入项目根，使 monitors 包可以 import
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from monitors.adb_manager import ADBManager

router = APIRouter(prefix="/api/device", tags=["device"])
_adb = ADBManager()


@router.get("/status", response_model=DeviceStatusResponse)
async def device_status():
    adb_ok = _adb.is_adb_available()
    devices = _adb.list_devices() if adb_ok else []
    return DeviceStatusResponse(
        adb_available=adb_ok,
        devices=[
            DeviceInfo(
                serial=d.serial,
                status=d.status,
                model=d.model,
                ip=d.ip,
                is_connected=d.is_connected,
            )
            for d in devices
        ],
    )


@router.post("/connect")
async def connect_device(ip: str, port: int = 5555):
    success = _adb.connect(ip, port)
    return {"success": success, "ip": ip, "port": port}
