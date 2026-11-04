// Domain-list coverage and provenance. This is NOT a TLS-sniffing proxy.
using System;
using System.IO;
using System.Collections.Generic;
using System.Net;
using System.Text.RegularExpressions;

partial class NetworkBroker {
    static readonly Guid OriginalAppId=new Guid("0e6cd086-e1fb-4212-842f-8a9f993fb3f6");
    static readonly object domainLock=new object();
    static Dictionary<string,HashSet<string>> addressDomains=new Dictionary<string,HashSet<string>>();
    static readonly string[] GatewayDomains={"gw-pve.escapefromtarkov.com","gw-pve.escapefromtarkov.ru"};
    static readonly string[] SeedDomains={"gw-pve.escapefromtarkov.com","gw-pve.escapefromtarkov.ru","lobby.escapefromtarkov.com","prod.escapefromtarkov.com","ragfair.escapefromtarkov.com","trading.escapefromtarkov.com"};
    static bool GameDomain(string host) {
        if(String.IsNullOrEmpty(host))return false;host=host.TrimEnd('.').ToLowerInvariant();
        return host.IndexOf("escapefromtarkov",StringComparison.OrdinalIgnoreCase)>=0 || host=="battlestategames.com" || host.EndsWith(".battlestategames.com",StringComparison.Ordinal);
    }
    static void AddDomainHosts(string text,HashSet<string> domains) {
        foreach(Match match in Regex.Matches(text,@"\b(?:https?|wss?)://(?<host>[A-Za-z0-9.-]+)",RegexOptions.IgnoreCase)) {
            string host=match.Groups["host"].Value.TrimEnd('.').ToLowerInvariant();if(GameDomain(host) && domains.Count<32)domains.Add(host);
        }
    }
    static HashSet<string> DomainCandidates() {
        var domains=new HashSet<string>(SeedDomains,StringComparer.OrdinalIgnoreCase);
        try {
            string logs=Path.Combine(Path.GetDirectoryName(programPath),"Logs");
            var folders=Directory.GetDirectories(logs);Array.Sort(folders,StringComparer.OrdinalIgnoreCase);
            string latest=null;DateTime newest=DateTime.MinValue;
            foreach(string folderName in folders) {DateTime date=Directory.GetLastWriteTimeUtc(folderName);if(date>newest) {newest=date;latest=folderName;}}
            if(latest!=null)foreach(string file in Directory.GetFiles(latest,"*.log")) {
                if(!Regex.IsMatch(Path.GetFileName(file),@"(?:^|[ _])backend_\d+\.log$",RegexOptions.IgnoreCase))continue;
                using(var input=new FileStream(file,FileMode.Open,FileAccess.Read,FileShare.ReadWrite|FileShare.Delete)) {
                    input.Seek(Math.Max(0,input.Length-384000),SeekOrigin.Begin);
                    using(var reader=new StreamReader(input))AddDomainHosts(reader.ReadToEnd(),domains);
                }
            }
        } catch {} // Static verified vendor domains remain available.
        return domains;
    }
    static void ResolveDomainBatch() {
        var domains=DomainCandidates();Audit("domain_policy",new Dictionary<string,object>{{"domains",new List<string>(domains)},
            {"source","seed_domains_and_game_log_hostname_keyword"},{"domain_keyword","escapefromtarkov"},{"recognition","dns_mapping_not_tls_sniffing"},{"query_values_recorded",false}});
        foreach(string host in domains) {
            string domain=host;
            try {Dns.BeginGetHostAddresses(domain,delegate(IAsyncResult result) {
                try {foreach(var ip in Dns.EndGetHostAddresses(result))if(PublicAddress(ip)) {
                    string address=ip.ToString();lock(domainLock) {HashSet<string> names;if(!addressDomains.TryGetValue(address,out names)) {names=new HashSet<string>();addressDomains[address]=names;}names.Add(domain);}
                    dnsResults.Enqueue(address);
                }} catch {}
            },null);}catch {}
        }
    }
    static List<string> DomainEvidence(string address) {
        lock(domainLock) {HashSet<string> names;if(addressDomains.TryGetValue(address,out names))return new List<string>(names);}
        return new List<string>();
    }
    static void DomainSelfCheck() {
        if(!GameDomain("gw-pve.escapefromtarkov.com") || !GameDomain("PROD.ESCAPEFROMTARKOV.COM.") || !GameDomain("GW-PVE.ESCAPEFROMTARKOV.RU.") ||
           !GameDomain("cdn-escapefromtarkov.example.net") || GameDomain("example.com") || GameDomain("127.0.0.1"))throw new Exception("Domain keyword check failed");
        if(OriginalAppId==AppId)throw new Exception("Current and original app identities must be separate filters");
        var hosts=new HashSet<string>();AddDomainHosts("https://cdn-escapefromtarkov.example/path?secret=1 https://example.test/escapefromtarkov?keyword=escapefromtarkov",hosts);
        if(hosts.Count!=1 || !hosts.Contains("cdn-escapefromtarkov.example"))throw new Exception("Keyword must match host, not query/path");
    }
}
