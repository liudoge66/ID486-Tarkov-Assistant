// Route discovery and image-scoped game/verified-UU isolation.
// No remote node addresses are hard-coded.
using System;
using System.Collections.Generic;
using System.Collections.Concurrent;
using System.IO;
using System.Net;
using System.Runtime.InteropServices;
using System.Text;
using System.Diagnostics;

partial class NetworkBroker {
    [DllImport("iphlpapi.dll")] static extern uint GetExtendedTcpTable(IntPtr table,ref uint size,bool ordered,uint family,int kind,uint reserved);
    [DllImport("fwpuclnt.dll")] static extern uint FwpmFilterDeleteById0(IntPtr handle,ulong id);
    static readonly Guid RemotePort=new Guid("c35a604d-d22b-4e1a-91b4-68f674ee674b"),Protocol=new Guid("3971ef2b-623e-4f9a-8cb1-6e79b806b9a7");
    class TcpRow {public int pid,state;public string local,remote;public ushort localPort,remotePort;}
    class ProxyRoute {public string ip,path;public ushort port;public int pid;public string Key {get{return ip+"/"+port;}}}
    static Dictionary<string,ProxyRoute> proxyRoutes=new Dictionary<string,ProxyRoute>();
    static bool externalDiagnostic=false;
    static bool fullProcessScope=true,limitedDiagnostic=false;
    static readonly Guid Receive4=new Guid("e1cd9fe7-f4b5-4273-96c0-592e487b8650"),Receive6=new Guid("a3b42c97-9f04-4672-b87e-cee9c483257f");
    static readonly Guid FlowFlags=new Guid("632ce23b-5167-435c-86d7-e903684aa80c");
    static ConcurrentQueue<string> dnsResults=new ConcurrentQueue<string>();
    static long lastRouteRefresh=0,lastDnsRefresh=0;static int dnsPending=0;
    static ushort Port(byte[] data,int offset) {return (ushort)((data[offset]<<8)|data[offset+1]);}
    static string AddressText(byte[] data,int offset,int count) {byte[] raw=new byte[count];Array.Copy(data,offset,raw,0,count);return new IPAddress(raw).ToString();}
    static List<TcpRow> TcpRows() {
        var result=new List<TcpRow>();
        foreach(uint family in new uint[]{2,23})for(int attempt=0;attempt<3;attempt++) {
            uint size=0,code=GetExtendedTcpTable(IntPtr.Zero,ref size,false,family,5,0);
            if(code!=0 && code!=122)throw new InvalidOperationException("TCP table size error "+code);
            IntPtr buffer=Marshal.AllocHGlobal((int)size);
            try {
                code=GetExtendedTcpTable(buffer,ref size,false,family,5,0);
                if(code==122)continue;if(code!=0)throw new InvalidOperationException("TCP table error "+code);
                byte[] data=new byte[size];Marshal.Copy(buffer,data,0,(int)size);
                int count=BitConverter.ToInt32(data,0),rowSize=family==2?24:56;
                if(count<0 || 4L+(long)count*rowSize>data.Length)throw new InvalidOperationException("Invalid TCP table size");
                for(int i=0;i<count;i++) {
                    int o=4+i*rowSize;var row=new TcpRow();
                    if(family==2) {row.state=BitConverter.ToInt32(data,o);row.local=AddressText(data,o+4,4);row.localPort=Port(data,o+8);row.remote=AddressText(data,o+12,4);row.remotePort=Port(data,o+16);row.pid=BitConverter.ToInt32(data,o+20);}
                    else {row.local=AddressText(data,o,16);row.localPort=Port(data,o+20);row.remote=AddressText(data,o+24,16);row.remotePort=Port(data,o+44);row.state=BitConverter.ToInt32(data,o+48);row.pid=BitConverter.ToInt32(data,o+52);}
                    result.Add(row);
                }
                break;
            } finally {Marshal.FreeHGlobal(buffer);}
        }
        return result;
    }
    static string PeerPath(int pid) {
        IntPtr handle=OpenProcess(0x1000,false,pid);if(handle==IntPtr.Zero)return PeerName(pid);
        try {var text=new StringBuilder(32768);int size=text.Capacity;return QueryFullProcessImageName(handle,0,text,ref size)?text.ToString():PeerName(pid);}
        finally {CloseHandle(handle);}
    }
    static string PeerName(int pid) {try {using(var process=Process.GetProcessById(pid))return process.ProcessName+".exe";}catch{return "";}}
    static bool SupportedProxy(string path) {return Path.GetFileName(path).Equals("uu.exe",StringComparison.OrdinalIgnoreCase);}
    static List<ProxyRoute> FindProxyRoutes(List<TcpRow> rows,int game,Func<int,string> image) {
        var found=new Dictionary<string,ProxyRoute>();
        foreach(var client in rows) {
            if(client.pid!=game || client.state!=5 || !IPAddress.IsLoopback(IPAddress.Parse(client.remote)))continue;
            foreach(var server in rows) {
                if(server.pid==game || server.state!=5 || server.localPort!=client.remotePort || server.remotePort!=client.localPort || server.local!=client.remote || server.remote!=client.local)continue;
                string path=image(server.pid);if(!SupportedProxy(path))continue;
                var route=new ProxyRoute {ip=client.remote,port=client.remotePort,pid=server.pid,path=path};found[route.Key]=route;
            }
        }
        if(found.Count>16)throw new InvalidOperationException("Too many game proxy endpoints");
        return new List<ProxyRoute>(found.Values);
    }
    static bool PeerStillOwnsPort(ProxyRoute route,List<TcpRow> rows) {
        string image=PeerPath(route.pid);if(!SupportedProxy(image) || !String.Equals(image,route.path,StringComparison.OrdinalIgnoreCase))return false;
        foreach(var row in rows)if(row.pid==route.pid && row.localPort==route.port && (row.state==2 || row.state==5) &&
            (row.local==route.ip || row.local=="0.0.0.0" || row.local=="::"))return true;
        return false;
    }
    static bool PublicAddress(IPAddress ip) {
        if(IPAddress.IsLoopback(ip) || ip.Equals(IPAddress.Any) || ip.Equals(IPAddress.IPv6Any))return false;
        var b=ip.GetAddressBytes();
        if(b.Length==4)return b[0]!=0 && b[0]!=10 && b[0]!=127 && b[0]<224 && !(b[0]==169 && b[1]==254) &&
            !(b[0]==172 && b[1]>=16 && b[1]<=31) && !(b[0]==192 && (b[1]==168 || b[1]==0)) &&
            !(b[0]==100 && b[1]>=64 && b[1]<=127) && !(b[0]==198 && (b[1]==18 || b[1]==19));
        return !ip.IsIPv6LinkLocal && !ip.IsIPv6Multicast && !ip.IsIPv6SiteLocal && (b[0]&254)!=252 && !(b[0]==0 && b[1]==0);
    }
    static void StartDnsRefresh(long now) {
        if(dnsPending!=0 || (lastDnsRefresh!=0 && (now-lastDnsRefresh)/(double)Stopwatch.Frequency<30))return;
        lastDnsRefresh=now;System.Threading.Interlocked.Exchange(ref dnsPending,1);
        try {ResolveDomainBatch();}finally {System.Threading.Interlocked.Exchange(ref dnsPending,0);}
    }
    static bool HasDestination(string ip,ushort port,string identity="") {
        lock(ruleLock)foreach(var rule in activeRules.Values)if(Convert.ToString(rule["remote_ip"])==ip &&
            (rule.ContainsKey("remote_port")?Convert.ToUInt16(rule["remote_port"]):(ushort)0)==port &&
            (identity=="" || (rule.ContainsKey("identity_kind") && Convert.ToString(rule["identity_kind"])==identity)))return true;
        return false;
    }
    static void AddDestination(IntPtr app,string executable,string text,ProxyRoute proxy) {
        ushort port=proxy==null?(ushort)0:proxy.port;
        foreach(bool original in new bool[]{false,true}) {
        string identity=original?"original_game":"current_game";if(HasDestination(text,port,identity))continue;
        IPAddress ip=IPAddress.Parse(text);if(proxy==null && !PublicAddress(ip))throw new ArgumentException("Unverified public destination");
        if(proxy!=null && (!IPAddress.IsLoopback(ip) || port==0 || !SupportedProxy(proxy.path)))throw new ArgumentException("Unverified proxy endpoint");
        int size=Marshal.SizeOf(typeof(Condition));IntPtr conditions=Marshal.AllocHGlobal(size*4),v6=IntPtr.Zero;
        try {
            Condition c=new Condition();c.key=original?OriginalAppId:AppId;c.value.type=12;c.value.value.pointer=app;Marshal.StructureToPtr(c,conditions,false);
            c=new Condition();c.key=Remote;byte[] raw=ip.GetAddressBytes();
            if(raw.Length==4) {c.value.type=3;c.value.value.number=((uint)raw[0]<<24)|((uint)raw[1]<<16)|((uint)raw[2]<<8)|raw[3];}
            else {v6=Marshal.AllocHGlobal(16);Marshal.Copy(raw,0,v6,16);c.value.type=11;c.value.value.pointer=v6;}
            Marshal.StructureToPtr(c,new IntPtr(conditions.ToInt64()+size),false);
            if(proxy!=null) {
                c=new Condition();c.key=RemotePort;c.value.type=2;c.value.value.number=port;Marshal.StructureToPtr(c,new IntPtr(conditions.ToInt64()+size*2),false);
                c=new Condition();c.key=Protocol;c.value.type=1;c.value.value.small=6;Marshal.StructureToPtr(c,new IntPtr(conditions.ToInt64()+size*3),false);
            }
            Filter f=new Filter();f.display.name=proxy==null?"ID486 Fence: game backend":"ID486 Fence: game to verified UU endpoint";
            f.layer=raw.Length==4?Layer4:Layer6;f.count=proxy==null?(uint)2:4;f.conditions=conditions;f.action.type=0x1001;ApplyPolicy(ref f);
            ulong id;uint added=FwpmFilterAdd0(engine,ref f,IntPtr.Zero,out id);
            if(added!=0 && original) {Audit("original_identity_rule_unavailable",new Dictionary<string,object>{{"wfp_error",added},{"target_ip",text}});continue;}
            Check(added);
            var record=new Dictionary<string,object>{{"filter_id",id.ToString()},{"remote_ip",ip.ToString()},{"ip_version",raw.Length==4?4:6},{"layer",f.layer.ToString()},
                {"program",executable},{"action","block"},{"identity_kind",identity},{"domain_evidence",DomainEvidence(text)},
                {"route_kind",proxy==null?"domain_address_coverage":"verified_uu_loopback"}};
            if(proxy!=null) {record["remote_port"]=port;record["ip_protocol"]=6;record["proxy_pid"]=proxy.pid;record["proxy_program"]=proxy.path;record["proxy_identity_source"]=Path.IsPathRooted(proxy.path)?"image_path_and_socket_pair":"process_name_and_socket_pair";record["proxy_scope"]="all_game_traffic_on_this_verified_local_proxy_endpoint";proxyRoutes[proxy.Key]=proxy;}
            lock(ruleLock)activeRules[id]=record;
        } finally {Marshal.FreeHGlobal(conditions);if(v6!=IntPtr.Zero)Marshal.FreeHGlobal(v6);}
        }
    }
    static void AddCurrentProxyDestinations(IntPtr app,string executable) {
        var routes=FindProxyRoutes(TcpRows(),boundPid,PeerPath);
        foreach(var route in routes)AddDestination(app,executable,route.ip,route);
        Audit("route_discovery",new Dictionary<string,object>{{"proxy_endpoint_count",routes.Count},{"policy","game_image_plus_verified_proxy_ip_tcp_port"},{"self_ipc_excluded",true}});
    }
    static void AddGameExternalControl(IntPtr app,string executable) {
        int size=Marshal.SizeOf(typeof(Condition));IntPtr conditions=Marshal.AllocHGlobal(size*2);
        try {foreach(bool original in new bool[]{false,true})foreach(Guid layer in new Guid[]{Layer4,Layer6}) {
            Condition c=new Condition();c.key=original?OriginalAppId:AppId;c.value.type=12;c.value.value.pointer=app;Marshal.StructureToPtr(c,conditions,false);
            c=new Condition();c.key=FlowFlags;c.match=8;c.value.type=3;c.value.value.number=1;Marshal.StructureToPtr(c,new IntPtr(conditions.ToInt64()+size),false);
            Filter f=new Filter();f.display.name="ID486 Fence: 15s game external diagnostic";f.layer=layer;f.count=2;f.conditions=conditions;f.action.type=0x1001;ApplyPolicy(ref f);
            ulong id;uint added=FwpmFilterAdd0(engine,ref f,IntPtr.Zero,out id);
            if(added!=0 && original) {Audit("original_identity_rule_unavailable",new Dictionary<string,object>{{"wfp_error",added},{"scope","game_external_control"}});continue;}
            Check(added);lock(ruleLock)activeRules[id]=new Dictionary<string,object>{{"filter_id",id.ToString()},{"remote_ip","any_non_loopback"},{"program",executable},
                {"action","block"},{"identity_kind",original?"original_game":"current_game"},{"route_kind","diagnostic_game_external"},{"loopback_excluded",true},{"maximum_seconds",15}};
        }} finally {Marshal.FreeHGlobal(conditions);}
    }
    static int AddGameFullNetwork(IntPtr app,string executable,bool inspectOnly=false,bool uu=false) {
        int count=0;
        if(!inspectOnly)expectedImageIds[executable]=AppBlob(app);
        IntPtr condition=Marshal.AllocHGlobal(Marshal.SizeOf(typeof(Condition)));
        try {foreach(bool original in new bool[]{false,true})foreach(Guid layer in new[]{Layer4,Layer6,Receive4,Receive6}) {
            // These receive layers rejected OriginalAppId in the actual logs.
            // Current game identity still covers both receive layers.
            if(original && (layer==Receive4 || layer==Receive6))continue;
            // Deliberately no address, port, protocol or loopback exception.
            Condition c=new Condition();c.key=original?OriginalAppId:AppId;c.value.type=12;c.value.value.pointer=app;Marshal.StructureToPtr(c,condition,false);
            Filter f=new Filter();f.display.name="ID486 Fence: full game process network block";f.layer=layer;f.count=1;f.conditions=condition;f.action.type=0x1001;ApplyPolicy(ref f);
            if(inspectOnly) {
                Condition actual=(Condition)Marshal.PtrToStructure(condition,typeof(Condition));
                if(f.count!=1 || actual.key!=(original?OriginalAppId:AppId) || actual.match!=0 || actual.value.type!=12 || actual.value.value.pointer!=app || f.action.type!=0x1001 || f.sublayer==Universal || f.sublayer!=policySubLayer || f.provider==IntPtr.Zero || (f.flags&8)==0)throw new Exception("Full block must use own policy and identity only, no traffic exceptions");
                count++;continue;
            }
            ulong id;uint added=FwpmFilterAdd0(engine,ref f,IntPtr.Zero,out id);
            if(added!=0 && original) {Audit("original_identity_rule_unavailable",new Dictionary<string,object>{{"wfp_error",added},{"scope","game_all_network"},{"layer",layer.ToString()}});continue;}
            Check(added);count++;lock(ruleLock)activeRules[id]=new Dictionary<string,object>{{"filter_id",id.ToString()},{"remote_ip","any_including_loopback"},{"program",executable},
                {"action","block"},{"identity_kind",uu?(original?"original_uu":"current_uu"):(original?"original_game":"current_game")},{"route_kind",uu?"uu_process_full_network":"game_process_full_network"},{"layer",layer.ToString()},
                {"direction",layer==Layer4 || layer==Layer6?"connect":"receive_accept"},{"loopback_excluded",false},{"protocol_scope","all"}};
        }} finally {Marshal.FreeHGlobal(condition);}
        return count;
    }
    static void RefreshRoutes(long now) {
        if(fullProcessScope)return; // Full game rules need neither DNS nor proxy discovery.
        if(!externalDiagnostic)StartDnsRefresh(now);
        if(engine==IntPtr.Zero) {
            string cached;while(dnsResults.TryDequeue(out cached))if(verifiedTargets.Count<64)verifiedTargets.Add(cached);
            return;
        }
        if(lastRouteRefresh!=0 && (now-lastRouteRefresh)/(double)Stopwatch.Frequency<.5)return;
        lastRouteRefresh=now;
        List<TcpRow> rows;List<ProxyRoute> routes;
        try {rows=TcpRows();routes=FindProxyRoutes(rows,boundPid,PeerPath);}catch(Exception e) {Audit("route_discovery_error",new Dictionary<string,object>{{"reason",e.Message}});return;}
        var added=new List<string>();string address;
        while(dnsResults.TryDequeue(out address))if(verifiedTargets.Count<64 && verifiedTargets.Add(address))added.Add(address);
        var stale=new List<string>();foreach(var item in proxyRoutes)if(!PeerStillOwnsPort(item.Value,rows))stale.Add(item.Key);
        bool changes=added.Count>0 || stale.Count>0;foreach(var route in routes)if(!HasDestination(route.ip,route.port))changes=true;
        if(!changes)return;
        IntPtr app=IntPtr.Zero;
        try {
            Check(FwpmGetAppIdFromFileName0(programPath,out app));Check(FwpmTransactionBegin0(engine,0));
            foreach(string key in stale) {
                var route=proxyRoutes[key];var ids=new List<ulong>();lock(ruleLock)foreach(var rule in activeRules)if(Convert.ToString(rule.Value["remote_ip"])==route.ip && rule.Value.ContainsKey("remote_port") && Convert.ToUInt16(rule.Value["remote_port"])==route.port)ids.Add(rule.Key);
                foreach(ulong id in ids) {Check(FwpmFilterDeleteById0(engine,id));lock(ruleLock)activeRules.Remove(id);}proxyRoutes.Remove(key);
            }
            if(!externalDiagnostic)foreach(string ip in added)AddDestination(app,programPath,ip,null);
            foreach(var route in routes)AddDestination(app,programPath,route.ip,route);
            Check(FwpmTransactionCommit0(engine));
            Audit("route_filters_updated",new Dictionary<string,object>{{"new_backend_ips",added},{"removed_stale_proxy_endpoints",stale},{"active_rules",Rules()},{"lease_extended",false}});
            Status(deathHeld?"death_hold":"isolated","route_filters_updated",Rules().Count);
        } catch {FwpmTransactionAbort0(engine);throw;}
        finally {if(app!=IntPtr.Zero)FwpmFreeMemory0(ref app);}
    }
    static void RouteSelfCheck() {
        var rows=new List<TcpRow>{
            new TcpRow{pid=10,state=5,local="127.0.0.1",localPort=40000,remote="127.0.0.1",remotePort=1111},
            new TcpRow{pid=20,state=5,local="127.0.0.1",localPort=1111,remote="127.0.0.1",remotePort=40000},
            new TcpRow{pid=10,state=5,local="127.0.0.1",localPort=41000,remote="127.0.0.1",remotePort=41001},
            new TcpRow{pid=10,state=5,local="127.0.0.1",localPort=41001,remote="127.0.0.1",remotePort=41000},
            new TcpRow{pid=30,state=5,local="127.0.0.1",localPort=42000,remote="127.0.0.1",remotePort=1111},
            new TcpRow{pid=20,state=5,local="127.0.0.1",localPort=1111,remote="127.0.0.1",remotePort=42000}};
        Func<int,string> identity=delegate(int pid){return pid==20?@"C:\NetEase\UU\uu.exe":@"C:\other.exe";};
        var result=FindProxyRoutes(rows,10,identity);if(result.Count!=1 || result[0].port!=1111 || result[0].pid!=20)throw new Exception("UU route scoping failed");
        rows[0].remotePort=2222;rows[1].localPort=2222;
        result=FindProxyRoutes(rows,10,identity);if(result.Count!=1 || result[0].port!=2222)throw new Exception("Dynamic UU port failed");
        if(FindProxyRoutes(rows,10,delegate(int pid){return @"C:\unknown.exe";}).Count!=0)throw new Exception("Unknown peer incorrectly trusted");
        if(FindProxyRoutes(new List<TcpRow>(),10,identity).Count!=0)throw new Exception("No-UU path failed");
        if(Port(new byte[]{0x04,0x57},0)!=1111 || PublicAddress(IPAddress.Parse("127.0.0.1")) || !PublicAddress(IPAddress.Parse("104.18.7.148")))throw new Exception("Route byte order/address classification failed");
    }
}
