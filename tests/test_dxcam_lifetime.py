import sys
import types
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from dxcam_lifetime import release_surface,release_duplicator

class Resource:
    def __init__(self):self.refs=0;self.releases=0

class Pointer:
    def __init__(self,resource):self.resource=resource;resource.refs+=1
    def Release(self):self.resource.refs-=1;self.resource.releases+=1
    def __del__(self):self.Release()

class LifetimeTests(unittest.TestCase):
    def test_two_owned_interfaces_released_once_each(self):
        r=Resource();s=types.SimpleNamespace(interface=Pointer(r),texture=Pointer(r),width=20,height=10)
        release_surface(s)
        self.assertEqual((r.refs,r.releases),(0,2))
        self.assertEqual((s.width,s.height),(0,0))
        release_surface(s);self.assertEqual(r.releases,2)

    def test_other_owner_remains_valid(self):
        r=Resource();held=Pointer(r)
        s=types.SimpleNamespace(interface=Pointer(r),texture=Pointer(r),width=20,height=10)
        release_surface(s);self.assertEqual(r.refs,1)
        del held;self.assertEqual((r.refs,r.releases),(0,3))

    def test_partial_creation_still_cleans_interface(self):
        r=Resource();s=types.SimpleNamespace(interface=Pointer(r),texture=None,width=20,height=10)
        release_surface(s);self.assertEqual((r.refs,r.releases),(0,1))

    def test_frame_return_precedes_pointer_cleanup(self):
        r=Resource();calls=[]
        d=types.SimpleNamespace(duplicator=Pointer(r),texture=Pointer(r),_frame_held=True)
        d.release_frame=lambda:calls.append(r.refs)
        release_duplicator(d)
        self.assertEqual(calls,[2]);self.assertEqual((r.refs,r.releases),(0,2))
        self.assertFalse(d._frame_held)
        release_duplicator(d);self.assertEqual(calls,[2]);self.assertEqual(r.releases,2)

    def test_frame_return_error_still_drops_owned_references(self):
        r=Resource()
        def fail():raise RuntimeError('device lost')
        d=types.SimpleNamespace(duplicator=Pointer(r),texture=Pointer(r),_frame_held=True,release_frame=fail)
        with self.assertRaises(RuntimeError):release_duplicator(d)
        self.assertEqual((r.refs,r.releases),(0,2));self.assertFalse(d._frame_held)

if __name__=='__main__':unittest.main()
