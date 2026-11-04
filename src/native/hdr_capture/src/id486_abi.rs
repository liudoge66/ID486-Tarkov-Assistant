//! ID486's thread-owned C ABI. No Python ABI or borrowed pixel buffers cross this boundary.
use crate::hdr_capture::{Capture, ToneMapping, display::Rect};
use std::{cell::RefCell, ffi::c_void, panic::{catch_unwind, AssertUnwindSafe}};
thread_local! { static ERROR: RefCell<String> = RefCell::new(String::new()); }
fn error(s:String) -> i32 { ERROR.with(|e| *e.borrow_mut()=s); -1 }
#[repr(C)]
pub struct Info { pub abi:u32, pub size:u32, pub qpc:i64, pub generation:u64,
    pub cached:u32, pub hdr:u32, pub white:f32, pub format:u32 }
#[no_mangle]
pub extern "C" fn id486_hdr_open() -> *mut c_void {
    match catch_unwind(|| Capture::with_timeout(1)) {
        Ok(Ok(c)) => Box::into_raw(Box::new(c)) as *mut c_void,
        Ok(Err(e)) => { error(e.to_string()); std::ptr::null_mut() },
        Err(_) => { error("native capture initialization panic".into()); std::ptr::null_mut() }
    }
}
#[no_mangle]
pub unsafe extern "C" fn id486_hdr_grab(p:*mut c_void,x:i32,y:i32,w:u32,h:u32,
    dst:*mut u8,len:usize,info:*mut Info) -> i32 {
    if p.is_null() || dst.is_null() || info.is_null() || w==0 || h==0 ||
        (w as usize).checked_mul(h as usize).and_then(|n|n.checked_mul(3)) != Some(len) {
        return error("invalid capture arguments".into());
    }
    match catch_unwind(AssertUnwindSafe(|| {
        let c=&mut *(p as *mut Capture);let region=Rect::new(x,y,w,h);
        if !c.monitors().iter().any(|m| !m.is_virtual_desktop && m.rect.contains(region)) {
            return Err("capture must fit within one physical output".to_string());
        }
        let f=match c.grab_region(region,1,ToneMapping::Static) {
            Ok(f)=>f,
            Err(crate::hdr_capture::Error::InitialFrameTimeout{..})=>return Ok(0),
            Err(e)=>return Err(e.to_string())
        };
        if f.monitor_info.len()!=1 {return Err("ambiguous source output".into())}
        let m=&f.monitor_info[0];
        *info=Info {abi:1,size:std::mem::size_of::<Info>() as u32,qpc:m.source_qpc,
            generation:c.generation,cached:m.cached as u32,hdr:m.hdr_enabled as u32,
            white:m.white_scale,format:if m.source_format=="rgba16f" {16}else{8}};
        if m.cached || m.source_qpc<=0 {return Ok(0)}
        let out=std::slice::from_raw_parts_mut(dst,len);
        for (s,d) in f.bgra.chunks_exact(4).zip(out.chunks_exact_mut(3)) {d.copy_from_slice(&s[..3]);}
        Ok(1)
    })) {
        Ok(Ok(status))=>status,Ok(Err(s))=>error(s),Err(_)=>error("native capture panic".into())
    }
}
#[no_mangle]
pub unsafe extern "C" fn id486_hdr_close(p:*mut c_void) {
    if !p.is_null() {drop(Box::from_raw(p as *mut Capture));}
}
#[no_mangle]
pub unsafe extern "C" fn id486_hdr_error(dst:*mut u8,len:usize) -> usize {
    if dst.is_null() || len==0 {return 0}
    ERROR.with(|e| {let e=e.borrow();let n=e.len().min(len-1);
        std::ptr::copy_nonoverlapping(e.as_ptr(),dst,n);*dst.add(n)=0;n})
}
