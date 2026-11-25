// native/capture_worker/Scope.cs - core/capture/scope.is_in_scope_url, as the Python capture worker runs it:
// the built-in portals, the admin's vsdc_scope.json extra domains, and (VSDC_ALLOW_LOCAL_TEST=1) local
// test pages. Registered service portals come from Sera's database, which a worker never opens - the
// Python worker has none either, so this matches it exactly.
// Decided on the PARSED HOSTNAME, never a substring of the URL.

using System;
using System.Collections.Generic;
using System.IO;
using System.Text;
using System.Text.RegularExpressions;

namespace SeraCapture
{
    public static class Scope
    {
        private static readonly string[] Builtin = { "incometax.gov.in", "gst.gov.in", "tdscpc.gov.in", "traces.gov.in" };
        private static readonly HashSet<string> Forbidden = new HashSet<string> {
            "in", "gov.in", "nic.in", "co.in", "org.in", "net.in", "edu.in", "ac.in", "res.in" };
        private static readonly HashSet<string> LocalHosts = new HashSet<string> { "localhost", "127.0.0.1", "[::1]", "::1" };
        private static readonly string[] NoHostSchemes = { "about", "chrome", "edge", "data", "javascript", "view-source", "blob" };

        private static readonly Regex SchemeRe = new Regex(@"^([a-z][a-z0-9+.\-]*):", RegexOptions.IgnoreCase | RegexOptions.CultureInvariant);
        private static readonly Regex HostnameRe = new Regex(@"^[a-z0-9]([a-z0-9\-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9\-]*[a-z0-9])?)*$", RegexOptions.CultureInvariant);
        private static readonly Regex DrivePathRe = new Regex(@"^[a-zA-Z]:[/\\]", RegexOptions.CultureInvariant);

        private static List<string> _extra = new List<string>();

        public static bool AllowLocal()
        {
            return Environment.GetEnvironmentVariable("VSDC_ALLOW_LOCAL_TEST") == "1";
        }

        // Python's str.lower() for what can still pass the ASCII-only hostname check: ASCII, and the
        // Kelvin sign (U+212A), whose lower case is a plain 'k'. Any other non-ASCII stays and fails the check.
        private static string Lower(string s)
        {
            var sb = new StringBuilder(s.Length);
            foreach (char c in s)
            {
                if (c >= 'A' && c <= 'Z') sb.Append((char)(c + 32));
                else if (c == 'K') sb.Append('k');
                else sb.Append(c);
            }
            return sb.ToString();
        }

        private static string RStripDots(string s)
        {
            int b = s.Length;
            while (b > 0 && s[b - 1] == '.') b--;
            return s.Substring(0, b);
        }

        public static string ExtractHost(string url)
        {
            string u = Py.Strip(url);
            if (u.Length == 0) return null;
            var m = SchemeRe.Match(u);
            if (m.Success && u.Length >= m.Length + 2 && u.Substring(m.Length, 2) == "//")
                u = u.Substring(m.Length + 2);
            else if (m.Success && Array.IndexOf(NoHostSchemes, Lower(m.Groups[1].Value)) >= 0)
                return null;
            int cut = u.IndexOfAny(new[] { '/', '?', '#', '\\' });
            string authority = cut >= 0 ? u.Substring(0, cut) : u;
            int at = authority.LastIndexOf('@');
            if (at >= 0) authority = authority.Substring(at + 1);
            authority = RStripDots(Lower(Py.Strip(authority)));
            if (authority.StartsWith("[", StringComparison.Ordinal))
                return LocalHosts.Contains(authority) ? authority : null;
            int colon = authority.IndexOf(':');
            string host = colon >= 0 ? authority.Substring(0, colon) : authority;
            if (host.Length == 0 || host.Length > 253 || !HostnameRe.IsMatch(host)) return null;
            return host;
        }

        private static bool InDomain(string host, string domain)
        {
            return host == domain || host.EndsWith("." + domain, StringComparison.Ordinal);
        }

        public static bool IsLocalReference(string url)
        {
            string u = Py.Strip(url);
            if (u.Length == 0) return false;
            var m = SchemeRe.Match(u);
            if (m.Success && Lower(m.Groups[1].Value) == "file") return true;
            if (DrivePathRe.IsMatch(u)) return true;
            string host = ExtractHost(u);
            return host != null && LocalHosts.Contains(host);
        }

        public static bool InScope(string url)
        {
            string host = ExtractHost(url);
            if (host != null)
            {
                foreach (var d in Builtin) if (InDomain(host, d)) return true;
                foreach (var d in _extra) if (InDomain(host, d)) return true;
            }
            return AllowLocal() && IsLocalReference(url);
        }

        // load_extra_domains(): {"income_tax": [...], "gst": [...], "traces": [...]}; anything that is not a
        // plain registered domain is ignored, so a typo can never widen the scope.
        public static void LoadExtra(string path)
        {
            var found = new List<string>();
            try
            {
                if (string.IsNullOrEmpty(path) || !File.Exists(path)) { _extra = found; return; }
                var data = JsonParser.Parse(File.ReadAllText(path, Encoding.UTF8)) as Dictionary<string, object>;
                if (data == null) { _extra = found; return; }
                foreach (var key in new[] { "income_tax", "gst", "traces" })
                {
                    object v;
                    if (!data.TryGetValue(key, out v)) continue;
                    var list = v as List<object>;
                    if (list == null) continue;
                    foreach (var raw in list)
                    {
                        var s = raw as string;
                        if (s == null) continue;
                        string dom = RStripDots(Lower(Py.Strip(s)));
                        int dots = 0;
                        foreach (char c in dom) if (c == '.') dots++;
                        if (!HostnameRe.IsMatch(dom) || dots < 2 || Forbidden.Contains(dom) || dom.Length > 253) continue;
                        found.Add(dom);
                    }
                }
            }
            catch (Exception) { found.Clear(); }
            _extra = found;
        }
    }
}
