// native/capture_worker/Flash.cs - core/sgt_i/uia_events.FlashWatcher in C#: catch what flashes (live-region
// changes, notifications, windows opened inside the page) on the Documents of the windows SGT-I watches.
// Its own MTA thread does every registration; UIA calls the handlers on its own threads, and they only
// read the cached subtree that came with the event. A registration running HANG_SEC switches it off.

using System;
using System.Collections.Concurrent;
using System.Collections.Generic;
using System.Linq;
using System.Runtime.InteropServices;
using System.Threading;

namespace SeraCapture
{
    public sealed class Flash
    {
        public string Kind;
        public List<string> Lines;
        public double Ts;
    }

    [ComVisible(true), ClassInterface(ClassInterfaceType.None)]
    public sealed class AutomationHandler : IUIAutomationEventHandler
    {
        private readonly Action<string, List<string>> _sink;
        public AutomationHandler(Action<string, List<string>> sink) { _sink = sink; }

        public void HandleAutomationEvent(IUIAutomationElement sender, int eventId)
        {
            try
            {
                string kind = FlashWatcher.KindOf(eventId);
                if (kind != null) _sink(kind, FlashWatcher.ElementLines(sender, ""));
            }
            catch (Exception) { }
        }
    }

    [ComVisible(true), ClassInterface(ClassInterfaceType.None)]
    public sealed class NotificationHandler : IUIAutomationNotificationEventHandler
    {
        private readonly Action<string, List<string>> _sink;
        public NotificationHandler(Action<string, List<string>> sink) { _sink = sink; }

        public void HandleNotificationEvent(IUIAutomationElement sender, int kind, int processing, string displayString, string activityId)
        {
            try { _sink("notification", FlashWatcher.ElementLines(sender, displayString ?? "")); }
            catch (Exception) { }
        }
    }

    internal sealed class Registration
    {
        public string How;            // "auto" | "note"
        public int Event;
        public IUIAutomationElement Doc;
        public object Handler;
    }

    internal sealed class FlashWindow
    {
        public string Key;            // the URL the registrations were made for (null = none)
        public List<Registration> Tokens = new List<Registration>();
        public readonly LinkedList<Flash> Pending = new LinkedList<Flash>();
        public double Seen;
    }

    public sealed class FlashWatcher
    {
        public const int EVENT_WINDOW_OPENED = 20016, EVENT_LIVE_REGION_CHANGED = 20024, EVENT_NOTIFICATION = 20035;
        public const double HANG_SEC = 10.0, FLASH_TTL_SEC = 30.0, DEDUPE_SEC = 2.0;
        public const int MAX_WINDOWS = 4, MAX_PENDING = 32, MAX_FLASH_LINES = 40, MAX_LINE_CHARS = 300;

        private readonly Action<string> _echo;
        private readonly object _lock = new object();
        private readonly Dictionary<long, FlashWindow> _windows = new Dictionary<long, FlashWindow>();
        private readonly List<long> _order = new List<long>();
        private BlockingCollection<object[]> _commands;
        private double _busySince = -1;
        public string Disabled;

        public FlashWatcher(Action<string> echo) { _echo = echo; }

        public static string KindOf(int ev)
        {
            if (ev == EVENT_LIVE_REGION_CHANGED) return "live_region";
            if (ev == EVENT_NOTIFICATION) return "notification";
            if (ev == EVENT_WINDOW_OPENED) return "window_opened";
            return null;
        }

        // element_lines(): the sender's own name, then its cached descendants, as the reader lists them.
        public static List<string> ElementLines(IUIAutomationElement sender, string extra)
        {
            var nodes = new List<Node>();
            try
            {
                nodes.Add(Nodes.MakeNode(sender, -1, 0));
                Nodes.Walk(sender, 0, 1, nodes);
            }
            catch (Exception) { }
            var lines = new List<string>();
            string e = Py.Strip(extra);
            if (e.Length > 0) lines.Add(e);
            lines.AddRange(Nodes.LinesFromNodes(new List<List<Node>> { nodes }, false));
            var output = new List<string>();
            foreach (var raw in lines)
            {
                string ln = Py.Head(raw, MAX_LINE_CHARS);
                if (ln.Length > 0 && (output.Count == 0 || output[output.Count - 1] != ln)) output.Add(ln);
                if (output.Count >= MAX_FLASH_LINES) break;
            }
            return output;
        }

        // ---- the request side (never blocks, never throws) -----------------------------------------

        public void Watch(long hwnd, string key)
        {
            try
            {
                if (hwnd == 0 || Disabled != null) return;
                double busy = _busySince;
                if (busy >= 0 && Clock.Now() - busy > HANG_SEC)
                {
                    Disable(string.Format("a UIA event registration has been running for over {0:0} s (hung)", HANG_SEC));
                    return;
                }
                lock (_lock)
                {
                    FlashWindow w;
                    if (_windows.TryGetValue(hwnd, out w)) _order.Remove(hwnd);
                    else w = new FlashWindow();
                    _windows[hwnd] = w;
                    _order.Add(hwnd);
                    w.Seen = Clock.Now();
                    var stale = new List<FlashWindow>();
                    while (_order.Count > MAX_WINDOWS)
                    {
                        long old = _order[0];
                        _order.RemoveAt(0);
                        stale.Add(_windows[old]);
                        _windows.Remove(old);
                    }
                    if (w.Key == key && stale.Count == 0) return;
                    bool changed = w.Key != key;
                    w.Key = key;
                    if (_commands == null)
                    {
                        _commands = new BlockingCollection<object[]>();
                        var cmds = _commands;
                        var t = new Thread(() => Run(cmds)) { IsBackground = true, Name = "sgt-i-uia-events" };
                        t.SetApartmentState(ApartmentState.MTA);
                        t.Start();
                    }
                    foreach (var old in stale) _commands.Add(new object[] { "drop", old });
                    if (changed) _commands.Add(new object[] { "watch", hwnd, w });
                }
            }
            catch (Exception) { }
        }

        public List<Flash> Take(long hwnd)
        {
            try
            {
                lock (_lock)
                {
                    FlashWindow w;
                    if (!_windows.TryGetValue(hwnd, out w) || w.Pending.Count == 0) return new List<Flash>();
                    double cutoff = Clock.Now() - FLASH_TTL_SEC;
                    var got = w.Pending.Where(f => f.Ts >= cutoff).ToList();
                    w.Pending.Clear();
                    return got;
                }
            }
            catch (Exception) { return new List<Flash>(); }
        }

        public void Stop()
        {
            BlockingCollection<object[]> commands;
            lock (_lock)
            {
                commands = _commands;
                _commands = null;
                _windows.Clear();
                _order.Clear();
            }
            if (commands != null) commands.Add(new object[] { "stop" });
        }

        private void Disable(string why)
        {
            lock (_lock)
            {
                if (Disabled != null) return;
                Disabled = why;
                _windows.Clear();
                _order.Clear();
                _commands = null;          // the wedged thread is abandoned, never waited for
            }
            try { _echo("[SGT-I] UIA event listening off for the rest of this run: " + why); } catch (Exception) { }
        }

        private Action<string, List<string>> SinkFor(FlashWindow w)
        {
            return (kind, lines) =>
            {
                if (lines == null || lines.Count == 0) return;
                double now = Clock.Now();
                lock (_lock)
                {
                    for (var node = w.Pending.Last; node != null; node = node.Previous)
                    {
                        var f = node.Value;
                        if (now - f.Ts > DEDUPE_SEC) break;
                        if (f.Kind == kind && f.Lines.SequenceEqual(lines)) return;
                    }
                    w.Pending.AddLast(new Flash { Kind = kind, Lines = lines, Ts = now });
                    while (w.Pending.Count > MAX_PENDING) w.Pending.RemoveFirst();
                }
            };
        }

        // ---- the listener's own thread ---------------------------------------------------------------

        private IUIAutomation _uia;
        private IUIAutomation5 _uia5;
        private IUIAutomationCacheRequest _cache;

        private void InitBackend()
        {
            object obj;
            try { obj = Uia.Create(Uia.CLSID_CUIAutomation8); }     // also answers IUIAutomation2..6 (Windows 8+)
            catch (Exception) { obj = Uia.Create(Uia.CLSID_CUIAutomation); }
            _uia = (IUIAutomation)obj;
            _uia5 = obj as IUIAutomation5;                             // before Windows 10 1709: no notifications
            _cache = Nodes.FullRequest(_uia, false);
        }

        private List<Registration> Register(long hwnd, Action<string, List<string>> sink)
        {
            var tokens = new List<Registration>();
            var root = _uia.ElementFromHandle(new IntPtr(hwnd));
            if (root == null) return tokens;
            var found = root.FindAll(Uia.SCOPE_DESCENDANTS, _uia.CreatePropertyCondition(Uia.PID_CONTROL_TYPE, Uia.CT_DOCUMENT));
            int n = found != null ? found.Length : 0;
            for (int i = 0; i < n; i++)
            {
                var doc = found.GetElement(i);
                var handler = new AutomationHandler(sink);
                foreach (int ev in new[] { EVENT_LIVE_REGION_CHANGED, EVENT_WINDOW_OPENED })
                {
                    _uia.AddAutomationEventHandler(ev, doc, Uia.SCOPE_SUBTREE, _cache, handler);
                    tokens.Add(new Registration { How = "auto", Event = ev, Doc = doc, Handler = handler });
                }
                if (_uia5 != null)
                {
                    var note = new NotificationHandler(sink);
                    _uia5.AddNotificationEventHandler(doc, Uia.SCOPE_SUBTREE, _cache, note);
                    tokens.Add(new Registration { How = "note", Event = EVENT_NOTIFICATION, Doc = doc, Handler = note });
                }
            }
            return tokens;
        }

        private void Unregister(List<Registration> tokens)
        {
            foreach (var t in tokens)
            {
                try
                {
                    if (t.How == "auto") _uia.RemoveAutomationEventHandler(t.Event, t.Doc, (IUIAutomationEventHandler)t.Handler);
                    else _uia5.RemoveNotificationEventHandler(t.Doc, (IUIAutomationNotificationEventHandler)t.Handler);
                }
                catch (Exception) { }      // the page is gone already
            }
        }

        private void Run(BlockingCollection<object[]> commands)
        {
            try
            {
                _busySince = Clock.Now();
                InitBackend();
            }
            catch (Exception e)
            {
                _busySince = -1;
                Disable("UI Automation events unavailable: " + PageReader.ErrName(e) + ": " + e.Message);
                return;
            }
            _busySince = -1;
            var live = new List<FlashWindow>();
            while (true)
            {
                var cmd = commands.Take();
                if ((string)cmd[0] == "stop") break;
                var w = (FlashWindow)cmd[cmd.Length - 1];
                _busySince = Clock.Now();
                try
                {
                    Unregister(w.Tokens);
                    w.Tokens = new List<Registration>();
                    live.Remove(w);
                    if ((string)cmd[0] == "watch")
                    {
                        w.Tokens = Register((long)cmd[1], SinkFor(w));
                        live.Add(w);
                    }
                }
                catch (Exception) { }
                finally
                {
                    if ((string)cmd[0] == "watch" && w.Tokens.Count == 0)
                        lock (_lock) w.Key = null;      // window or page gone, or no Document yet: the next watch tries again
                    _busySince = -1;
                }
            }
            foreach (var w in live) { Unregister(w.Tokens); w.Tokens = new List<Registration>(); }
        }
    }
}
