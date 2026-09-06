"""
Stub out optional heavy dependencies that are not installed in the test
environment (cv2, torch, etc.) so that test collection succeeds without
needing a GPU/camera runtime.
"""

import sys
import types


def _stub(name: str) -> types.ModuleType:
    mod = types.ModuleType(name)
    sys.modules[name] = mod
    return mod


for _heavy in [
    "cv2",
    "torch",
    "torchvision",
    "paddleocr",
    "paddlepaddle",
    "transformers",
    "accelerate",
    "bitsandbytes",
]:
    if _heavy not in sys.modules:
        _stub(_heavy)
