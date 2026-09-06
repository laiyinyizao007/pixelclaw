"""
Root conftest: stub heavy optional dependencies before pytest collects any
module so that tests can run without GPU/camera/cv2 installed.

The stubs are injected at module level (before any test or hook runs) so that
even the project root __init__.py imports safely.
"""

import sys
import types


def _stub(name: str) -> types.ModuleType:
    mod = types.ModuleType(name)
    sys.modules[name] = mod
    return mod


_HEAVY_DEPS = [
    "cv2",
    "torch",
    "torchvision",
    "paddleocr",
    "paddlepaddle",
    "transformers",
    "accelerate",
    "bitsandbytes",
]

for _heavy in _HEAVY_DEPS:
    if _heavy not in sys.modules:
        _stub(_heavy)

# Also pre-stub common sub-modules that get accessed on import
_sub = {
    "cv2.cv2": "cv2",
    "torch.nn": "torch",
    "torch.cuda": "torch",
    "torchvision.transforms": "torchvision",
}
for _sub_name, _parent in _sub.items():
    if _sub_name not in sys.modules:
        child = types.ModuleType(_sub_name)
        setattr(sys.modules[_parent], _sub_name.split(".")[-1], child)
        sys.modules[_sub_name] = child
