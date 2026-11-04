// UU identities are discovered while online. No signature verification, DNS,
// process enumeration or accelerator control is performed on the cutoff path.
using System;
using System.IO;
using System.Diagnostics;
using System.Collections.Generic;
using System.Runtime.InteropServices;
using System.Security.Cryptography.X509Certificates;
using System.Threading;

partial class NetworkBroker {
    [StructLayout(LayoutKind.Sequential,CharSet=CharSet.Unicode)] struct TrustFile {
        public uint size;public string path;public IntPtr file,subject;
    }
    [StructLayout(LayoutKind.Sequential)] struct TrustData {
        public uint size;public IntPtr policy,sip;public uint ui,revoke,choice;
        public IntPtr file;public uint state;public IntPtr stateData,url;
        public uint flags,context;public IntPtr signature;
    }
    [DllImport("wintrust.dll",ExactSpelling=true)] static extern int WinVerifyTrust(IntPtr window,ref Guid action,ref TrustData data);
    sealed class UuImage {
        public string path;public int pid;public long started,modified,length;
    }
    static readonly object uuLock=new object();
    static List<UuImage> uuImages=new List<UuImage>();
    static Dictionary<string,string> expectedImageIds=new Dictionary<string,string>(StringComparer.OrdinalIgnoreCase);
    static bool uuIsolationRequested=false,uuDiscoveryComplete=false;
    static int uuScanPending=0,uuUnverified=0,expectedFullRuleCount=6,uuIsolatedImages=0,uuSkippedImages=0;
    static long uuLastScan=0;

    static bool IsUuName(string name) {
        name=Path.GetFileNameWithoutExtension(name).ToLowerInvariant();
        return name=="uu" || name.StartsWith("uu_");
    }
    static bool UuRoot(string path) {
        var directory=new FileInfo(path).Directory;
        for(int n=0;directory!=null && n<5;n++,directory=directory.Parent)
            if(directory.Name.Equals("UU",StringComparison.OrdinalIgnoreCase) || directory.Name=="UU加速器")return true;
        return false;
    }
    static bool SignedByNetEase(string path) {
        IntPtr memory=IntPtr.Zero;var data=new TrustData();
        try {
            var file=new TrustFile();file.size=(uint)Marshal.SizeOf(typeof(TrustFile));file.path=path;
            memory=Marshal.AllocHGlobal(Marshal.SizeOf(typeof(TrustFile)));Marshal.StructureToPtr(file,memory,false);
            data.size=(uint)Marshal.SizeOf(typeof(TrustData));data.ui=2;data.choice=1;data.file=memory;
            data.state=1;data.flags=0x1000; // Offline/cache-only validation, no trust-store changes.
            Guid action=new Guid("00aac56b-cd44-11d0-8cc2-00c04fc295ee");
            if(WinVerifyTrust(IntPtr.Zero,ref action,ref data)!=0)return false;
            var certificate=new X509Certificate2(X509Certificate.CreateFromSignedFile(path));
            string subject=certificate.Subject;
            return subject.IndexOf("NetEase",StringComparison.OrdinalIgnoreCase)>=0 &&
                subject.IndexOf("Hangzhou",StringComparison.OrdinalIgnoreCase)>=0;
        } catch {return false;}
        finally {
            if(data.stateData!=IntPtr.Zero) {data.state=2;Guid action=new Guid("00aac56b-cd44-11d0-8cc2-00c04fc295ee");WinVerifyTrust(IntPtr.Zero,ref action,ref data);}
            if(memory!=IntPtr.Zero) {Marshal.DestroyStructure(memory,typeof(TrustFile));Marshal.FreeHGlobal(memory);}
        }
    }
    static void RequestUuScan(long now) {
        if(!uuIsolationRequested || deathRequested || (uuLastScan!=0 && (now-uuLastScan)/(double)Stopwatch.Frequency<2))return;
        if(Interlocked.CompareExchange(ref uuScanPending,1,0)!=0)return;
        uuLastScan=now;
        ThreadPool.QueueUserWorkItem(delegate {
            var found=new List<UuImage>();int rejected=0;bool complete=true;
            try {
                List<UuImage> cached;lock(uuLock)cached=new List<UuImage>(uuImages);
                foreach(var process in Process.GetProcesses())using(process) {
                    try {
                        if(!IsUuName(process.ProcessName))continue;
                        string path=PeerPath(process.Id);
                        if(!Path.IsPathRooted(path) || !UuRoot(path)) {rejected++;continue;}
                        var file=new FileInfo(path);long started=process.StartTime.ToUniversalTime().Ticks;
                        bool verified=cached.Exists(x=>x.pid==process.Id && x.started==started && x.path==path && x.modified==file.LastWriteTimeUtc.Ticks && x.length==file.Length);
                        if(!verified && !SignedByNetEase(path)) {rejected++;continue;}
                        found.Add(new UuImage {path=path,pid=process.Id,started=started,modified=file.LastWriteTimeUtc.Ticks,length=file.Length});
                    } catch {rejected++;}
                }
            } catch {complete=false;}
            lock(uuLock) {uuImages=found;uuUnverified=rejected;uuDiscoveryComplete=complete;}
            Audit("uu_identity_inventory",new Dictionary<string,object>{{"verified_images",found.Count},{"unverified_candidates",rejected},{"complete",complete},{"blocking",false}});
            Interlocked.Exchange(ref uuScanPending,0);
        });
    }
    static void UuSelfCheck() {
        if(Marshal.SizeOf(typeof(TrustFile))!=32 || Marshal.SizeOf(typeof(TrustData))!=88)throw new Exception("UU signature ABI mismatch");
        if(!IsUuName("uu.exe") || !IsUuName("uu_neths_helper.exe") || IsUuName("EscapeFromTarkov.exe") || IsUuName("mihomo.exe"))throw new Exception("UU identity allowlist failed");
        if(!UuRoot(@"C:\Apps\UU\5253\x64\uu_neths_helper.exe") || UuRoot(@"C:\Windows\uu.exe"))throw new Exception("UU root boundary failed");
        if(SignedByNetEase(Path.Combine(Path.GetTempPath(),"nonexistent-uu-identity-46.exe")))throw new Exception("Missing image trusted");
    }
    static void AddVerifiedUuFilters() {
        uuIsolatedImages=0;uuSkippedImages=0;
        if(!uuIsolationRequested)return;
        List<UuImage> images;lock(uuLock)images=new List<UuImage>(uuImages);
        var paths=new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        foreach(var image in images) {
            try {
                using(var process=Process.GetProcessById(image.pid)) {
                    var file=new FileInfo(image.path);
                    if(process.HasExited || process.StartTime.ToUniversalTime().Ticks!=image.started ||
                       !String.Equals(PeerPath(image.pid),image.path,StringComparison.OrdinalIgnoreCase) ||
                       file.LastWriteTimeUtc.Ticks!=image.modified || file.Length!=image.length) {
                        uuSkippedImages++;
                        Audit("uu_identity_changed",new Dictionary<string,object>{{"pid",image.pid},{"blocked",false}});continue;
                    }
                }
            } catch {uuSkippedImages++;continue;}
            if(!paths.Add(image.path))continue;
            IntPtr app=IntPtr.Zero;
            try {
                Check(FwpmGetAppIdFromFileName0(image.path,out app));
                expectedFullRuleCount+=6;
                AddGameFullNetwork(app,image.path,false,true);uuIsolatedImages++;
                Audit("uu_identity_isolated",new Dictionary<string,object>{{"target_pid",image.pid},{"image",image.path},{"scope","all_uu_image_network_tcp_udp_ipv4_ipv6_loopback"},{"pending_transaction_commit",true}});
            } finally {if(app!=IntPtr.Zero)FwpmFreeMemory0(ref app);}
        }
    }
}
