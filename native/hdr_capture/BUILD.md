# ID486 native HDR capture

Pinned upstream and MIT notices are in NOTICE.txt, LICENSE-hdrcapture.txt and
LICENSE-windows-capture.txt. Cargo.lock fixes the dependency versions.

Build on Windows x64 with Rust's x86_64-pc-windows-gnu toolchain, GNU dlltool
on PATH and `RUSTFLAGS=-C linker=rust-lld -C link-self-contained=yes`:

```
cargo build --release --locked
```

Copy target/release/hdrcapture.dll to ../../assets/capture/ID486HdrCapture.dll.
The distributed DLL links only Windows system DLLs. The compiler and build
dependencies are not included in either application edition.

ID486 additions: thread-owned C ABI; LastPresentTime and cache metadata;
configuration generation; active HDR versus WCG query; static GPU mapping.
The Python adapter rejects cached/stale frames, rechecks display state around
readback, and invalidates old observation evidence when configuration changes.
