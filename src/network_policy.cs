// Independently implemented dynamic WFP policy context and read-only arbitration
// audit. No third-party filters or their ACLs are modified.
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Runtime.InteropServices;

partial class NetworkBroker {
    [StructLayout(LayoutKind.Sequential)] struct Provider {
        public Guid key;public Display display;public uint flags;public Blob data;public IntPtr serviceName;
    }
    [StructLayout(LayoutKind.Sequential)] struct SubLayer {
        public Guid key;public Display display;public uint flags;public IntPtr provider;public Blob data;public ushort weight;
    }
    [DllImport("fwpuclnt.dll")] static extern uint FwpmProviderAdd0(IntPtr engine,ref Provider provider,IntPtr security);
    [DllImport("fwpuclnt.dll")] static extern uint FwpmSubLayerAdd0(IntPtr engine,ref SubLayer layer,IntPtr security);
    [DllImport("fwpuclnt.dll")] static extern uint FwpmProviderGetByKey0(IntPtr engine,ref Guid key,out IntPtr provider);
    [DllImport("fwpuclnt.dll")] static extern uint FwpmSubLayerGetByKey0(IntPtr engine,ref Guid key,out IntPtr layer);
    [DllImport("fwpuclnt.dll")] static extern uint FwpmFilterGetById0(IntPtr engine,ulong id,out IntPtr filter);
    [DllImport("fwpuclnt.dll")] static extern uint FwpmFilterCreateEnumHandle0(IntPtr engine,IntPtr template,out IntPtr enumerator);
    [DllImport("fwpuclnt.dll")] static extern uint FwpmFilterEnum0(IntPtr engine,IntPtr enumerator,uint requested,out IntPtr entries,out uint count);
    [DllImport("fwpuclnt.dll")] static extern uint FwpmFilterDestroyEnumHandle0(IntPtr engine,IntPtr enumerator);
    static Guid policyProvider=Guid.NewGuid(),policySubLayer=Guid.NewGuid();
    static IntPtr policyProviderPointer=IntPtr.Zero;
    // This Windows BFE returns 65530 when asked for 65535. Request the
    // independently observed compatible value; retain exact readback checks.
    const ushort PolicyWeight=65530;
    const uint ClearActionRight=8;
    const int ExpectedFullRules=6; // Current app:4 layers; original app:2 outbound layers.
    static bool policyRegistered=false,policyReadback=false;
    static int verifiedFilterCount=0;static long lastArbitrationAudit=0;
    static object observedSubLayerWeight=null,observedProviderFlags=null,observedSubLayerFlags=null;

    static void PreparePolicyMemory() {
        if(policyProviderPointer!=IntPtr.Zero)return;
        policyProviderPointer=Marshal.AllocHGlobal(16);Marshal.StructureToPtr(policyProvider,policyProviderPointer,false);
    }
    static Provider ProviderPlan() {
        var provider=new Provider();provider.key=policyProvider;provider.display.name="ID486 Fence transient game policy";return provider;
    }
    static SubLayer SubLayerPlan() {
        PreparePolicyMemory();var layer=new SubLayer();layer.key=policySubLayer;layer.provider=policyProviderPointer;
        layer.display.name="ID486 Fence high-priority game-only policy";layer.weight=PolicyWeight;return layer;
    }
    static void CreatePolicy() {
        ReleasePolicyMemory();policyProvider=Guid.NewGuid();policySubLayer=Guid.NewGuid();
        policyRegistered=false;policyReadback=false;verifiedFilterCount=0;
        observedSubLayerWeight=observedProviderFlags=observedSubLayerFlags=null;
        var provider=ProviderPlan();Check(FwpmProviderAdd0(engine,ref provider,IntPtr.Zero));
        var layer=SubLayerPlan();Check(FwpmSubLayerAdd0(engine,ref layer,IntPtr.Zero));policyRegistered=true;
        Audit("policy_context_created",new Dictionary<string,object>{{"provider_key",policyProvider.ToString()},{"sublayer_key",policySubLayer.ToString()},
            {"configured_sublayer_weight",PolicyWeight},{"persistent",false},{"dynamic_session_owned",true},{"pending_transaction_commit",true}});
    }
    static void ApplyPolicy(ref Filter filter) {
        PreparePolicyMemory();filter.sublayer=policySubLayer;filter.provider=policyProviderPointer;
        filter.flags|=ClearActionRight;filter.weight.type=1;filter.weight.value.small=15;
    }
    static void ReleasePolicyMemory() {
        if(policyProviderPointer!=IntPtr.Zero) {Marshal.FreeHGlobal(policyProviderPointer);policyProviderPointer=IntPtr.Zero;}
        observedSubLayerWeight=observedProviderFlags=observedSubLayerFlags=null;
    }
    static string WeightHex(Value value) {
        if(value.type==4 && value.value.pointer!=IntPtr.Zero)return "0x"+unchecked((ulong)Marshal.ReadInt64(value.value.pointer)).ToString("X16");
        if(value.type==3)return "0x"+value.value.number.ToString("X16");
        if(value.type==1)return "range_"+value.value.small;
        return "unavailable_type_"+value.type;
    }
    static Guid PointerGuid(IntPtr pointer) {return pointer==IntPtr.Zero?Guid.Empty:(Guid)Marshal.PtrToStructure(pointer,typeof(Guid));}
    static string AppBlob(IntPtr pointer) {
        if(pointer==IntPtr.Zero)return "";
        var blob=(Blob)Marshal.PtrToStructure(pointer,typeof(Blob));
        return blob.data!=IntPtr.Zero && blob.size>0 && blob.size<=65536?Marshal.PtrToStringUni(blob.data,(int)blob.size/2).TrimEnd('\0'):"";
    }
    static Dictionary<string,object> PolicySummary() {
        int count=Rules().Count;
        return new Dictionary<string,object>{{"provider_key",policyProvider.ToString()},{"sublayer_key",policySubLayer.ToString()},
            {"configured_sublayer_weight",PolicyWeight},{"registered",policyRegistered},{"readback_verified",policyReadback},
            {"observed_sublayer_weight",observedSubLayerWeight},{"observed_provider_flags",observedProviderFlags},{"observed_sublayer_flags",observedSubLayerFlags},
            {"expected_filter_count",fullProcessScope?expectedFullRuleCount:count},{"actual_filter_count",count},{"verified_filter_count",verifiedFilterCount},
            {"coverage_complete",policyReadback && verifiedFilterCount==count && (!fullProcessScope || count==expectedFullRuleCount) && (!uuIsolationRequested || (uuDiscoveryComplete && uuUnverified==0 && uuSkippedImages==0))},
            {"uu_isolation_requested",uuIsolationRequested},{"uu_discovery_complete",uuDiscoveryComplete},{"uu_unverified_candidates",uuUnverified},{"uu_isolated_images",uuIsolatedImages},{"uu_skipped_images",uuSkippedImages},
            {"persistent",false},{"meaning","deployment_readback_not_proof_of_all_traffic_blocked"}};
    }
    static void AuditPolicyReadback(IntPtr app) {
        IntPtr pointer=IntPtr.Zero;bool providerOk=false,layerOk=false;
        try {
            uint code=FwpmProviderGetByKey0(engine,ref policyProvider,out pointer);
            if(code==0) {var provider=(Provider)Marshal.PtrToStructure(pointer,typeof(Provider));observedProviderFlags=provider.flags;providerOk=provider.key==policyProvider && provider.flags==0;}
            if(pointer!=IntPtr.Zero) {FwpmFreeMemory0(ref pointer);pointer=IntPtr.Zero;}
            code=FwpmSubLayerGetByKey0(engine,ref policySubLayer,out pointer);
            if(code==0) {var layer=(SubLayer)Marshal.PtrToStructure(pointer,typeof(SubLayer));observedSubLayerWeight=layer.weight;observedSubLayerFlags=layer.flags;layerOk=layer.key==policySubLayer && layer.weight==PolicyWeight && layer.flags==0 && PointerGuid(layer.provider)==policyProvider;}
        } finally {if(pointer!=IntPtr.Zero)FwpmFreeMemory0(ref pointer);}
        policyReadback=providerOk && layerOk;verifiedFilterCount=0;string game=AppBlob(app);
        foreach(var item in Rules()) {
            ulong id=Convert.ToUInt64(item["filter_id"]);IntPtr address=IntPtr.Zero;var result=new Dictionary<string,object>(item);
            uint code=FwpmFilterGetById0(engine,id,out address);
            try {
                if(code==0) {
                    var filter=(Filter)Marshal.PtrToStructure(address,typeof(Filter));
                    bool identityOk=false;
                    if(filter.count>0 && filter.conditions!=IntPtr.Zero) {
                        var condition=(Condition)Marshal.PtrToStructure(filter.conditions,typeof(Condition));
                        Guid expected=Convert.ToString(item["identity_kind"]).StartsWith("original_",StringComparison.Ordinal)?OriginalAppId:AppId;
                        string expectedApp;string image=Convert.ToString(item["program"]);
                        if(!expectedImageIds.TryGetValue(image,out expectedApp))expectedApp=game;
                        identityOk=condition.key==expected && condition.match==0 && condition.value.type==12 && String.Equals(AppBlob(condition.value.value.pointer),expectedApp,StringComparison.OrdinalIgnoreCase);
                    }
                    bool layerMatches=!item.ContainsKey("layer") || Convert.ToString(item["layer"])==filter.layer.ToString();
                    bool verified=filter.id==id && layerMatches && identityOk && filter.sublayer==policySubLayer && PointerGuid(filter.provider)==policyProvider && filter.action.type==0x1001 && (filter.flags&ClearActionRight)!=0 && (filter.flags&0x21)==0 && filter.effectiveWeight.type==4 && filter.effectiveWeight.value.pointer!=IntPtr.Zero && (!fullProcessScope || filter.count==1);
                    result["readback_verified"]=verified;result["sublayer_key"]=filter.sublayer.ToString();result["provider_key"]=PointerGuid(filter.provider).ToString();
                    result["effective_weight"]=WeightHex(filter.effectiveWeight);result["filter_flags"]=filter.flags;result["condition_count"]=filter.count;
                    if(verified)verifiedFilterCount++;
                }else {result["readback_verified"]=false;result["readback_error"]=code;}
            } finally {if(address!=IntPtr.Zero)FwpmFreeMemory0(ref address);}
            // Publish an immutable replacement; callbacks may hold the old record.
            lock(ruleLock)activeRules[id]=result;
        }
        Audit("policy_readback",PolicySummary());
    }
    static bool PossibleGameFilter(Filter filter,string gameApp,out bool hasAppCondition) {
        hasAppCondition=false;if(filter.count>256 || (filter.count>0 && filter.conditions==IntPtr.Zero))return false;
        int size=Marshal.SizeOf(typeof(Condition));
        for(int index=0;index<filter.count;index++) {
            var condition=(Condition)Marshal.PtrToStructure(new IntPtr(filter.conditions.ToInt64()+index*size),typeof(Condition));
            if(condition.key!=AppId && condition.key!=OriginalAppId)continue;
            hasAppCondition=true;if(condition.value.type!=12)continue;
            bool equal=String.Equals(AppBlob(condition.value.value.pointer),gameApp,StringComparison.OrdinalIgnoreCase);
            if((condition.match==0 && !equal) || (condition.match==10 && equal))return false;
        }
        return true;
    }
    static int SubWeight(Guid key,Dictionary<Guid,int> cache) {
        int weight;if(cache.TryGetValue(key,out weight))return weight;IntPtr pointer=IntPtr.Zero;
        uint code=FwpmSubLayerGetByKey0(engine,ref key,out pointer);weight=-1;
        try {if(code==0)weight=((SubLayer)Marshal.PtrToStructure(pointer,typeof(SubLayer))).weight;}
        finally {if(pointer!=IntPtr.Zero)FwpmFreeMemory0(ref pointer);}
        cache[key]=weight;return weight;
    }
    static void AuditArbitration(IntPtr app) {
        // One bounded snapshot per broker lifetime, after protection is committed.
        if(lastArbitrationAudit!=0)return;lastArbitrationAudit=Stopwatch.GetTimestamp();
        var candidates=new List<Dictionary<string,object>>();var cache=new Dictionary<Guid,int>();IntPtr iterator=IntPtr.Zero;
        int scanned=0,possible=0;bool truncated=false;uint error=FwpmFilterCreateEnumHandle0(engine,IntPtr.Zero,out iterator);
        string game=AppBlob(app);long began=Stopwatch.GetTimestamp();
        try {
            if(error==0)while(scanned<8192) {
                IntPtr entries=IntPtr.Zero;uint count=0;error=FwpmFilterEnum0(engine,iterator,128,out entries,out count);
                try {
                    if(error!=0 || count==0)break;
                    for(int index=0;index<count;index++) {
                        scanned++;var filter=(Filter)Marshal.PtrToStructure(Marshal.ReadIntPtr(entries,index*IntPtr.Size),typeof(Filter));
                        if(filter.sublayer==policySubLayer || (filter.layer!=Layer4 && filter.layer!=Layer6 && filter.layer!=Receive4 && filter.layer!=Receive6))continue;
                        if(filter.action.type!=0x1002 && (filter.action.type&0x4000)==0)continue;
                        bool hasApp;if(!PossibleGameFilter(filter,game,out hasApp))continue;
                        possible++;int weight=SubWeight(filter.sublayer,cache);
                        candidates.Add(new Dictionary<string,object>{{"filter_id",filter.id.ToString()},{"layer",filter.layer.ToString()},{"sublayer",filter.sublayer.ToString()},
                            {"provider",PointerGuid(filter.provider).ToString()},{"callout",(filter.action.type&0x4000)!=0?filter.action.key.ToString():null},
                            {"sublayer_weight",weight},{"priority_relation",weight<0?"unknown":weight==PolicyWeight?"equal":weight>PolicyWeight?"higher":"lower"},
                            {"action_type",filter.action.type},{"flags",filter.flags},{"effective_weight",WeightHex(filter.effectiveWeight)},
                            {"game_app_condition_present",hasApp},{"condition_count",filter.count},{"endpoint_match_not_evaluated",true},{"runtime_callout_verdict_not_observed",true}});
                        if(candidates.Count>128) {candidates.Sort(delegate(Dictionary<string,object> a,Dictionary<string,object> b){return Convert.ToInt32(b["sublayer_weight"]).CompareTo(Convert.ToInt32(a["sublayer_weight"]));});candidates.RemoveAt(candidates.Count-1);}
                    }
                } finally {if(entries!=IntPtr.Zero)FwpmFreeMemory0(ref entries);}
                if((Stopwatch.GetTimestamp()-began)/(double)Stopwatch.Frequency>.35) {truncated=true;break;}
            }
            if(scanned>=8192)truncated=true;
        } finally {if(iterator!=IntPtr.Zero)FwpmFilterDestroyEnumHandle0(engine,iterator);}
        Audit("arbitration_snapshot",new Dictionary<string,object>{{"scanned_filter_count",scanned},{"possible_permit_or_callout_count",possible},{"candidates",candidates},
            {"own_sublayer_weight",PolicyWeight},{"own_readback_verified",policyReadback},{"universal_sublayer_weight",SubWeight(Universal,cache)},
            {"truncated",truncated},{"enumeration_error",error},{"candidate_only_not_actual_flow_verdict",true},{"other_policies_modified",false}});
    }
    static void PolicySelfCheck() {
        if(Marshal.SizeOf(typeof(Provider))!=64 || Marshal.SizeOf(typeof(SubLayer))!=72 || Marshal.OffsetOf(typeof(SubLayer),"weight").ToInt32()!=64)throw new Exception("Policy ABI mismatch");
        var provider=ProviderPlan();var sublayer=SubLayerPlan();
        if(provider.flags!=0 || sublayer.flags!=0 || sublayer.weight!=PolicyWeight || sublayer.key==Universal || PointerGuid(sublayer.provider)!=provider.key)throw new Exception("Transient own-provider/sublayer plan failed");
        Filter filter=new Filter();ApplyPolicy(ref filter);
        if(filter.sublayer!=policySubLayer || filter.provider==IntPtr.Zero || (filter.flags&8)==0 || filter.weight.type!=1 || filter.weight.value.small!=15)throw new Exception("Policy attachment failed");
        bool ignored;if(!PossibleGameFilter(new Filter(),"game",out ignored))throw new Exception("Unqualified candidate should remain possible");
    }
}
