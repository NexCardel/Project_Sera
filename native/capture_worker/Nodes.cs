// native/capture_worker/Nodes.cs - core/sgt_i/uia_nodes.py in C#: a page as nodes in one cached UIA call,
// and the lines rebuilt from them. Must give byte-identical nodes and lines to the Python reader
// (tests/test_cs_capture_worker.py compares both on the same pages).

using System;
using System.Collections.Generic;
using System.Text;

namespace SeraCapture
{
    public sealed class Node
    {
        public int Parent, Depth, CType;
        public string Name = "";
        public long[] Rect;                  // left, top, width, height; null = none
        public int Heading;                  // 0 = no key
        public int Landmark;                 // 0 = no key
        public string Role;                  // null = no key
        public string Aid;                   // null = no key
        public string Value;                 // null = no key
        public int Selected = -1;            // -1 = no key, else 0 / 1
        public int[] Grid;                   // null = no key
        public bool Own;
        public bool Gecko;

        public void Write(JsonWriter w)
        {
            w.BeginObject();
            w.Key("parent").Int(Parent).Key("depth").Int(Depth).Key("ctype").Int(CType).Key("name").String(Name);
            w.Key("rect");
            if (Rect == null) w.Null();
            else { w.BeginArray(); foreach (long v in Rect) w.Int(v); w.EndArray(); }
            if (Heading != 0) w.Key("heading").Int(Heading);
            if (Landmark != 0) w.Key("landmark").Int(Landmark);
            if (Role != null) w.Key("role").String(Role);
            if (Aid != null) w.Key("aid").String(Aid);
            if (Value != null) w.Key("value").String(Value);
            if (Selected >= 0) w.Key("selected").Bool(Selected == 1);
            if (Grid != null) w.Key("grid").BeginArray().Int(Grid[0]).Int(Grid[1]).EndArray();
            if (Own) w.Key("own").Bool(true);
            if (Gecko) w.Key("gecko").Bool(true);
            w.EndObject();
        }

        public static void WriteDocs(JsonWriter w, List<List<Node>> docs)
        {
            w.BeginArray();
            foreach (var nodes in docs)
            {
                w.BeginArray();
                foreach (var n in nodes) n.Write(w);
                w.EndArray();
            }
            w.EndArray();
        }
    }

    public static class Py
    {
        // Python's str.isspace() set, so Strip() is str.strip().
        public static bool IsSpace(char c)
        {
            int v = c;
            if (v <= 0x20) return v == 0x20 || (v >= 0x09 && v <= 0x0d) || (v >= 0x1c && v <= 0x1f);
            switch (v)
            {
                case 0x85: case 0xa0: case 0x1680: case 0x2028: case 0x2029: case 0x202f:
                case 0x205f: case 0x3000:
                    return true;
            }
            return v >= 0x2000 && v <= 0x200a;
        }

        public static string Strip(string s)
        {
            if (string.IsNullOrEmpty(s)) return "";
            int a = 0, b = s.Length;
            while (a < b && IsSpace(s[a])) a++;
            while (b > a && IsSpace(s[b - 1])) b--;
            return (a == 0 && b == s.Length) ? s : s.Substring(a, b - a);
        }

        public static bool Truthy(object v)
        {
            if (v == null) return false;
            if (v is bool) return (bool)v;
            if (v is int) return (int)v != 0;
            if (v is double) return (double)v != 0.0;
            if (v is string) return ((string)v).Length > 0;
            var arr = v as Array;
            if (arr != null) return arr.Length > 0;
            return true;
        }

        // s[:n] by code points (a valid surrogate pair is one character, as in Python).
        public static string Head(string s, int n)
        {
            int cp = 0, i = 0;
            while (i < s.Length && cp < n)
            {
                if (char.IsHighSurrogate(s[i]) && i + 1 < s.Length && char.IsLowSurrogate(s[i + 1])) i += 2;
                else i++;
                cp++;
            }
            return i >= s.Length ? s : s.Substring(0, i);
        }
    }

    public static class Nodes
    {
        public const string OWN_ID_PREFIX = "sera-";
        public const string SELECTED_PREFIX = "Selected: ";
        public const string FRAMEWORK_GECKO = "Gecko";
        private const int HEADING_NONE = 80050;

        private static readonly int[] CachedProperties = {
            Uia.PID_BOUNDING_RECT, Uia.PID_CONTROL_TYPE, Uia.PID_NAME, Uia.PID_AUTOMATION_ID, Uia.PID_IS_OFFSCREEN,
            Uia.PID_IS_PASSWORD, Uia.PID_FRAMEWORK_ID, Uia.PID_VALUE, Uia.PID_GRID_ROW, Uia.PID_GRID_COLUMN,
            Uia.PID_SELECTION_IS_SELECTED, Uia.PID_TOGGLE_STATE, Uia.PID_ARIA_ROLE, Uia.PID_LANDMARK_TYPE,
            Uia.PID_HEADING_LEVEL };
        private static readonly int[] UrlOnlyProperties = { Uia.PID_IS_OFFSCREEN, Uia.PID_VALUE, Uia.PID_FRAMEWORK_ID };

        public static bool IsContainer(int ct) { return ct == Uia.CT_CUSTOM || ct == Uia.CT_GROUP || ct == Uia.CT_PANE; }
        public static bool IsChoice(int ct) { return ct == Uia.CT_RADIOBUTTON || ct == Uia.CT_CHECKBOX; }
        private static bool IsValueBearing(int ct) { return ct == Uia.CT_COMBOBOX || ct == Uia.CT_EDIT || ct == Uia.CT_SPINNER; }

        // build_cache_request(): the whole subtree (control view, or raw), cached values only.
        public static IUIAutomationCacheRequest FullRequest(IUIAutomation uia, bool rawView)
        {
            var req = uia.CreateCacheRequest();
            foreach (int pid in CachedProperties)
            {
                try { req.AddProperty(pid); } catch (Exception) { }
            }
            req.SetTreeScope(Uia.SCOPE_SUBTREE);
            req.SetAutomationElementMode(0);
            if (rawView) req.SetTreeFilter(uia.RawViewCondition());
            return req;
        }

        // build_cache_request(properties=URL_ONLY_PROPERTIES): the found element only, live reference kept.
        public static IUIAutomationCacheRequest UrlOnlyRequest(IUIAutomation uia)
        {
            var req = uia.CreateCacheRequest();
            foreach (int pid in UrlOnlyProperties)
            {
                try { req.AddProperty(pid); } catch (Exception) { }
            }
            req.SetTreeScope(Uia.SCOPE_ELEMENT);
            return req;
        }

        public static object Cached(IUIAutomationElement el, int pid)
        {
            try { return el.GetCachedPropertyValue(pid); }
            catch (Exception) { return null; }
        }

        private static string CachedStr(IUIAutomationElement el, int pid)
        {
            return Cached(el, pid) as string;
        }

        public static Node MakeNode(IUIAutomationElement el, int parent, int depth)
        {
            var n = new Node { Parent = parent, Depth = depth };
            object ct = Cached(el, Uia.PID_CONTROL_TYPE);
            n.CType = ct is int ? (int)ct : 0;
            n.Name = Py.Strip(CachedStr(el, Uia.PID_NAME));
            var rect = Cached(el, Uia.PID_BOUNDING_RECT) as double[];
            if (rect != null && rect.Length > 0)
            {
                n.Rect = new long[rect.Length];
                for (int i = 0; i < rect.Length; i++) n.Rect[i] = (long)rect[i];       // int(): toward zero
            }
            object heading = Cached(el, Uia.PID_HEADING_LEVEL);
            if (heading is int && (int)heading > HEADING_NONE) n.Heading = (int)heading - HEADING_NONE;
            object landmark = Cached(el, Uia.PID_LANDMARK_TYPE);
            if (landmark is int && (int)landmark != 0) n.Landmark = (int)landmark;
            string role = CachedStr(el, Uia.PID_ARIA_ROLE);
            if (!string.IsNullOrEmpty(role)) n.Role = role;
            string aid = Py.Strip(CachedStr(el, Uia.PID_AUTOMATION_ID));
            if (aid.Length > 0) n.Aid = aid;
            int c = n.CType;
            if (IsValueBearing(c))
            {
                // A password field's content never reaches a node: the label (name) is kept, the value is not read.
                if (!Py.Truthy(Cached(el, Uia.PID_IS_PASSWORD)))
                    n.Value = Py.Strip(CachedStr(el, Uia.PID_VALUE));
            }
            else if (c == Uia.CT_RADIOBUTTON || c == Uia.CT_TABITEM)
            {
                n.Selected = Py.Truthy(Cached(el, Uia.PID_SELECTION_IS_SELECTED)) ? 1 : 0;
            }
            else if (c == Uia.CT_CHECKBOX)
            {
                object t = Cached(el, Uia.PID_TOGGLE_STATE);
                n.Selected = (t is int && (int)t == 1) ? 1 : 0;
            }
            else if (c == Uia.CT_DATAITEM || role == "cell" || role == "gridcell" || role == "columnheader" || role == "rowheader")
            {
                object row = Cached(el, Uia.PID_GRID_ROW), col = Cached(el, Uia.PID_GRID_COLUMN);
                if (row is int && col is int) n.Grid = new[] { (int)row, (int)col };
            }
            else if (IsContainer(c))
            {
                if (aid.StartsWith(OWN_ID_PREFIX, StringComparison.Ordinal)) n.Own = true;
            }
            return n;
        }

        // Pre-order over the cached subtree, the document order FindAll(Descendants) gives.
        public static void Walk(IUIAutomationElement el, int parent, int depth, List<Node> output)
        {
            IUIAutomationElementArray children;
            int count;
            try
            {
                children = el.GetCachedChildren();
                count = children != null ? children.Length : 0;
            }
            catch (Exception) { return; }
            for (int i = 0; i < count; i++)
            {
                var child = children.GetElement(i);
                output.Add(MakeNode(child, parent, depth));
                Walk(child, output.Count - 1, depth + 1, output);
            }
        }

        public static List<Node> NodesOf(IUIAutomationElement element)
        {
            var nodes = new List<Node>();
            Walk(element, -1, 0, nodes);
            if (nodes.Count > 0 && CachedStr(element, Uia.PID_FRAMEWORK_ID) == FRAMEWORK_GECKO)
                nodes[0].Gecko = true;
            return nodes;
        }

        // read_cached_subtree(): one Document with a live reference, read in one BuildUpdatedCache.
        public static List<Node> ReadCachedSubtree(IUIAutomationElement element, IUIAutomationCacheRequest request)
        {
            var updated = element.BuildUpdatedCache(request);
            if (updated == null) return new List<Node>();
            return NodesOf(updated);
        }

        public static List<string> LinesFromNodes(List<List<Node>> docs, bool includeSelection)
        {
            var lines = new List<string>();
            foreach (var nodes in docs)
            {
                bool gecko = nodes.Count > 0 && nodes[0].Gecko;
                var inDropdown = new HashSet<int>();
                string containerName = "";
                for (int idx = 0; idx < nodes.Count; idx++)
                {
                    var n = nodes[idx];
                    string name = n.Name;
                    int ctype = n.CType;
                    if (gecko)
                    {
                        int parent = n.Parent;
                        if (inDropdown.Contains(parent) || (parent >= 0 && nodes[parent].CType == Uia.CT_COMBOBOX))
                        {
                            inDropdown.Add(idx);
                            continue;
                        }
                        string echoed = containerName;
                        containerName = "";
                        if (IsContainer(ctype)) containerName = name;
                        else if (name.Length > 0 && name == echoed && IsChoice(ctype) && lines.Count > 0 && lines[lines.Count - 1] == name)
                            lines.RemoveAt(lines.Count - 1);
                    }
                    if (name.Length > 0) lines.Add(name);
                    string value = n.Value ?? "";
                    if (value.Length > 0 && value != name)
                    {
                        lines.Add(value);
                        if (includeSelection && ctype == Uia.CT_COMBOBOX)
                            lines.Add(SELECTED_PREFIX + (name.Length > 0 ? name + " = " + value : value));
                    }
                    if (includeSelection && name.Length > 0 && IsChoice(ctype) && n.Selected == 1)
                        lines.Add(SELECTED_PREFIX + name);
                }
            }
            return lines;
        }

        public static void WriteLines(JsonWriter w, List<string> lines)
        {
            w.BeginArray();
            foreach (var s in lines) w.String(s);
            w.EndArray();
        }
    }
}
