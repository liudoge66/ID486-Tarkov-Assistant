"""Thread-owned HDR/SDR capture with source-present timestamps, never adaptive exposure."""
import ctypes as C
from ctypes import wintypes as W
from pathlib import Path
import sys,time
import numpy as np

class Luid(C.Structure):_fields_=[('low',W.DWORD),('high',W.LONG)]
class Header(C.Structure):_fields_=[('type',W.UINT),('size',W.UINT),('adapter',Luid),('id',W.UINT)]
class Source(C.Structure):_fields_=[('adapter',Luid),('id',W.UINT),('mode',W.UINT),('flags',W.UINT)]
class Target(C.Structure):_fields_=[('adapter',Luid),('id',W.UINT),('mode',W.UINT),('technology',W.UINT),('rotation',W.UINT),('scaling',W.UINT),('num',W.UINT),('den',W.UINT),('scan',W.UINT),('available',W.BOOL),('flags',W.UINT)]
class DisplayPath(C.Structure):_fields_=[('source',Source),('target',Target),('flags',W.UINT)]
class Mode(C.Structure):_fields_=[('raw',C.c_byte*64)]
class Color2(C.Structure):_fields_=[('header',Header),('flags',W.UINT),('encoding',W.UINT),('bpc',W.UINT),('mode',W.UINT)]
class White(C.Structure):_fields_=[('header',Header),('value',W.UINT)]
class Name(C.Structure):_fields_=[('header',Header),('name',W.WCHAR*32)]
class NativeInfo(C.Structure):
    _fields_=[('abi',C.c_uint32),('size',C.c_uint32),('qpc',C.c_int64),('generation',C.c_uint64),('cached',C.c_uint32),('hdr',C.c_uint32),('white',C.c_float),('format',C.c_uint32)]

def display_state():
    """Read only. Match active paths by GDI name; no enumeration-order assumptions."""
    u=C.WinDLL('user32');pn=W.UINT();mn=W.UINT()
    u.GetDisplayConfigBufferSizes.argtypes=[W.UINT,C.POINTER(W.UINT),C.POINTER(W.UINT)]
    u.QueryDisplayConfig.argtypes=[W.UINT,C.POINTER(W.UINT),C.c_void_p,C.POINTER(W.UINT),C.c_void_p,C.c_void_p]
    u.DisplayConfigGetDeviceInfo.argtypes=[C.POINTER(Header)]
    for _ in range(3):
        if u.GetDisplayConfigBufferSizes(2,C.byref(pn),C.byref(mn)):continue
        paths=(DisplayPath*pn.value)();modes=(Mode*mn.value)()
        if not u.QueryDisplayConfig(2,C.byref(pn),paths,C.byref(mn),modes,None):break
    else:raise RuntimeError('无法读取显示器色彩状态，请停止后重新开启监控')
    results=[]
    for path in paths[:pn.value]:
        name=Name();name.header=Header(1,C.sizeof(Name),path.source.adapter,path.source.id)
        if u.DisplayConfigGetDeviceInfo(C.byref(name.header)):raise RuntimeError('无法核实显示器名称')
        color=Color2();color.header=Header(15,C.sizeof(Color2),path.target.adapter,path.target.id)
        cr=u.DisplayConfigGetDeviceInfo(C.byref(color.header))
        white=White();white.header=Header(11,C.sizeof(White),path.target.adapter,path.target.id)
        wr=u.DisplayConfigGetDeviceInfo(C.byref(white.header))
        if cr:
            # Old API: Advanced Color can include WCG. Use the color-safe native path
            # whenever enabled, rather than assuming that 10-bit transport means HDR.
            color.header.type=9;color.header.size=32
            if u.DisplayConfigGetDeviceInfo(C.byref(color.header)):raise RuntimeError('无法核实HDR状态')
            active='advanced' if color.flags&2 else 'SDR'
        else:active={0:'SDR',1:'WCG',2:'HDR'}.get(color.mode,'unknown')
        if active=='unknown':raise RuntimeError('未知的显示器色彩模式')
        results.append((name.name,active,white.value if wr==0 else None,color.bpc))
    return tuple(sorted(results))

class AutoCapture:
    """Construction is lazy: DXGI initialization, grab and close share one thread."""
    def __init__(self):
        self.raw=None;self.dll=None;self.handle=None;self.state=None;self.checked=-100.
        self.generation=0;self.metadata={};self.last_qpc=0;self.native_signature=None
        self.frequency=C.c_int64();C.windll.kernel32.QueryPerformanceFrequency(C.byref(self.frequency))
        # CPython perf_counter and DXGI LastPresentTime use the same QPC clock.
        self.route=None;self.closed=False

    def _close_backend(self):
        if self.handle:self.dll.id486_hdr_close(self.handle);self.handle=None
        if self.raw:self.raw.release();self.raw=None

    def _configure(self):
        now=time.perf_counter()
        state=display_state();self.checked=now
        if state==self.state:return False
        self._close_backend();self.state=state;self.generation+=1;self.last_qpc=0;self.native_signature=None
        # Capture coordinates are already physical desktop coordinates. The native
        # path refuses a region spanning outputs rather than mixing frame clocks.
        self.route='hdr_static' if any(s[1]!='SDR' for s in state) else 'sdr_dxcam'
        if self.route=='hdr_static':
            base=Path(getattr(sys,'_MEIPASS',Path(__file__).resolve().parent))
            self.dll=C.CDLL(str(base/'assets/capture/ID486HdrCapture.dll'))
            self.dll.id486_hdr_open.restype=C.c_void_p
            self.dll.id486_hdr_grab.argtypes=[C.c_void_p,C.c_int32,C.c_int32,C.c_uint32,C.c_uint32,C.c_void_p,C.c_size_t,C.POINTER(NativeInfo)]
            self.dll.id486_hdr_grab.restype=C.c_int32
            self.dll.id486_hdr_close.argtypes=[C.c_void_p]
            self.dll.id486_hdr_error.argtypes=[C.c_void_p,C.c_size_t]
            self.handle=self.dll.id486_hdr_open()
            if not self.handle:raise RuntimeError(self._error())
        else:
            import dxcam
            self.raw=dxcam.create(output_color='BGRA')
        self.metadata=dict(backend=self.route,configuration_generation=self.generation,displays=self.state,cached=True)
        return True

    def _error(self):
        error=C.create_string_buffer(2048);self.dll.id486_hdr_error(error,len(error))
        return 'HDR采集失败：'+error.value.decode('utf-8',errors='replace')

    def grab(self,region,copy=True):
        if self.closed:raise RuntimeError('采集已关闭')
        self._configure()
        x,y,x2,y2=region;stamp=time.perf_counter()
        if self.handle:
            image=np.empty((y2-y,x2-x,3),dtype=np.uint8);info=NativeInfo()
            result=self.dll.id486_hdr_grab(self.handle,x,y,x2-x,y2-y,image.ctypes.data,image.nbytes,C.byref(info))
            if result<0:raise RuntimeError(self._error())
            if not result:
                self.metadata.update(cached=True);return None
            if info.abi!=1 or info.size!=C.sizeof(NativeInfo):raise RuntimeError('HDR采集接口版本不匹配')
            signature=(info.generation,info.hdr,info.white,info.format)
            if self.native_signature is not None and self.native_signature!=signature:self.generation+=1
            self.native_signature=signature
            qpc=info.qpc;source=qpc/self.frequency.value
            self.metadata.update(source_format='rgba16f' if info.format==16 else 'rgba8',white_scale=info.white,hdr=bool(info.hdr))
        else:
            image=self.raw.grab(region=region,copy=True)
            if image is None:self.metadata.update(cached=True);return None
            qpc=getattr(getattr(self.raw,'_duplicator',None),'latest_frame_ticks',0)
            if not qpc:raise RuntimeError('SDR采集缺少源帧时间戳')
            source=qpc/self.frequency.value
            # Contiguous BGR avoids repeated implicit channel/stride conversions.
            image=np.ascontiguousarray(image[:,:,:3])
        # A mode/white-level switch can occur during GPU readback. Reject that
        # mixed-configuration frame as well as the frame before the next poll.
        if display_state()!=self.state:
            self._configure();return None
        if qpc<=self.last_qpc:
            self.metadata.update(cached=True);return None
        self.last_qpc=qpc
        self.metadata.update(source_qpc=qpc,source_monotonic=source,cached=False,
                             configuration_generation=self.generation,backend=self.route)
        if source>time.perf_counter()+.005 or stamp-source>.2:return None
        return image

    def release(self):
        self._close_backend();self.closed=True
