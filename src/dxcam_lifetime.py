"""Compatibility fix for DXcam 0.3 COM ownership, installed before camera creation.

comtypes pointers own a reference and release it in their destructor. Calling
IUnknown.Release manually and then dropping the pointer releases that reference
twice. Keep ReleaseFrame/Unmap and DXCamera.release; only fix the extra COM calls.
https://comtypes.readthedocs.io/en/stable/com_interfaces.html#comtypes.IUnknown.Release
"""
import threading

_lock = threading.Lock()


def release_surface(self):
    # QueryInterface created a separate owned reference. Drop both wrappers;
    # outstanding references held elsewhere remain valid until their own release.
    self.interface = None
    self.texture = None
    self.width = 0
    self.height = 0


def release_duplicator(self):
    try:
        if self.duplicator is not None:
            self.release_frame()
    finally:
        self.texture = None
        self.duplicator = None
        self._frame_held = False


def install():
    from dxcam.core.stagesurf import StageSurface
    from dxcam.core.dxgi_duplicator import DXGIDuplicator
    with _lock:
        # Do not override an upstream version that already removed manual Release.
        for cls, replacement in ((StageSurface, release_surface),
                                 (DXGIDuplicator, release_duplicator)):
            code = getattr(cls.release, '__code__', None)
            if code is not None and 'Release' in code.co_names:
                cls.release = replacement
