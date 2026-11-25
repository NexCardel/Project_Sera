// native/capture_worker/Backend.cs - core/capture/reader.UiaBackend and the uia_text helpers it calls
// (selected_tab_id_here, address_bar_here, documents_with_urls), plus uia_nodes.read_page_nodes_here.
// One UIA object per lane thread (MTA), made on first use on that thread.
// Strictly passive: property reads only; nothing is clicked, focused, scrolled or typed.

using System;
using System.Collections.Generic;
using System.Linq;

namespace SeraCapture
{
    public sealed class DocRef
    {
        public IUIAutomationElement Element;
        public string Url;
        public bool Offscreen;
    }

    public sealed class UiaBackend
    {
        private const double NO_TABS_RECHECK_SEC = 10.0;

        [ThreadStatic] private static IUIAutomation _uia;
        [ThreadStatic] private static Dictionary<long, Tuple<List<IUIAutomationElement>, double>> _tabs;
        [ThreadStatic] private static Dictionary<long, Tuple<IUIAutomationElement, IUIAutomationValuePattern>> _bars;
        [ThreadStatic] private static IUIAutomationCacheRequest _urlOnly, _full, _raw;

        public void InitThread()
        {
            _uia = Uia.New();
            _tabs = new Dictionary<long, Tuple<List<IUIAutomationElement>, double>>();
            _bars = new Dictionary<long, Tuple<IUIAutomationElement, IUIAutomationValuePattern>>();
            _urlOnly = Nodes.UrlOnlyRequest(_uia);
            _full = Nodes.FullRequest(_uia, false);
            _raw = null;
        }

        public int ProcessId(long hwnd)
        {
            try
            {
                uint pid;
                Uia.GetWindowThreadProcessId(new IntPtr(hwnd), out pid);
                return (int)pid;
            }
            catch (Exception) { return 0; }
        }

        public IUIAutomationElement Root(long hwnd)
        {
            if (hwnd == 0 || !Uia.IsWindow(new IntPtr(hwnd))) return null;
            try { return _uia.ElementFromHandle(new IntPtr(hwnd)); }
            catch (Exception) { return null; }
        }

        private IUIAutomationCondition ControlType(int ct)
        {
            return _uia.CreatePropertyCondition(Uia.PID_CONTROL_TYPE, ct);
        }

        private static string SelectedAmong(List<IUIAutomationElement> items)
        {
            foreach (var el in items)
            {
                try
                {
                    var p = el.GetCurrentPattern(Uia.PATTERN_SELECTION_ITEM) as IUIAutomationSelectionItemPattern;
                    if (p != null && p.CurrentIsSelected != 0)
                        return string.Join(".", el.GetRuntimeId().Select(x => x.ToString(System.Globalization.CultureInfo.InvariantCulture)));
                }
                catch (Exception) { }          // a closed tab's element: skip it
            }
            return null;
        }

        // selected_tab_id_here(): the RuntimeId of the selected TabItem; the tab strip is searched again
        // only when none of the remembered items is selected (a window without one: every 10 s at most).
        public string TabId(long hwnd, IUIAutomationElement root)
        {
            try
            {
                Tuple<List<IUIAutomationElement>, double> c;
                var items = new List<IUIAutomationElement>();
                double searched = 0.0;
                if (_tabs.TryGetValue(hwnd, out c)) { items = c.Item1; searched = c.Item2; }
                if (items.Count > 0)
                {
                    string found = SelectedAmong(items);
                    if (found != null) return found;
                }
                else if (searched != 0.0 && Clock.Now() - searched < NO_TABS_RECHECK_SEC)
                    return null;
                root = root ?? _uia.ElementFromHandle(new IntPtr(hwnd));
                if (root == null) return null;
                var coll = root.FindAll(Uia.SCOPE_DESCENDANTS, ControlType(Uia.CT_TABITEM));
                items = new List<IUIAutomationElement>();
                int n = coll != null ? coll.Length : 0;
                for (int i = 0; i < n; i++) items.Add(coll.GetElement(i));
                if (_tabs.Count > 20) _tabs.Clear();
                _tabs[hwnd] = Tuple.Create(items, Clock.Now());
                return SelectedAmong(items);
            }
            catch (Exception) { return null; }
        }

        private static bool LooksLikeUrl(string v)
        {
            return !string.IsNullOrEmpty(v) && (v.IndexOf('.') >= 0 || v.IndexOf('/') >= 0);
        }

        // address_bar_here(): the browser's address bar text; the bar found last time is asked first.
        public string AddressBar(long hwnd, IUIAutomationElement root)
        {
            Tuple<IUIAutomationElement, IUIAutomationValuePattern> cached;
            if (_bars.TryGetValue(hwnd, out cached))
            {
                try
                {
                    string v = cached.Item2.CurrentValue;
                    if (LooksLikeUrl(v)) return v;
                }
                catch (Exception) { _bars.Remove(hwnd); }
            }
            try
            {
                var element = root ?? _uia.ElementFromHandle(new IntPtr(hwnd));
                if (element == null) return "";
                var cond = _uia.CreateOrCondition(ControlType(Uia.CT_EDIT), ControlType(Uia.CT_COMBOBOX));
                var edits = element.FindAll(Uia.SCOPE_DESCENDANTS, cond);
                if (edits == null) return "";
                int n = edits.Length;
                for (int i = 0; i < n; i++)
                {
                    try
                    {
                        var edit = edits.GetElement(i);
                        string name = (edit.CurrentName ?? "").ToLowerInvariant();
                        string autoId = (edit.CurrentAutomationId ?? "").ToLowerInvariant();
                        if (name.Contains("address") || name.Contains("search") || name.Contains("url")
                            || autoId.Contains("address") || autoId.Contains("url") || autoId.Contains("view_"))
                        {
                            var vp = edit.GetCurrentPattern(Uia.PATTERN_VALUE) as IUIAutomationValuePattern;
                            if (vp != null)
                            {
                                string v = vp.CurrentValue;
                                if (LooksLikeUrl(v))
                                {
                                    if (_bars.Count > 20) _bars.Clear();
                                    _bars[hwnd] = Tuple.Create(edit, vp);
                                    return v;
                                }
                            }
                        }
                    }
                    catch (Exception) { }
                }
            }
            catch (Exception) { }          // transient UIA errors (e.g. during alt-tab)
            return "";
        }

        private static bool IsOffscreen(IUIAutomationElement el)
        {
            try { return el.CurrentIsOffscreen != 0; }
            catch (Exception) { }
            try { return Py.Truthy(el.GetCachedPropertyValue(Uia.PID_IS_OFFSCREEN)); }
            catch (Exception) { return false; }
        }

        // documents_with_urls(): every Document as (element, url, offscreen) in one FindAllBuildCache of
        // IsOffscreen / Value / FrameworkId only - no page content.
        public List<DocRef> Documents(IUIAutomationElement root)
        {
            var found = root.FindAllBuildCache(Uia.SCOPE_DESCENDANTS, ControlType(Uia.CT_DOCUMENT), _urlOnly);
            var output = new List<DocRef>();
            int n = found != null ? found.Length : 0;
            for (int i = 0; i < n; i++)
            {
                var el = found.GetElement(i);
                string url = Nodes.Cached(el, Uia.PID_VALUE) as string;
                object off = Nodes.Cached(el, Uia.PID_IS_OFFSCREEN);
                output.Add(new DocRef {
                    Element = el, Url = url != null ? Py.Strip(url) : "",
                    Offscreen = off != null ? Py.Truthy(off) : IsOffscreen(el) });
            }
            return output;
        }

        public List<Node> ReadDocument(IUIAutomationElement element)
        {
            return Nodes.ReadCachedSubtree(element, _full);
        }

        // document_url(): the live Value of the Document just read (null = could not be read).
        public string DocumentUrl(IUIAutomationElement element)
        {
            object v;
            try { v = element.GetCurrentPropertyValue(Uia.PID_VALUE); }
            catch (Exception) { return null; }
            var s = v as string;
            return s != null ? Py.Strip(s) : "";
        }

        // read_page_nodes_here(raw_view=True): the SDIS recorder's read - every on-screen Document, raw view.
        public List<List<Node>> ReadRawPage(long hwnd)
        {
            var docs = new List<List<Node>>();
            if (hwnd == 0 || !Uia.IsWindow(new IntPtr(hwnd))) return docs;
            var root = _uia.ElementFromHandle(new IntPtr(hwnd));
            if (root == null) return docs;
            if (_raw == null) _raw = Nodes.FullRequest(_uia, true);
            var found = root.FindAllBuildCache(Uia.SCOPE_DESCENDANTS, ControlType(Uia.CT_DOCUMENT), _raw);
            int n = found != null ? found.Length : 0;
            for (int i = 0; i < n; i++)
            {
                var el = found.GetElement(i);
                if (IsOffscreen(el)) continue;            // never a background tab's page
                docs.Add(Nodes.NodesOf(el));
            }
            return docs;
        }
    }
}
