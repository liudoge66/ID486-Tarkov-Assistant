using System;

// Preparation owns only an EMPTY dynamic WFP session and application-ID blob.
// It has no filter creation/commit API. The confirm path must explicitly take it.
sealed class PreparedIsolation {
    IntPtr engine=IntPtr.Zero,app=IntPtr.Zero;
    string executable="";int pid;
    public bool Ready { get { return engine!=IntPtr.Zero && app!=IntPtr.Zero; } }
    public bool Prepare(string path,int gamePid,Func<IntPtr> open,Func<string,IntPtr> identity,
                        Action<IntPtr> close,Action<IntPtr> free) {
        if(Ready && pid==gamePid && String.Equals(path,executable,StringComparison.OrdinalIgnoreCase))return false;
        Clear(close,free);
        try {
            engine=open();if(engine==IntPtr.Zero)throw new InvalidOperationException("Empty prepared session");
            app=identity(path);if(app==IntPtr.Zero)throw new InvalidOperationException("Empty prepared identity");
            executable=path;pid=gamePid;return true;
        } catch { Clear(close,free);throw; }
    }
    public bool Take(string path,int gamePid,out IntPtr session,out IntPtr identity) {
        session=identity=IntPtr.Zero;
        if(!Ready || pid!=gamePid || !String.Equals(path,executable,StringComparison.OrdinalIgnoreCase))return false;
        session=engine;identity=app;engine=app=IntPtr.Zero;executable="";pid=0;return true;
    }
    public void Clear(Action<IntPtr> close,Action<IntPtr> free) {
        IntPtr e=engine,a=app;engine=app=IntPtr.Zero;executable="";pid=0;
        try {if(a!=IntPtr.Zero)free(a);} finally {if(e!=IntPtr.Zero)close(e);}
    }
    public static void SelfCheck() {
        int opened=0,closed=0,freed=0;var p=new PreparedIsolation();
        Func<IntPtr> open=delegate {opened++;return new IntPtr(10);};
        Func<string,IntPtr> app=delegate(string s){return new IntPtr(20);};
        Action<IntPtr> close=delegate(IntPtr e){closed++;};Action<IntPtr> free=delegate(IntPtr a){freed++;};
        p.Prepare("game.exe",7,open,app,close,free);p.Prepare("GAME.EXE",7,open,app,close,free);
        if(opened!=1 || !p.Ready || closed!=0 || freed!=0)throw new Exception("Preparation must be idempotent and nonblocking");
        IntPtr e0,a0;
        if(p.Take("other.exe",7,out e0,out a0) || p.Take("game.exe",8,out e0,out a0))throw new Exception("Cross-session preparation reuse");
        if(!p.Take("game.exe",7,out e0,out a0) || p.Ready || e0!=new IntPtr(10) || a0!=new IntPtr(20))throw new Exception("Prepared ownership transfer failed");
        p.Clear(close,free);if(closed!=0 || freed!=0)throw new Exception("Transferred resources freed twice");
        p.Prepare("game.exe",7,open,app,close,free);p.Clear(close,free);
        if(closed!=1 || freed!=1)throw new Exception("Prepared cleanup failed");
        bool failed=false;
        try {p.Prepare("bad.exe",7,open,delegate(string s){throw new Exception("identity failure");},close,free);}catch {failed=true;}
        if(!failed || p.Ready || closed!=2 || freed!=1)throw new Exception("Failed preparation leaked resources");
    }
}
