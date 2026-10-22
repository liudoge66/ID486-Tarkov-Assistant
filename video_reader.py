"""PTS-aware reader backed by PyAV; supports the AV1 recordings used in validation."""
import av
import cv2

class VideoReader:
    def __init__(self,path):
        self.container=av.open(str(path));self.stream=self.container.streams.video[0]
        self.frames=iter(self.container.decode(self.stream));self.origin=None;self.last_time=0.
        self.fps=float(self.stream.average_rate or self.stream.base_rate or 30)
        self.duration=float(self.container.duration or 0)/1000000
        self.count=self.stream.frames or round(self.duration*self.fps)
    def isOpened(self):return self.container is not None
    def get(self,key):
        return {cv2.CAP_PROP_FPS:self.fps,cv2.CAP_PROP_FRAME_COUNT:self.count,cv2.CAP_PROP_FRAME_WIDTH:self.stream.width,cv2.CAP_PROP_FRAME_HEIGHT:self.stream.height,cv2.CAP_PROP_POS_MSEC:self.last_time*1000}.get(key,0)
    def read(self):
        try:frame=next(self.frames)
        except StopIteration:return False,None
        stamp=float(frame.pts*frame.time_base) if frame.pts is not None else self.last_time+1/self.fps
        if self.origin is None:self.origin=stamp
        self.last_time=stamp-self.origin
        return True,frame.to_ndarray(format='bgr24')
    def release(self):
        if self.container is not None:self.container.close();self.container=None
