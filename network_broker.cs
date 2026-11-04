// Fence-only, fixed-scope elevated WFP lease owner. No persistent filters, services or drivers.
// Build with network_routes.cs; work/build_editions.py compiles both files.
using System;
using System.Collections.Generic;
using System.Collections.Concurrent;
using System.Diagnostics;
using System.IO;
using System.Net;
using System.Runtime.InteropServices;
using System.Text;
using System.Threading;
using System.Web.Script.Serialization;

partial class NetworkBroker {
    [StructLayout(LayoutKind.Sequential, CharSet=CharSet.Unicode)] struct Display { public string name,description; }
    [StructLayout(LayoutKind.Sequential)] struct Blob { public uint size; public IntPtr data; }
    [StructLayout(LayoutKind.Sequential)] struct Session {
        public Guid key; public Display display; public uint flags,timeout,pid;
        public IntPtr sid,username; public int kernel;
    }
    [StructLayout(LayoutKind.Explicit,Size=8)] struct ValueUnion {
        [FieldOffset(0)] public IntPtr pointer;
        [FieldOffset(0)] public uint number;
        [FieldOffset(0)] public byte small;
    }
    [StructLayout(LayoutKind.Sequential)] struct Value { public uint type; public ValueUnion value; }
    [StructLayout(LayoutKind.Sequential)] struct Condition { public Guid key; public uint match; public Value value; }
    [StructLayout(LayoutKind.Sequential)] struct Action { public uint type; public Guid key; }
    [StructLayout(LayoutKind.Explicit,Size=16)] struct Context { [FieldOffset(0)] public ulong raw; }
    [StructLayout(LayoutKind.Sequential)] struct Filter {
        public Guid key; public Display display; public uint flags; public IntPtr provider;
        public Blob data; public Guid layer,sublayer; public Value weight; public uint count;
        public IntPtr conditions; public Action action; public Context context; public IntPtr reserved;
        public ulong id; public Value effectiveWeight;
    }
    [DllImport("fwpuclnt.dll",CharSet=CharSet.Unicode)] static extern uint FwpmEngineOpen0(string server,uint auth,IntPtr identity,ref Session session,out IntPtr engine);
    [DllImport("fwpuclnt.dll")] static extern uint FwpmEngineClose0(IntPtr engine);
    [DllImport("fwpuclnt.dll",CharSet=CharSet.Unicode)] static extern uint FwpmGetAppIdFromFileName0(string file,out IntPtr blob);
    [DllImport("fwpuclnt.dll")] static extern void FwpmFreeMemory0(ref IntPtr p);
    [DllImport("fwpuclnt.dll")] static extern uint FwpmFilterAdd0(IntPtr engine,ref Filter filter,IntPtr sd,out ulong id);
    [DllImport("fwpuclnt.dll")] static extern uint FwpmTransactionBegin0(IntPtr engine,uint flags);
    [DllImport("fwpuclnt.dll")] static extern uint FwpmTransactionCommit0(IntPtr engine);
    [DllImport("fwpuclnt.dll")] static extern uint FwpmTransactionAbort0(IntPtr engine);
    [DllImport("kernel32.dll")] static extern IntPtr OpenProcess(uint access,bool inherit,int pid);
    [DllImport("kernel32.dll")] static extern uint WaitForSingleObject(IntPtr handle,uint ms);
    [DllImport("kernel32.dll",CharSet=CharSet.Unicode)] static extern IntPtr OpenEvent(uint access,bool inherit,string name);
    [DllImport("kernel32.dll")] static extern bool CloseHandle(IntPtr handle);
    [DllImport("kernel32.dll",CharSet=CharSet.Unicode)] static extern bool QueryFullProcessImageName(IntPtr handle,uint flags,StringBuilder name,ref int size);
    [StructLayout(LayoutKind.Explicit,Size=16)] struct Address { [FieldOffset(0)] public uint v4; }
    [StructLayout(LayoutKind.Sequential)] struct EventHeader2 {
        public ulong time; public uint flags,version; public byte protocol;
        public Address local,remote; public ushort localPort,remotePort; public uint scope;
        public Blob app; public IntPtr user; public uint family; public IntPtr package;
    }
    [StructLayout(LayoutKind.Sequential)] struct NetEvent2 { public EventHeader2 header; public uint type; public IntPtr data; }
    [StructLayout(LayoutKind.Sequential)] struct DropPrefix {
        public ulong filterId;public ushort layerId;public uint reauth,originalProfile,currentProfile,direction;public int loopback;
    }
    [StructLayout(LayoutKind.Sequential)] struct EventTemplate { public ulong start,end;public uint count;public IntPtr conditions; }
    [StructLayout(LayoutKind.Sequential)] struct Subscription { public IntPtr template;public uint flags;public Guid session; }
    [UnmanagedFunctionPointer(CallingConvention.Winapi)] delegate void NetCallback(IntPtr context,IntPtr data);
    [DllImport("fwpuclnt.dll")] static extern uint FwpmEngineGetOption0(IntPtr engine,uint option,out IntPtr value);
    [DllImport("fwpuclnt.dll")] static extern uint FwpmNetEventSubscribe1(IntPtr engine,ref Subscription subscription,NetCallback callback,IntPtr context,out IntPtr events);
    [DllImport("fwpuclnt.dll")] static extern uint FwpmNetEventUnsubscribe0(IntPtr engine,IntPtr events);
    static JavaScriptSerializer json=new JavaScriptSerializer();
    static string folder; static IntPtr engine=IntPtr.Zero;
    static PreparedIsolation prepared=new PreparedIsolation();
    static IntPtr commandWake=IntPtr.Zero;
    static long deadline=0; static string lastCommand="",lastRegularCommand=""; static long statusSerial=0;
    static string pendingStatusJson=null;static long lastIpcWarning=0;
    static bool deathHeld=false;
    static bool deathRequested=false;static string urgentSeen="";
    static bool endRequested=false;static string endHoldCommand="";
    static HashSet<string> verifiedTargets=new HashSet<string>();
    static readonly object auditWriteLock=new object();
    static long auditSequence=0;static int boundPid=0;static string programPath="",leaseId="";
    static readonly object ruleLock=new object();
    static Dictionary<ulong,Dictionary<string,object>> activeRules=new Dictionary<ulong,Dictionary<string,object>>();
    static ConcurrentQueue<Dictionary<string,object>> drops=new ConcurrentQueue<Dictionary<string,object>>();
    static long dropCount=0,gameDropCount=0,uuDropCount=0,queueLoss=0,callbackErrors=0;static bool monitorAvailable=false;static string monitorReason="not_started";
    static IntPtr eventSubscription=IntPtr.Zero;static NetCallback callback=OnNetEvent;
    static readonly Guid AppId=new Guid("d78e1e87-8644-4ea5-9437-d809ecefc971"), Remote=new Guid("b235ae9a-1d64-49b8-a44c-5ff3d9095045");
    static readonly Guid Layer4=new Guid("c38d57d1-05a7-4c33-904f-7fbceee60e82"),Layer6=new Guid("4a72393b-319f-44bc-84c3-ba54dcb3b6b4");
    static readonly Guid Universal=new Guid("eebecc03-ced4-4380-819a-2734397b2b74");
    static void Check(uint code) { if(code!=0)throw new InvalidOperationException("WFP error "+code); }
    static List<Dictionary<string,object>> Rules() {
        lock(ruleLock)return new List<Dictionary<string,object>>(activeRules.Values);
    }
    static void Audit(string kind,Dictionary<string,object> data) {
        if(folder==null)return;
        try {
            var entry=new Dictionary<string,object>(data);entry["event"]=kind;entry["sequence"]=Interlocked.Increment(ref auditSequence);
            if(!entry.ContainsKey("timestamp"))entry["timestamp"]=DateTimeOffset.Now.ToString("o");
            entry["recorded_timestamp"]=DateTimeOffset.Now.ToString("o");entry["command_id"]=lastCommand;
            if(!entry.ContainsKey("isolation_command_id"))entry["isolation_command_id"]=leaseId;
            entry["bound_game_pid"]=boundPid;entry["program"]=programPath;
            lock(auditWriteLock)File.AppendAllText(Path.Combine(folder,"events.jsonl"),json.Serialize(entry)+"\n",Encoding.UTF8);
        } catch(IOException) {} catch(UnauthorizedAccessException) {}
    }
    static void OnNetEvent(IntPtr context,IntPtr pointer) {
        try {
            NetEvent2 e=(NetEvent2)Marshal.PtrToStructure(pointer,typeof(NetEvent2));
            if(e.type!=3 || e.data==IntPtr.Zero)return;
            DropPrefix drop=(DropPrefix)Marshal.PtrToStructure(e.data,typeof(DropPrefix));
            Dictionary<string,object> rule;
            lock(ruleLock) {if(!activeRules.TryGetValue(drop.filterId,out rule))return;}
            Interlocked.Increment(ref dropCount);
            if(rule.ContainsKey("route_kind") && Convert.ToString(rule["route_kind"])=="uu_process_full_network")Interlocked.Increment(ref uuDropCount);else Interlocked.Increment(ref gameDropCount);
            var record=new Dictionary<string,object>{{"filter_id",drop.filterId.ToString()},
                {"target_ip",rule["remote_ip"]},{"layer_id",drop.layerId},{"reauthorization_reason",drop.reauth},
                {"direction",drop.direction},{"event_type","FWPM_NET_EVENT_TYPE_CLASSIFY_DROP"},
                {"pid_source","bound_process_identity_not_event_payload"},
                {"isolation_command_id",leaseId},
                {"timestamp",new DateTimeOffset(DateTime.FromFileTimeUtc((long)e.header.time)).ToLocalTime().ToString("o")}};
            if(rule.ContainsKey("program"))record["rule_program"]=rule["program"];
            foreach(string key in new string[]{"identity_kind","route_kind","domain_evidence"})if(rule.ContainsKey(key))record[key]=rule[key];
            if(e.header.app.data!=IntPtr.Zero && e.header.app.size>0 && e.header.app.size<=65536)
                record["observed_app_id"]=Marshal.PtrToStringUni(e.header.app.data,(int)e.header.app.size/2).TrimEnd('\0');
            if((e.header.flags&1)!=0)record["ip_protocol"]=e.header.protocol;
            if((e.header.flags&4)!=0) {record["ip_version"]=e.header.version;if(e.header.version==0)record["remote_ipv4_u32"]=e.header.remote.v4;}
            if((e.header.flags&16)!=0)record["destination_port"]=e.header.remotePort;
            if(drops.Count>=512) {Interlocked.Increment(ref queueLoss);return;}
            drops.Enqueue(record);
        } catch {Interlocked.Increment(ref callbackErrors);}
    }
    static void DrainHits(int maximum) {
        Dictionary<string,object> item;
        for(int i=0;i<maximum && drops.TryDequeue(out item);i++)Audit("classify_drop",item);
    }
    static void StartHitMonitor(IntPtr app) {
        monitorAvailable=false;monitorReason="unavailable";dropCount=gameDropCount=uuDropCount=queueLoss=callbackErrors=0;
        IntPtr option=IntPtr.Zero,condition=IntPtr.Zero,template=IntPtr.Zero;
        try {
            uint code=FwpmEngineGetOption0(engine,0,out option);
            if(code!=0) {monitorReason="engine_option_error_"+code;return;}
            Value value=(Value)Marshal.PtrToStructure(option,typeof(Value));
            if(value.value.number==0) {monitorReason="system_event_collection_disabled";return;}
            EventTemplate t=new EventTemplate();t.start=(ulong)DateTime.UtcNow.AddMinutes(-1).ToFileTimeUtc();
            // A proxied event's effective app may be UU. Retain ONLY events whose
            // filter ID belongs to our game/original-game rules in OnNetEvent.
            t.end=(ulong)DateTime.SpecifyKind(DateTime.MaxValue,DateTimeKind.Utc).ToFileTimeUtc();t.count=0;t.conditions=IntPtr.Zero;
            template=Marshal.AllocHGlobal(Marshal.SizeOf(typeof(EventTemplate)));Marshal.StructureToPtr(t,template,false);
            Subscription sub=new Subscription();sub.template=template;
            code=FwpmNetEventSubscribe1(engine,ref sub,callback,IntPtr.Zero,out eventSubscription);
            monitorAvailable=code==0;monitorReason=monitorAvailable?"subscribed_to_game_drop_events":"subscription_error_"+code;
        } catch(Exception e) {monitorReason=e.GetType().Name;}
        finally {
            if(option!=IntPtr.Zero)FwpmFreeMemory0(ref option);
            if(condition!=IntPtr.Zero)Marshal.FreeHGlobal(condition);
            if(template!=IntPtr.Zero)Marshal.FreeHGlobal(template);
            Audit("hit_monitor",new Dictionary<string,object>{{"available",monitorAvailable},{"reason",monitorReason},
                {"global_audit_settings_changed",false},{"scope","only_our_current_and_original_game_filter_ids"},
                {"count_unit","observed_connection_authorization_drop_events_not_packets"}});
        }
    }
    static void StopHitMonitor() {
        if(eventSubscription!=IntPtr.Zero && engine!=IntPtr.Zero) {
            uint code=FwpmNetEventUnsubscribe0(engine,eventSubscription);eventSubscription=IntPtr.Zero;
            if(code!=0)Audit("hit_monitor",new Dictionary<string,object>{{"unsubscribe_error",code}});
        }
    }
    static void Status(string state,string reason,int count) {
        var item=new Dictionary<string,object>{{"state",state},{"reason",reason},{"filter_count",count},{"command_id",lastCommand},{"serial",++statusSerial},{"timestamp",DateTimeOffset.Now.ToString("o")}};
        item["resources_prepared"]=prepared.Ready;
        item["active_rules"]=Rules();item["hit_monitor_available"]=monitorAvailable;
        item["isolation_command_id"]=leaseId;item["death_hold"]=deathHeld;
        item["hold_mode"]=deathRequested?"CONFIRMED_DEATH":endRequested?"END_LATCHED":engine!=IntPtr.Zero?"TEMP_ISOLATED":"ARMED";
        item["scope"]=fullProcessScope?"game_all_network":externalDiagnostic?"game_external_control":"backend_domains";
        item["policy"]=PolicySummary();
        item["observed_drop_events"]=monitorAvailable?(object)Interlocked.Read(ref dropCount):null;
        Audit("broker_state",item);
        pendingStatusJson=json.Serialize(item);FlushStatus();
    }
    static void FlushStatus() {
        if(pendingStatusJson==null)return;
        string tmp=Path.Combine(folder,"status.tmp"),path=Path.Combine(folder,"status.json");
        try {
            File.WriteAllText(tmp,pendingStatusJson,Encoding.UTF8);
            if(File.Exists(path))File.Replace(tmp,path,null);else File.Move(tmp,path);
            pendingStatusJson=null;
        } catch(IOException e) {IpcWarning("status_write_deferred",e.Message);}
          catch(UnauthorizedAccessException e) {IpcWarning("status_write_deferred",e.Message);}
    }
    static void IpcWarning(string kind,string reason) {
        long now=Stopwatch.GetTimestamp();if(lastIpcWarning!=0 && (now-lastIpcWarning)/(double)Stopwatch.Frequency<2)return;
        lastIpcWarning=now;Audit(kind,new Dictionary<string,object>{{"reason",reason},{"rules_retained",engine!=IntPtr.Zero},{"death_hold",deathHeld}});
    }
    static string ReadShared(string path) {
        using(var input=new FileStream(path,FileMode.Open,FileAccess.Read,FileShare.ReadWrite|FileShare.Delete))
        using(var reader=new StreamReader(input,Encoding.UTF8,true))return reader.ReadToEnd();
    }
    static Dictionary<string,object> ReadIpc(string path) {
        try {return json.Deserialize<Dictionary<string,object>>(ReadShared(path));}
        catch(IOException e) {IpcWarning("ipc_read_retry",e.Message);return null;}
        catch(UnauthorizedAccessException e) {IpcWarning("ipc_read_retry",e.Message);return null;}
        catch(ArgumentException e) {IpcWarning("ipc_decode_retry",e.Message);return null;}
    }
    static bool AcceptRegular(Dictionary<string,object> data,out string action) {
        action=Convert.ToString(data["action"]);string id=Convert.ToString(data["id"]);
        // Consumed command IDs belong to their IPC channel, not the last audit.
        if(id==lastRegularCommand)return false;
        lastRegularCommand=id;
        if(action=="stop") {lastCommand=id;return true;}
        if(deathRequested && !(action=="isolate" && !fullProcessScope && engine==IntPtr.Zero && verifiedTargets.Count==0)) {
            Audit("regular_command_ignored_after_death",new Dictionary<string,object>{{"ignored_command_id",id},{"action",action}});return false;
        }
        if(endRequested && action!="revoke_end_hold")return false;
        DateTimeOffset created=DateTimeOffset.Parse(Convert.ToString(data["created"]));
        if(Math.Abs((DateTimeOffset.UtcNow-created).TotalSeconds)>3) {
            Audit("stale_command_ignored",new Dictionary<string,object>{{"ignored_command_id",id},{"action",action},{"rules_unchanged",true}});return false;
        }
        lastCommand=id;return true;
    }
    static bool CanRevokeEnd(Dictionary<string,object> data) {
        return !deathRequested && endRequested && engine!=IntPtr.Zero &&
            data.ContainsKey("hold_command_id") && Convert.ToString(data["hold_command_id"])==endHoldCommand &&
            data.ContainsKey("isolation_command_id") && Convert.ToString(data["isolation_command_id"])==leaseId;
    }
    static bool TryRevokeEnd(Dictionary<string,object> data,string executable) {
        ReadUrgentDeath(executable);
        if(!CanRevokeEnd(data)) {
            Audit("end_hold_revoke_rejected",new Dictionary<string,object>{{"rules_retained",engine!=IntPtr.Zero}});return false;
        }
        endRequested=false;endHoldCommand="";
        lastCommand=Convert.ToString(data["id"]);Restore("end_hold_revoked");return true;
    }
    static void Restore(string reason) {
        prepared.Clear(ClosePreparedEngine,FreePreparedApp);
        List<Dictionary<string,object>> removed=Rules();
        if(engine!=IntPtr.Zero) {
            StopHitMonitor();
            uint result=FwpmEngineClose0(engine);
            if(result!=0)throw new InvalidOperationException("WFP close error "+result);
            engine=IntPtr.Zero;
        }
        DrainHits(2048);
        if(removed.Count>0) {
            Audit("rules_removed",new Dictionary<string,object>{{"reason",reason},{"removed_rules",removed},{"filter_count",0}});
            Audit("hit_summary",new Dictionary<string,object>{{"available",monitorAvailable},{"reason",monitorReason},
                {"observed_drop_events",monitorAvailable?(object)Interlocked.Read(ref dropCount):null},{"game_drop_events",monitorAvailable?(object)Interlocked.Read(ref gameDropCount):null},{"uu_drop_events",monitorAvailable?(object)Interlocked.Read(ref uuDropCount):null},
                {"sample_queue_losses",Interlocked.Read(ref queueLoss)},{"callback_errors",Interlocked.Read(ref callbackErrors)},
                {"count_unit","connection_authorization_events_not_packets"},{"coverage","observed_callbacks_only"}});
        }
        lock(ruleLock)activeRules.Clear();expectedImageIds.Clear();expectedFullRuleCount=6;uuIsolatedImages=0;proxyRoutes.Clear();monitorAvailable=false;externalDiagnostic=false;
        if(policyRegistered)Audit("policy_context_released",new Dictionary<string,object>{{"provider_key",policyProvider.ToString()},{"sublayer_key",policySubLayer.ToString()},{"dynamic_engine_closed",true},{"persistent",false}});
        policyRegistered=false;policyReadback=false;verifiedFilterCount=0;ReleasePolicyMemory();
        deadline=0;deathHeld=false;Status("online",reason,0);leaseId="";
    }
    static IntPtr OpenPreparedEngine() {
        Session session=new Session();session.flags=1;session.timeout=1000;session.display.name="ID486 empty prepared game session";
        IntPtr handle;Check(FwpmEngineOpen0(null,10,IntPtr.Zero,ref session,out handle));return handle;
    }
    static IntPtr PreparedApp(string executable) {
        IntPtr app;Check(FwpmGetAppIdFromFileName0(executable,out app));return app;
    }
    static void ClosePreparedEngine(IntPtr handle) {Check(FwpmEngineClose0(handle));}
    static void FreePreparedApp(IntPtr app) {FwpmFreeMemory0(ref app);}
    static void PrepareResources(string executable) {
        RequestUuScan(Stopwatch.GetTimestamp());
        if(deathRequested || engine!=IntPtr.Zero || limitedDiagnostic)return;
        try {
            bool created=prepared.Prepare(executable,boundPid,OpenPreparedEngine,PreparedApp,ClosePreparedEngine,FreePreparedApp);
            Audit("resources_prepared",new Dictionary<string,object>{{"created",created},{"ready",prepared.Ready},{"filter_count",0},{"blocking",false}});
            Status("online","resources_prepared",0);
        } catch(Exception e) {
            Audit("preparation_error",new Dictionary<string,object>{{"reason",e.Message},{"blocking",false}});
            Status("online","prepare_failed",0);
        }
    }
    static void Isolate(string executable,string[] addresses,double seconds) {
        if(engine!=IntPtr.Zero) { Status("isolated","lease_not_extended",addresses.Length);return; }
        if(seconds<1 || seconds>170)throw new ArgumentException("Lease must be 1..170 seconds");
        if((addresses.Length==0 && !externalDiagnostic && !fullProcessScope) || addresses.Length>64)throw new ArgumentException("Empty/oversized address set");
        if(limitedDiagnostic && seconds>15)throw new ArgumentException("Diagnostic cannot exceed15 seconds");
        leaseId=lastCommand;
        Session session=new Session();session.flags=1;session.timeout=1000;session.display.name="ID486 temporary game isolation";
        IntPtr app=IntPtr.Zero,conditions=IntPtr.Zero;
        // Start the independent monotonic lease BEFORE adding any object. No repeat command can renew it.
        deadline=Stopwatch.GetTimestamp()+(long)(seconds*Stopwatch.Frequency);
        try {
            bool reused=prepared.Take(executable,boundPid,out engine,out app);
            if(!reused) {
                Check(FwpmEngineOpen0(null,10,IntPtr.Zero,ref session,out engine));
                Check(FwpmGetAppIdFromFileName0(executable,out app));
            }
            StartHitMonitor(app); // Optional diagnostics; never changes global audit options.
            Check(FwpmTransactionBegin0(engine,0));
            CreatePolicy();expectedImageIds.Clear();expectedFullRuleCount=6;uuIsolatedImages=0;
            if(fullProcessScope) {AddGameFullNetwork(app,executable);AddVerifiedUuFilters();}
            else if(externalDiagnostic)AddGameExternalControl(app,executable);
            else foreach(string address in addresses)AddDestination(app,executable,address,null);
            if(!fullProcessScope)AddCurrentProxyDestinations(app,executable);
            var commitStartedWall=DateTimeOffset.Now;long commitStarted=Stopwatch.GetTimestamp();
            Check(FwpmTransactionCommit0(engine));
            var commitFinishedWall=DateTimeOffset.Now;long commitFinished=Stopwatch.GetTimestamp();
            Audit("prepared_resources_consumed",new Dictionary<string,object>{{"reused",reused},{"filter_count_before_commit",0}});
            Audit("filter_transaction_committed",new Dictionary<string,object>{{"commit_started_timestamp",commitStartedWall.ToString("o")},
                {"commit_finished_timestamp",commitFinishedWall.ToString("o")},
                {"commit_ms",(commitFinished-commitStarted)*1000.0/Stopwatch.Frequency},
                {"meaning","API_commit_completion_before_policy_readback"}});
            try {AuditPolicyReadback(app);}catch(Exception e) {policyReadback=false;Audit("policy_audit_error",new Dictionary<string,object>{{"reason",e.Message},{"rules_retained",true}});}
            Audit("rules_committed",new Dictionary<string,object>{{"domain","gw-pve.escapefromtarkov.com"},
                {"program",executable},{"maximum_seconds",seconds},{"rules",Rules()},
                {"scope",fullProcessScope?"game_all_network":externalDiagnostic?"game_external_control":"backend_domain_keyword"},
                {"meaning","policy_committed_actual_hits_are_separate_events"}});
            if(deathRequested) {deathHeld=true;deadline=0;Status("death_hold","death_priority_filters_committed",Rules().Count);}
            else Status("isolated","filters_committed",Rules().Count);
            try {if(!deathRequested)AuditArbitration(app);}catch(Exception e) {Audit("arbitration_audit_error",new Dictionary<string,object>{{"reason",e.Message},{"rules_retained",true}});}
        } catch {
            if(engine!=IntPtr.Zero) {FwpmTransactionAbort0(engine);StopHitMonitor();FwpmEngineClose0(engine);engine=IntPtr.Zero;}
            lock(ruleLock)activeRules.Clear();
            policyRegistered=false;policyReadback=false;verifiedFilterCount=0;ReleasePolicyMemory();
            deadline=0;throw;
        } finally {
            if(conditions!=IntPtr.Zero)Marshal.FreeHGlobal(conditions);
            if(app!=IntPtr.Zero)FwpmFreeMemory0(ref app);
        }
    }
    static bool CanHoldDeathLease(bool active,string current,string requested,long expires,long now) {
        return !limitedDiagnostic && active && current!="" && current==requested && (expires==0 || now<expires);
    }
    static bool ExternalScope(string scope,bool diagnostic,double seconds) {
        if(scope!="backend_domains" && scope!="game_external_control" && scope!="game_all_network")throw new ArgumentException("Unknown isolation scope");
        bool external=scope=="game_external_control";
        if(diagnostic && (seconds<=0 || seconds>15))throw new ArgumentException("Diagnostic cannot exceed15 seconds");
        if(external && (!diagnostic || seconds<=0 || seconds>15))throw new ArgumentException("External block requires explicit15s diagnostic");
        return external;
    }
    static void ReadUrgentDeath(string executable) {
        string path=Path.Combine(folder,"urgent_command.json");
        if(!File.Exists(path))return;
        var d=ReadIpc(path);if(d==null)return;
        string id=Convert.ToString(d["id"]);
        if(id==urgentSeen && !(deathRequested && !deathHeld && engine==IntPtr.Zero && (verifiedTargets.Count>0 || fullProcessScope)))return;
        urgentSeen=id;
        string urgentAction=Convert.ToString(d["action"]);
        if(urgentAction!="death_cutoff" && urgentAction!="end_hold")return;
        if(urgentAction=="end_hold" && deathRequested)return;
        if(limitedDiagnostic) {Audit("diagnostic_death_hold_ignored",new Dictionary<string,object>{{"maximum_seconds",15}});return;}
        if(Math.Abs((DateTimeOffset.UtcNow-DateTimeOffset.Parse(Convert.ToString(d["created"])) ).TotalSeconds)>3)return;
        string requestedScope=d.ContainsKey("scope")?Convert.ToString(d["scope"]):(fullProcessScope?"game_all_network":"backend_domains");
        if(requestedScope!="game_all_network" && requestedScope!="backend_domains")return;
        fullProcessScope=requestedScope=="game_all_network";
        if(urgentAction=="death_cutoff")deathRequested=true;
        else {endRequested=true;endHoldCommand=id;}
        lastCommand=id;
        Audit(urgentAction=="end_hold"?"end_hold_received":"death_cutoff_received",new Dictionary<string,object>{{"already_isolated",engine!=IntPtr.Zero},
            {"cached_target_count",verifiedTargets.Count}});
        if(engine==IntPtr.Zero) {
            if(!fullProcessScope && verifiedTargets.Count==0) {Status("online","death_cutoff_no_verified_targets",0);return;}
            // Reuse only addresses previously DNS-verified by THIS broker/game
            // session. No DNS, heartbeat or HTTP request on the death path.
            Isolate(executable,new List<string>(verifiedTargets).ToArray(),170);
        }
        deathHeld=true;deadline=0;
        Status("death_hold","death_priority_rules_retained_until_exit",Rules().Count);
    }
    static int Main(string[] args) {
        // A marshaling-only probe; never calls WFP or changes networking.
        if(args.Length==2 && args[0]=="--self-check") {
            RouteSelfCheck();DomainSelfCheck();PolicySelfCheck();UuSelfCheck();
            if(AddGameFullNetwork(new IntPtr(123),"selfcheck-no-api",true)!=6)throw new Exception("Expected four current-app and two supported original-app filters");
            if(!ExternalScope("game_external_control",true,15) || ExternalScope("backend_domains",false,170))throw new Exception("Diagnostic scope validation failed");
            if(ExternalScope("game_all_network",false,170))throw new Exception("Full process scope is not the legacy external diagnostic");
            bool longProbeRejected=false;try {ExternalScope("game_all_network",true,16);}catch(ArgumentException){longProbeRejected=true;}
            if(!longProbeRejected)throw new Exception("Full process diagnostic cannot exceed15 seconds");
            PreparedIsolation.SelfCheck();
            foreach(bool confirmed in new bool[]{false,true}) {
                bool refused=false;try {ExternalScope("game_external_control",confirmed,confirmed?16:15);}catch(ArgumentException){refused=true;}
                if(!refused)throw new Exception("External control must require explicit diagnostic and15s limit");
            }
            if(!CanHoldDeathLease(true,"a","a",200,100) ||
               !CanHoldDeathLease(true,"a","a",0,300) ||
               CanHoldDeathLease(false,"a","a",200,100) ||
               CanHoldDeathLease(true,"a","b",200,100) ||
               CanHoldDeathLease(true,"a","a",200,200) ||
               CanHoldDeathLease(true,"a","a",200,201))throw new Exception("Death hold lease scope/expiry check failed");
            var decoded=json.Deserialize<Dictionary<string,object>>("{\"addresses\":[\"1.1.1.1\"]}");
            IntPtr dp=Marshal.AllocHGlobal(Marshal.SizeOf(typeof(DropPrefix))),ep=Marshal.AllocHGlobal(Marshal.SizeOf(typeof(NetEvent2)));
            try {
                activeRules[42]=new Dictionary<string,object>{{"remote_ip","203.0.113.7"}};
                DropPrefix drop=new DropPrefix();drop.filterId=42;Marshal.StructureToPtr(drop,dp,false);
                NetEvent2 e=new NetEvent2();e.type=3;e.data=dp;e.header.time=(ulong)DateTime.UtcNow.ToFileTimeUtc();
                e.header.flags=17;e.header.protocol=6;e.header.remotePort=443;Marshal.StructureToPtr(e,ep,false);
                OnNetEvent(IntPtr.Zero,ep);
                drop.filterId=99;Marshal.StructureToPtr(drop,dp,false);OnNetEvent(IntPtr.Zero,ep);
                if(dropCount!=1 || drops.Count!=1)throw new Exception("Drop event scope check failed");
                folder=Path.GetFullPath(args[1]+".policy");Directory.CreateDirectory(folder);
                engine=new IntPtr(1);deadline=Stopwatch.GetTimestamp()+Stopwatch.Frequency;leaseId="selfcheck-existing-lease";
                lastRegularCommand="already-executed-isolate";
                string endId=Guid.NewGuid().ToString("N");
                File.WriteAllText(Path.Combine(folder,"urgent_command.json"),json.Serialize(new {id=endId,action="end_hold",created=DateTimeOffset.UtcNow.ToString("o")}));
                ReadUrgentDeath("selfcheck-no-wfp");
                if(!endRequested || deathRequested || !deathHeld || deadline!=0)throw new Exception("End latch must remain reversible and retain lease");
                var revoke=new Dictionary<string,object>{{"hold_command_id",endId},{"isolation_command_id",leaseId}};
                if(!CanRevokeEnd(revoke))throw new Exception("Matching end lease cannot revoke");
                revoke["isolation_command_id"]="wrong-lease";
                if(CanRevokeEnd(revoke))throw new Exception("Wrong lease revoked end hold");
                revoke["isolation_command_id"]=leaseId;revoke["hold_command_id"]="old-hold";
                if(CanRevokeEnd(revoke))throw new Exception("Old end command revoked current hold");
                endRequested=false;endHoldCommand="";deathHeld=false;
                ReadUrgentDeath("selfcheck-no-wfp");
                if(endRequested || deathHeld)throw new Exception("Revoked urgent end command replayed");
                string urgentId=Guid.NewGuid().ToString("N");
                File.WriteAllText(Path.Combine(folder,"urgent_command.json"),json.Serialize(new {id=urgentId,action="death_cutoff",created=DateTimeOffset.UtcNow.ToString("o")}));
                ReadUrgentDeath("selfcheck-no-wfp");
                if(!deathHeld || deadline!=0 || lastCommand!=urgentId)throw new Exception("Urgent death IPC did not retain active lease");
                endRequested=true;endHoldCommand=endId;revoke["hold_command_id"]=endId;
                if(CanRevokeEnd(revoke))throw new Exception("Normal evidence revoked confirmed death");
                endRequested=false;
                using(var locked=new FileStream(Path.Combine(folder,"status.json"),FileMode.Open,FileAccess.Read,FileShare.None)) {
                    Status("death_hold","synthetic_snapshot_contention",Rules().Count);
                    if(!deathHeld || engine==IntPtr.Zero || pendingStatusJson==null)throw new Exception("Snapshot contention altered death protection");
                }
                FlushStatus();if(pendingStatusJson!=null)throw new Exception("Deferred snapshot was not retried");
                string ignoredAction;
                var oldCommand=new Dictionary<string,object>{{"id","already-executed-isolate"},{"action","isolate"},{"created",DateTimeOffset.UtcNow.AddSeconds(-20).ToString("o")}};
                if(AcceptRegular(oldCommand,out ignoredAction))throw new Exception("Death replayed an already consumed regular command");
                oldCommand["id"]="late-restore";oldCommand["action"]="restore";
                if(AcceptRegular(oldCommand,out ignoredAction) || !deathHeld || engine==IntPtr.Zero || deadline!=0)throw new Exception("Late regular command changed death hold");
                oldCommand["id"]="explicit-stop";oldCommand["action"]="stop";
                if(!AcceptRegular(oldCommand,out ignoredAction))throw new Exception("Explicit stop must still be accepted after death");
                externalDiagnostic=true;limitedDiagnostic=true;
                if(CanHoldDeathLease(true,"a","a",0,100))throw new Exception("External diagnostic must not accept legacy indefinite death hold");
                deathHeld=false;deadline=Stopwatch.GetTimestamp()+15*Stopwatch.Frequency;
                File.WriteAllText(Path.Combine(folder,"urgent_command.json"),json.Serialize(new {id=Guid.NewGuid().ToString("N"),action="death_cutoff",created=DateTimeOffset.UtcNow.ToString("o")}));
                ReadUrgentDeath("selfcheck-no-wfp");
                if(deathHeld || deadline==0)throw new Exception("Urgent death cannot extend external diagnostic");
                externalDiagnostic=false;limitedDiagnostic=false;
                // Fake engine is never passed to a Windows API. All WFP calls are
                // bypassed because the synthetic lease is already active.
                engine=IntPtr.Zero;
                File.WriteAllText(args[1],json.Serialize(new {Session=Marshal.SizeOf(typeof(Session)),Filter=Marshal.SizeOf(typeof(Filter)),Condition=Marshal.SizeOf(typeof(Condition)),Value=Marshal.SizeOf(typeof(Value)),Provider=Marshal.SizeOf(typeof(Provider)),SubLayer=Marshal.SizeOf(typeof(SubLayer)),AddressArrayType=decoded["addresses"].GetType().FullName,
                    EventHeader2=Marshal.SizeOf(typeof(EventHeader2)),NetEvent2=Marshal.SizeOf(typeof(NetEvent2)),
                    EventTypeOffset=Marshal.OffsetOf(typeof(NetEvent2),"type").ToInt32(),EventDataOffset=Marshal.OffsetOf(typeof(NetEvent2),"data").ToInt32(),
                    RemotePortOffset=Marshal.OffsetOf(typeof(EventHeader2),"remotePort").ToInt32(),
                    DropScopeCheck="only_own_filter_counted",ObservedSyntheticDropCount=dropCount,DeathHoldScopeAndExpiry="passed",UrgentDeathIpc="passed_no_wfp_calls",UrgentCannotReplayRegular="passed",SnapshotContentionRetainsRules="passed",FullProcessPlan="six_supported_identity_only_filters",OwnPolicyPlan="transient_provider_private_sublayer_weight"+PolicyWeight,Diagnostic15sOnly="passed",RouteScope="legacy_route_checks_passed"}));
            } finally {Marshal.FreeHGlobal(dp);Marshal.FreeHGlobal(ep);ReleasePolicyMemory();}
            return 0;
        }
        if(args.Length==3 && args[0]=="--inspect-routes") {
            int observedGame=int.Parse(args[1]);var rows=TcpRows();var routes=FindProxyRoutes(rows,observedGame,PeerPath);
            var report=new List<Dictionary<string,object>>();foreach(var route in routes)report.Add(new Dictionary<string,object>{{"ip",route.ip},{"port",route.port},{"proxy_pid",route.pid},{"proxy_program",route.path}});
            File.WriteAllText(args[2],json.Serialize(new {game_pid=observedGame,read_only=true,verified_uu_routes=report}));return 0;
        }
        if(args.Length!=3)return 2;
        IntPtr parent=IntPtr.Zero,game=IntPtr.Zero;
        try {
            folder=Path.GetFullPath(args[0]);
            if(!Directory.Exists(folder) || (File.GetAttributes(folder)&FileAttributes.ReparsePoint)!=0)return 3;
            boundPid=int.Parse(args[2]);
            parent=OpenProcess(0x100000,false,int.Parse(args[1]));game=OpenProcess(0x101000,false,int.Parse(args[2]));
            if(parent==IntPtr.Zero || game==IntPtr.Zero)return 4;
            StringBuilder buffer=new StringBuilder(32768);int capacity=buffer.Capacity;
            if(!QueryFullProcessImageName(game,0,buffer,ref capacity))return 5;
            string executable=buffer.ToString();
            if(!Path.GetFileName(executable).Equals("EscapeFromTarkov.exe",StringComparison.OrdinalIgnoreCase))return 6;
            programPath=executable;fullProcessScope=true;uuIsolationRequested=true;
            RequestUuScan(Stopwatch.GetTimestamp()); // Discovery only; no network rules while armed.
            commandWake=OpenEvent(0x00100000,false,"Local\\ID486NetworkWake_"+Path.GetFileName(folder));
            Audit("command_wake_ready",new Dictionary<string,object>{{"available",commandWake!=IntPtr.Zero}});
            Status("online","ready",0);
            long lastContact=Stopwatch.GetTimestamp();string previousPulse="";
            while(WaitForSingleObject(parent,0)==258 && WaitForSingleObject(game,0)==258) {
                long now=Stopwatch.GetTimestamp();
                ReadUrgentDeath(executable);
                RefreshRoutes(now);RequestUuScan(now);
                FlushStatus();
                if(deadline!=0 && now>=deadline)Restore("hard_deadline");
                DrainHits(16);
                string pulse=Path.Combine(folder,"pulse");
                try {if(File.Exists(pulse)) {string value=ReadShared(pulse);if(value!=previousPulse){previousPulse=value;lastContact=now;}}}
                catch(IOException e) {IpcWarning("heartbeat_read_retry",e.Message);}
                catch(UnauthorizedAccessException e) {IpcWarning("heartbeat_read_retry",e.Message);}
                if((now-lastContact)/(double)Stopwatch.Frequency>4) {Restore("parent_heartbeat_lost");return 0;}
                string command=Path.Combine(folder,"command.json");
                if(File.Exists(command)) {
                    var d=ReadIpc(command);string action;
                    if(d!=null && AcceptRegular(d,out action)) {
                        if(action=="stop") {Restore("user_stop");return 0;}
                        if(action=="revoke_end_hold") {
                            // Read the priority channel again before a reversible release.
                            TryRevokeEnd(d,executable);
                            continue;
                        }
                        if(action=="restore") {
                            if(!deathRequested)Restore(Convert.ToString(d["reason"]));
                            else Audit("automatic_restore_ignored_after_death",new Dictionary<string,object>{{"reason","death_has_priority"}});
                        }
                        else if(action=="prepare") {PrepareResources(executable);}
                        else if(action=="death_hold") {
                            if(d.ContainsKey("isolation_command_id") &&
                               CanHoldDeathLease(engine!=IntPtr.Zero,leaseId,Convert.ToString(d["isolation_command_id"]),deadline,Stopwatch.GetTimestamp())) {
                                deathHeld=true;deadline=0;
                                Status("death_hold","death_detected_retain_until_exit",Rules().Count);
                            } else {
                                // An expired lease cannot be resurrected by a delayed death event.
                                Audit("death_hold_rejected",new Dictionary<string,object>{{"reason","no_matching_active_lease"}});
                                Status(engine!=IntPtr.Zero?(deathHeld?"death_hold":"isolated"):"online","death_hold_rejected",Rules().Count);
                            }
                        }
                        else if(action=="isolate") {
                            if(deathRequested && verifiedTargets.Count>0)continue;
                            if(engine!=IntPtr.Zero) {Status("isolated","lease_not_extended",Rules().Count);continue;}
                            string scope=d.ContainsKey("scope")?Convert.ToString(d["scope"]):"backend_domains";
                            externalDiagnostic=ExternalScope(scope,d.ContainsKey("diagnostic") && Convert.ToBoolean(d["diagnostic"]),Convert.ToDouble(d["seconds"]));
                            fullProcessScope=scope=="game_all_network";limitedDiagnostic=d.ContainsKey("diagnostic") && Convert.ToBoolean(d["diagnostic"]);
                            var list=(System.Collections.ArrayList)d["addresses"];string[] ips=new string[list.Count];
                            for(int i=0;i<ips.Length;i++)ips[i]=Convert.ToString(list[i]);
                            // Input destinations must also match the fixed, verified PvE gateway's current DNS set.
                            HashSet<string> allowed=new HashSet<string>(verifiedTargets);
                            bool missing=false;foreach(string requested in ips)if(!allowed.Contains(IPAddress.Parse(requested).ToString()))missing=true;
                            if(missing)foreach(string gateway in GatewayDomains) {
                                try {foreach(var a in Dns.GetHostAddresses(gateway))if(PublicAddress(a))allowed.Add(a.ToString());}
                                catch(System.Net.Sockets.SocketException) {}
                            }
                            Audit("resolved_targets",new Dictionary<string,object>{{"domain","gw-pve.escapefromtarkov.com"},
                                {"requested_ips",ips},{"verified_dns_ips",new List<string>(allowed)},
                                {"verification_source",verifiedTargets.Count>0?"vendor_domain_dns_cache":"dns_lookup"}});
                            foreach(string ip in ips)if(!allowed.Contains(IPAddress.Parse(ip).ToString()))throw new ArgumentException("Destination not in PvE gateway DNS");
                            foreach(string ip in ips)verifiedTargets.Add(IPAddress.Parse(ip).ToString());
                            // Death may arrive while the first DNS lookup is running.
                            ReadUrgentDeath(executable);
                            if(deathRequested)continue;
                            if(d.ContainsKey("reason") && Convert.ToString(d["reason"])=="trusted_visual_candidate")
                                Audit("candidate_cutoff_received",new Dictionary<string,object>{{"command_created",Convert.ToString(d["created"])},{"seconds",Convert.ToDouble(d["seconds"])}});
                            Isolate(executable,new List<string>(verifiedTargets).ToArray(),Convert.ToDouble(d["seconds"]));
                        }
                    }
                }
                if(commandWake!=IntPtr.Zero)WaitForSingleObject(commandWake,10);else Thread.Sleep(10);
            }
            Restore("game_or_parent_exited");return 0;
        } catch(Exception e) {
            if(engine!=IntPtr.Zero) {StopHitMonitor();FwpmEngineClose0(engine);engine=IntPtr.Zero;}
            lock(ruleLock)activeRules.Clear();proxyRoutes.Clear();deadline=0;
            policyRegistered=false;policyReadback=false;verifiedFilterCount=0;ReleasePolicyMemory();
            try {Status("error",e.Message,0);}catch {} return 1;
        } finally {
            if(engine!=IntPtr.Zero) {StopHitMonitor();FwpmEngineClose0(engine);}
            prepared.Clear(ClosePreparedEngine,FreePreparedApp);
            if(commandWake!=IntPtr.Zero)CloseHandle(commandWake);
            ReleasePolicyMemory();
            if(parent!=IntPtr.Zero)CloseHandle(parent);if(game!=IntPtr.Zero)CloseHandle(game);
        }
    }
}
