"""One DXGI producer, immutable latest frames for slow and fast consumers."""
from dataclasses import dataclass
import threading,time


@dataclass(frozen=True)
class CaptureFrame:
    sequence:int
    captured_at:float
    wall_ns:int
    region:tuple
    guard_epoch:int
    safe:bool
    image:object
    capture_ms:float=0.
    provenance:object=None


class SharedCapture:
    def __init__(self,camera,hz,full_region,safety):
        self.camera=camera;self.hz=hz;self.full=tuple(full_region);self.requested=self.full
        self.safety=safety;self.epoch=0;self.full_until=0.;self.latest=None
        self.delivered=None;self.error=None;self.closed=threading.Event();self.fresh_after=-1.
        self.configuration_generation=None;self.configuration={}
        self.sequence=0
        self.request_serial=1;self.completed_serial=0
        self.lock=threading.Lock();self.changed=threading.Condition(self.lock)
        self.thread=threading.Thread(target=self._run,name='SharedGameCapture',daemon=True)
        self.thread.start()

    def _run(self):
        due=time.perf_counter()
        try:
            while not self.closed.is_set():
                with self.changed:
                    burst=time.perf_counter()<self.full_until
                    if not burst and self.request_serial==self.completed_serial:
                        self.changed.wait(timeout=.05);continue
                    region=self.full if burst else self.requested;hz=self.hz;serial=self.request_serial
                safe=bool(self.safety())
                if not safe:
                    with self.lock:self.epoch+=1
                    self.closed.wait(1/max(1,hz));continue
                with self.lock:epoch=self.epoch
                stamp=time.perf_counter();wall=time.time_ns()
                # Only this thread touches the DXGI camera. Never publish the
                # reusable copy=False buffer to concurrent image consumers.
                image=self.camera.grab(region=region,copy=True)
                metadata=dict(getattr(self.camera,'metadata',{}) or {})
                generation=metadata.get('configuration_generation')
                with self.lock:
                    if generation is not None and generation!=self.configuration_generation:
                        if self.configuration_generation is not None:self.epoch+=1;image=None;self.latest=None
                        self.configuration_generation=generation
                    self.configuration=metadata
                with self.lock:self.completed_serial=serial
                if not self.safety():
                    with self.lock:self.epoch+=1
                    image=None
                if image is not None:
                    source=metadata.get('source_monotonic',stamp)
                    if metadata.get('cached') or source>time.perf_counter()+.005 or time.perf_counter()-source>.2:
                        image=None
                if image is not None:
                    image.flags.writeable=False
                    with self.changed:
                        if self.epoch==epoch and source>self.fresh_after:
                            self.sequence+=1;seq=self.sequence
                            self.latest=CaptureFrame(seq,source,wall+int((source-stamp)*1e9),region,epoch,safe,image,(time.perf_counter()-stamp)*1000,metadata)
                            self.changed.notify_all()
                due=max(due+1/max(1,hz),time.perf_counter())
                self.closed.wait(max(0.,due-time.perf_counter()))
        except Exception as exc:
            with self.changed:self.error=exc;self.changed.notify_all()
        finally:
            try:self.camera.release()
            except Exception:pass

    def set_hz(self,hz):
        with self.lock:self.hz=hz

    def invalidate(self):
        with self.lock:self.epoch+=1

    def set_full_region(self,region):
        with self.lock:self.full=tuple(region)

    def request_full(self,seconds=1.):
        with self.changed:
            self.full_until=max(self.full_until,time.perf_counter()+seconds);self.changed.notify_all()

    def request_fresh_full(self,seconds=.35):
        """Refresh observation scope, without inventing a safety-state change.

        Reject cached/in-flight images captured before this request. Genuine
        owner/geometry/colour invalidations still advance epoch independently.
        """
        with self.changed:
            self.fresh_after=max(self.fresh_after,time.perf_counter())
            self.full_until=max(self.full_until,self.fresh_after+seconds)
            self.changed.notify_all()

    def stop_full_burst(self):
        with self.lock:self.full_until=0.

    def grab(self,region=None,copy=False):
        region=tuple(region or self.full)
        with self.changed:
            self.requested=region;self.request_serial+=1;self.changed.notify_all()
            def ready():
                f=self.latest
                if self.closed.is_set() or self.error:return True
                if f is None or (self.delivered and f.sequence==self.delivered.sequence):return False
                if f.guard_epoch!=self.epoch:return False
                if f.captured_at<=self.fresh_after:return False
                x,y,x2,y2=region;sx,sy,sx2,sy2=f.region
                return time.perf_counter()-f.captured_at<=.08 and x>=sx and y>=sy and x2<=sx2 and y2<=sy2
            self.changed.wait_for(ready,timeout=.1)
            frame=self.latest;error=self.error
        if error:raise RuntimeError('独立采集线程失败') from error
        if frame is None or (self.delivered and frame.sequence==self.delivered.sequence):return None
        if frame.guard_epoch!=self.epoch:return None
        if frame.captured_at<=self.fresh_after:return None
        if time.perf_counter()-frame.captured_at>.08:return None
        if not frame.safe:
            self.delivered=frame;return None
        x,y,x2,y2=region;sx,sy,sx2,sy2=frame.region
        if x<sx or y<sy or x2>sx2 or y2>sy2:return None
        self.delivered=frame
        view=frame.image[y-sy:y2-sy,x-sx:x2-sx]
        return view.copy() if copy else view

    def next_frame(self,after_sequence,timeout=.05):
        with self.changed:
            self.changed.wait_for(lambda:self.closed.is_set() or self.error or
                (self.latest and self.latest.sequence>after_sequence),timeout)
            return self.latest if self.latest and self.latest.sequence>after_sequence else None

    @property
    def capture_time(self):
        return self.delivered.captured_at if self.delivered else time.perf_counter()

    def release(self):
        self.closed.set()
        with self.changed:self.changed.notify_all()
        if threading.current_thread() is not self.thread:self.thread.join(timeout=1.)
        if self.thread.is_alive() and threading.current_thread() is not self.thread:
            raise RuntimeError('采集线程尚未退出，请停止后重新开启')
