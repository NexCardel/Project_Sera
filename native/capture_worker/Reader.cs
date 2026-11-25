// native/capture_worker/Reader.cs - core/capture/reader.PageReader in C#: page reads are tickets, one lane
// per browser window with a content thread and a scope (URL-only) thread, at most one queued read per
// thread (newer replaces older), R9 (a heavy page waits while the same browser reads another window),
// and the wedge rules (a read running WEDGE_SEC is abandoned; MAX_WEDGED_WORKERS stuck pause UIA).
// Same constants, same statuses, same stats keys as the Python reader.

using System;
using System.Collections.Concurrent;
using System.Collections.Generic;
using System.Diagnostics;
using System.Linq;
using System.Threading;

namespace SeraCapture
{
    public static class Clock
    {
        // QueryPerformanceCounter, the same counter Python's time.monotonic() reads on Windows.
        private static readonly double Freq = Stopwatch.Frequency;
        public static double Now() { return Stopwatch.GetTimestamp() / Freq; }
    }

    public sealed class Ctx
    {
        public long Seq, Hwnd;
        public string Url = "";
        public string Reason = "frame";
        public bool Background;
        public double AskedAt;
    }

    public sealed class Result
    {
        public Ctx Ctx;
        public List<string> Lines = new List<string>();
        public List<List<Node>> Docs = new List<List<Node>>();
        public List<string> DocUrls = new List<string>();
        public string TabBefore, TabAfter;
        public string UrlBefore = "", UrlAfter = "";
        public double StartedAt, FinishedAt;
        public string Error = "";
        public string ScopeRule = "";

        public double Ms { get { return Math.Max(0.0, (FinishedAt - StartedAt) * 1000.0); } }
        public bool Late { get { return Ms > PageReader.PAGE_TIMEOUT_SEC * 1000.0; } }

        public void Write(JsonWriter w)
        {
            w.BeginObject();
            w.Key("seq").Int(Ctx.Seq).Key("source").String("uia");
            w.Key("lines"); Nodes.WriteLines(w, Lines);
            w.Key("docs"); Node.WriteDocs(w, Docs);
            w.Key("doc_urls"); Nodes.WriteLines(w, DocUrls);
            w.Key("tab_before").String(TabBefore).Key("tab_after").String(TabAfter);
            w.Key("url_before").String(UrlBefore).Key("url_after").String(UrlAfter);
            w.Key("started_at").Num(StartedAt).Key("finished_at").Num(FinishedAt);
            w.Key("error").String(Error).Key("scope_rule").String(ScopeRule);
            w.EndObject();
        }
    }

    // What a raw (SDIS) read hands back: docs + ms, or an error.
    public sealed class RawValue
    {
        public List<List<Node>> Docs;
        public double Ms;
        public string Error;
        public static RawValue Err(string e) { return new RawValue { Error = e }; }
    }

    internal sealed class OneThread
    {
        private readonly BlockingCollection<Action> _q = new BlockingCollection<Action>();

        public OneThread(string name)
        {
            var t = new Thread(Run) { IsBackground = true, Name = name };
            t.SetApartmentState(ApartmentState.MTA);
            t.Start();
        }

        public void Submit(Action a) { _q.Add(a); }
        public void Shutdown() { _q.Add(null); }

        private void Run()
        {
            while (true)
            {
                var a = _q.Take();
                if (a == null) return;
                try { a(); } catch (Exception) { }
            }
        }
    }

    internal sealed class Job
    {
        public Ctx Ctx;
        public string Kind;          // "content" | "scope" | "raw"
        public double Submitted;
        public bool Abandoned;
        public volatile bool Done;
        public Job(Ctx ctx, string kind, double submitted) { Ctx = ctx; Kind = kind; Submitted = submitted; }
    }

    internal sealed class Worker
    {
        public readonly string Kind;
        public OneThread Thread;
        public Job Job;
        public Ctx Queued;
        public Tuple<long, Action<RawValue>> RawQueued;
        public Worker(string kind) { Kind = kind; }
    }

    internal sealed class Lane
    {
        public readonly long Hwnd;
        public readonly int Pid;
        public readonly Worker Content = new Worker(PageReader.CONTENT), ScopeW = new Worker(PageReader.SCOPE);
        public double LastUsed;
        public bool Background;
        public readonly List<KeyValuePair<string, double>> PageMsOrder = new List<KeyValuePair<string, double>>();
        public readonly Dictionary<string, double> PageMs = new Dictionary<string, double>();

        public Lane(long hwnd, int pid, double now) { Hwnd = hwnd; Pid = pid; LastUsed = now; }
        public Worker[] Workers() { return new[] { Content, ScopeW }; }
        public bool Idle() { return Workers().All(w => w.Job == null && w.Queued == null && w.RawQueued == null); }
    }

    public sealed class PageReader
    {
        public const int MAX_LANES = 3;
        public const double WEDGE_SEC = 20.0;
        public const int MAX_WEDGED_WORKERS = 5;
        public const double PAUSE_SEC = 300.0;
        public const double RESULT_MAX_AGE_SEC = 60.0;
        public const double LANE_IDLE_SEC = 120.0;
        public const double PAGE_TIMEOUT_SEC = 3.0;
        public const double HEAVY_READ_MS = 700.0;
        public const int PAGE_MS_KEPT = 50;
        public const int MAX_EARLY_ABANDONED = 4;
        public const string REASON_SCOPE = "scope";
        public const string CONTENT = "content", SCOPE = "scope";

        private readonly UiaBackend _backend;
        private readonly Action<string> _diag;
        private readonly Action<Ctx> _onDropped;
        private readonly Action _onResult;
        private readonly object _lock = new object();
        private readonly Dictionary<long, Lane> _lanes = new Dictionary<long, Lane>();
        private readonly List<long> _laneOrder = new List<long>();      // insertion order, as a Python dict
        private readonly Queue<Result> _done = new Queue<Result>();
        private readonly List<Job> _wedged = new List<Job>();
        private readonly List<Job> _earlyAbandoned = new List<Job>();
        private double _pausedUntil;
        private bool _closed;
        private int _abandoned;
        private Dictionary<string, double> _stats;
        [ThreadStatic] private static int _threadReady;      // 0 unknown, 1 ready, 2 failed

        public PageReader(UiaBackend backend, Action<string> diag, Action<Ctx> onDropped, Action onResult)
        {
            _backend = backend; _diag = diag; _onDropped = onDropped; _onResult = onResult;
            ResetIntervalStats(0.0);
        }

        // ---- the request side ----------------------------------------------------------------------

        public string Request(Ctx ctx)
        {
            lock (_lock)
            {
                if (_closed) return "no-lane";
                double now = Clock.Now();
                Sweep(now);
                if (_pausedUntil != 0.0) { _stats["paused_requests"] += 1; return "paused"; }
                Lane lane;
                if (!_lanes.TryGetValue(ctx.Hwnd, out lane))
                {
                    if (_lanes.Count >= MAX_LANES)
                    {
                        if (ctx.Background) { _stats["no_lane"] += 1; return "no-lane"; }
                        Evict();
                    }
                    lane = new Lane(ctx.Hwnd, Pid(ctx.Hwnd), now);
                    AddLane(lane);
                }
                lane.LastUsed = now;
                lane.Background = ctx.Background;
                var worker = ctx.Reason == REASON_SCOPE ? lane.ScopeW : lane.Content;
                if (worker.Queued != null)
                {
                    _stats["queued_replaced"] += 1;          // newer wins; same kind only (R3)
                    Dropped(worker.Queued);
                }
                worker.Queued = ctx;
                if (Pump(now).Any(c => ReferenceEquals(c, ctx))) return "started";
                if (worker.Job == null) _stats["held"] += 1;  // R9: waits for the same browser's other read
                return "queued";
            }
        }

        public List<Result> Results()
        {
            List<Result> output;
            double now;
            lock (_lock)
            {
                now = Clock.Now();
                Sweep(now);
                Pump(now);
                output = _done.ToList();
                _done.Clear();
            }
            var kept = new List<Result>();
            foreach (var r in output)
            {
                double asked = r.Ctx.AskedAt != 0.0 ? r.Ctx.AskedAt : r.StartedAt;
                if (now - asked > RESULT_MAX_AGE_SEC)
                {
                    lock (_lock) _stats["too_old"] += 1;
                    DiagSafe(string.Format("reader dropped a result {0:0} s old ({1})", now - asked, r.Error.Length > 0 ? r.Error : "ok"));
                    continue;
                }
                kept.Add(r);
            }
            return kept;
        }

        public bool AbandonSlowContent(long hwnd, double minRunningSec)
        {
            lock (_lock)
            {
                if (_closed || _pausedUntil != 0.0) return false;
                double now = Clock.Now();
                Sweep(now);
                _earlyAbandoned.RemoveAll(j => j.Done);
                if (_earlyAbandoned.Count >= MAX_EARLY_ABANDONED) return false;
                Lane lane;
                if (!_lanes.TryGetValue(hwnd, out lane)) return false;
                var worker = lane.Content;
                var job = worker.Job;
                if (job == null || job.Abandoned || job.Done) return false;
                if (now - job.Submitted < minRunningSec) return false;
                Abandon(worker, job);
                _abandoned += 1;
                _earlyAbandoned.Add(job);
                DiagSafe(string.Format("reader content read abandoned early ({0:0.0} s running) for urgent read ({1} early-abandoned running)",
                                       now - job.Submitted, _earlyAbandoned.Count));
                return true;
            }
        }

        public bool RequestRaw(long hwnd, Action<RawValue> done)
        {
            Tuple<long, Action<RawValue>> old = null;
            bool refused;
            lock (_lock)
            {
                Lane lane;
                _lanes.TryGetValue(hwnd, out lane);
                refused = _closed || _pausedUntil != 0.0 || lane == null;
                if (!refused)
                {
                    old = lane.Content.RawQueued;
                    lane.Content.RawQueued = Tuple.Create(hwnd, done);
                    Pump(Clock.Now());
                }
            }
            if (refused) { Call(done, RawValue.Err("no-lane")); return false; }
            if (old != null) Call(old.Item2, RawValue.Err("replaced"));
            return true;
        }

        private static void Call(Action<RawValue> done, RawValue v)
        {
            try { done(v); } catch (Exception) { }
        }

        public bool Paused() { lock (_lock) return _pausedUntil != 0.0; }

        public void CloseLane(long hwnd)
        {
            lock (_lock)
            {
                Lane lane;
                if (_lanes.TryGetValue(hwnd, out lane)) { RemoveLane(hwnd); Retire(lane); }
            }
        }

        public void WriteStats(JsonWriter w, bool reset)
        {
            lock (_lock)
            {
                w.BeginObject();
                foreach (var k in new[] { "reads", "scope_reads", "late", "queued_replaced", "held", "paused_requests", "no_lane", "too_old" })
                    w.Key(k).Int((long)_stats[k]);
                w.Key("max_ms").Int((long)Math.Round(_stats["max_ms"], MidpointRounding.ToEven));
                w.Key("last_ms").Int((long)Math.Round(_stats["last_ms"], MidpointRounding.ToEven));
                w.Key("abandoned").Int(_abandoned).Key("stuck").Int(Stuck());
                w.Key("paused"); if (_pausedUntil != 0.0) w.String("yes"); else w.Null();
                w.Key("lanes").Int(_lanes.Count);
                w.EndObject();
                if (reset) ResetIntervalStats(_stats["last_ms"]);
            }
        }

        public void Close()
        {
            lock (_lock)
            {
                _closed = true;
                foreach (var hwnd in _laneOrder.ToList()) Retire(_lanes[hwnd]);
                _lanes.Clear();
                _laneOrder.Clear();
                _earlyAbandoned.Clear();
            }
        }

        // ---- inside the lock ------------------------------------------------------------------------

        private void AddLane(Lane lane) { _lanes[lane.Hwnd] = lane; _laneOrder.Add(lane.Hwnd); }
        private void RemoveLane(long hwnd) { _lanes.Remove(hwnd); _laneOrder.Remove(hwnd); }

        private void ResetIntervalStats(double keepLast)
        {
            _stats = new Dictionary<string, double> {
                {"reads", 0}, {"scope_reads", 0}, {"max_ms", 0.0}, {"last_ms", keepLast}, {"late", 0},
                {"queued_replaced", 0}, {"held", 0}, {"paused_requests", 0}, {"no_lane", 0}, {"too_old", 0} };
        }

        private int Pid(long hwnd)
        {
            try { return _backend.ProcessId(hwnd); } catch (Exception) { return 0; }
        }

        private int Stuck()
        {
            _wedged.RemoveAll(j => j.Done);
            return _wedged.Count;
        }

        private void Sweep(double now)
        {
            if (_pausedUntil != 0.0 && now >= _pausedUntil)
            {
                _pausedUntil = 0.0;
                DiagSafe(string.Format("reader pause over - reading again ({0} worker(s) still stuck)", Stuck()));
            }
            bool wedgedNow = false;
            foreach (var hwnd in _laneOrder.ToList())
            {
                var lane = _lanes[hwnd];
                foreach (var worker in lane.Workers())
                {
                    var job = worker.Job;
                    if (job != null && !job.Done && now - job.Submitted >= WEDGE_SEC)
                    {
                        wedgedNow = true;
                        Abandon(worker, job);
                        _abandoned += 1;
                        _wedged.Add(job);
                        DiagSafe(string.Format("reader {0} read stuck {1:0} s - replaced its worker ({2} this run, {3} still stuck)",
                                               worker.Kind, now - job.Submitted, _abandoned, Stuck()));
                    }
                }
                if (now - lane.LastUsed >= LANE_IDLE_SEC && lane.Idle())
                {
                    RemoveLane(lane.Hwnd);
                    Retire(lane);
                }
            }
            foreach (var job in _earlyAbandoned.ToList())
            {
                if (!job.Done)
                {
                    if (now - job.Submitted >= WEDGE_SEC && !_wedged.Contains(job))
                    {
                        wedgedNow = true;
                        _wedged.Add(job);
                        DiagSafe(string.Format("early-abandoned read still running past {0:0} s - counted as wedged ({1} still stuck)",
                                               WEDGE_SEC, Stuck()));
                    }
                }
                else _earlyAbandoned.Remove(job);
            }
            if (wedgedNow && _pausedUntil == 0.0 && Stuck() >= MAX_WEDGED_WORKERS)
            {
                _pausedUntil = now + PAUSE_SEC;
                DiagSafe(string.Format("{0} reader workers stuck - UIA reads paused for {1:0} min (OCR still works)",
                                       _wedged.Count, PAUSE_SEC / 60));
            }
        }

        private void Abandon(Worker worker, Job job)
        {
            job.Abandoned = true;
            if (job.Kind != "raw") Dropped(job.Ctx);
            if (ReferenceEquals(worker.Job, job)) worker.Job = null;
            if (worker.Thread != null) { worker.Thread.Shutdown(); worker.Thread = null; }
        }

        private void Dropped(Ctx ctx)
        {
            if (_onDropped == null) return;
            try { _onDropped(ctx); } catch (Exception) { }
        }

        private void Retire(Lane lane)
        {
            foreach (var worker in lane.Workers())
            {
                if (worker.Queued != null) Dropped(worker.Queued);
                worker.Queued = null;
                if (worker.RawQueued != null) { Call(worker.RawQueued.Item2, RawValue.Err("no-lane")); worker.RawQueued = null; }
                if (worker.Job != null) Abandon(worker, worker.Job);
                else if (worker.Thread != null) { worker.Thread.Shutdown(); worker.Thread = null; }
            }
        }

        private void Evict()
        {
            var lanes = _laneOrder.Select(h => _lanes[h]).OrderBy(l => l.Background ? 0 : 1).ThenBy(l => l.LastUsed).ToList();
            if (lanes.Count > 0) { RemoveLane(lanes[0].Hwnd); Retire(lanes[0]); }
        }

        private bool Heavy(Lane lane, Ctx ctx)
        {
            double ms;
            return lane.PageMs.TryGetValue(ctx.Url, out ms) && ms > HEAVY_READ_MS;
        }

        private bool SameBrowserBusy(Lane lane)
        {
            if (lane.Pid == 0) return false;
            return _lanes.Values.Any(o => !ReferenceEquals(o, lane) && o.Pid == lane.Pid && o.Content.Job != null);
        }

        private List<Ctx> Pump(double now)
        {
            var started = new List<Ctx>();
            if (_closed || _pausedUntil != 0.0) return started;
            var waiting = new List<Tuple<double, long, Lane, Worker>>();
            foreach (var hwnd in _laneOrder)
            {
                var lane = _lanes[hwnd];
                foreach (var w in lane.Workers())
                    if (w.Queued != null && w.Job == null) waiting.Add(Tuple.Create(w.Queued.AskedAt, w.Queued.Seq, lane, w));
            }
            foreach (var t in waiting.OrderBy(t => t.Item1).ThenBy(t => t.Item2))
            {
                var lane = t.Item3; var worker = t.Item4;
                var ctx = worker.Queued;
                Lane cur;
                if (ctx == null || worker.Job != null || !_lanes.TryGetValue(lane.Hwnd, out cur) || !ReferenceEquals(cur, lane)) continue;
                if (worker.Kind == CONTENT && Heavy(lane, ctx) && SameBrowserBusy(lane)) continue;
                Start(lane, worker, ctx, now);
                started.Add(ctx);
            }
            foreach (var hwnd in _laneOrder.ToList())     // an SDIS raw read only when no page read waits
            {
                var lane = _lanes[hwnd];
                var w = lane.Content;
                if (w.RawQueued != null && w.Job == null && w.Queued == null && !SameBrowserBusy(lane)) StartRaw(lane, w, now);
            }
            return started;
        }

        private void StartRaw(Lane lane, Worker worker, double now)
        {
            long hwnd = worker.RawQueued.Item1;
            var done = worker.RawQueued.Item2;
            worker.RawQueued = null;
            if (worker.Thread == null) worker.Thread = new OneThread("sgt-read-" + worker.Kind + "-" + lane.Hwnd);
            var job = new Job(new Ctx { Seq = 0, Hwnd = hwnd, AskedAt = now, Reason = "sdis-raw" }, "raw", now);
            worker.Job = job;
            worker.Thread.Submit(() =>
            {
                RawValue value;
                try { value = RawJob(hwnd); }
                catch (Exception e) { value = RawValue.Err("exception:" + ErrName(e)); }
                job.Done = true;
                bool abandoned;
                lock (_lock)
                {
                    if (ReferenceEquals(worker.Job, job)) worker.Job = null;
                    abandoned = job.Abandoned;
                    Pump(Clock.Now());
                }
                Call(done, abandoned ? RawValue.Err("timeout-abandoned") : value);
            });
        }

        private void Start(Lane lane, Worker worker, Ctx ctx, double now)
        {
            if (worker.Thread == null) worker.Thread = new OneThread("sgt-read-" + worker.Kind + "-" + lane.Hwnd);
            var job = new Job(ctx, worker.Kind, now);
            worker.Queued = null;
            worker.Job = job;
            bool content = worker.Kind == CONTENT;
            worker.Thread.Submit(() =>
            {
                Result r;
                try { r = content ? ContentJob(ctx) : ScopeJob(ctx); }
                catch (Exception e)
                {
                    double t = Clock.Now();
                    r = new Result { Ctx = ctx, StartedAt = t, FinishedAt = t, Error = "exception:" + ErrName(e) };
                }
                job.Done = true;
                Finished(lane, worker, job, r);
            });
        }

        private RawValue RawJob(long hwnd)
        {
            double t0 = Clock.Now();
            if (!ThreadReady()) return RawValue.Err("uia-init");
            List<List<Node>> docs;
            try { docs = _backend.ReadRawPage(hwnd); }
            catch (Exception e) { return RawValue.Err("exception:" + ErrName(e)); }
            return new RawValue { Docs = docs, Ms = (Clock.Now() - t0) * 1000.0 };
        }

        private void Finished(Lane lane, Worker worker, Job job, Result result)
        {
            lock (_lock)
            {
                if (job.Abandoned)
                {
                    result.Error = "timeout-abandoned";
                    Stuck();
                }
                else
                {
                    if (ReferenceEquals(worker.Job, job)) worker.Job = null;
                    if (job.Kind == CONTENT) CountRead(lane, result);
                    else _stats["scope_reads"] += 1;
                    Lane cur;
                    if (result.Error == "no-window" && _lanes.TryGetValue(lane.Hwnd, out cur) && ReferenceEquals(cur, lane))
                    {
                        RemoveLane(lane.Hwnd);
                        Retire(lane);
                    }
                }
                _done.Enqueue(result);
                Pump(Clock.Now());
            }
            if (_onResult != null) { try { _onResult(); } catch (Exception) { } }
        }

        private void CountRead(Lane lane, Result r)
        {
            double ms = r.Ms;
            _stats["reads"] += 1;
            _stats["last_ms"] = ms;
            _stats["max_ms"] = Math.Max(_stats["max_ms"], ms);
            if (r.Late) _stats["late"] += 1;
            if (r.Error.Length == 0)
            {
                string url = r.Ctx.Url ?? "";
                if (lane.PageMs.Count >= PAGE_MS_KEPT && !lane.PageMs.ContainsKey(url))
                {
                    string first = lane.PageMsOrder[0].Key;
                    lane.PageMsOrder.RemoveAt(0);
                    lane.PageMs.Remove(first);
                }
                if (!lane.PageMs.ContainsKey(url)) lane.PageMsOrder.Add(new KeyValuePair<string, double>(url, ms));
                lane.PageMs[url] = ms;
            }
        }

        private void DiagSafe(string text)
        {
            try { if (_diag != null) _diag(text); } catch (Exception) { }
        }

        public static string ErrName(Exception e)
        {
            // Python's comtypes raises COMError for a failed UIA call; keep the same name in error strings.
            if (e is System.Runtime.InteropServices.COMException || e is InvalidCastException) return "COMError";
            return e.GetType().Name;
        }

        // ---- on a lane's thread ---------------------------------------------------------------------

        private bool ThreadReady()
        {
            if (_threadReady == 0)
            {
                try { _backend.InitThread(); _threadReady = 1; }
                catch (Exception e)
                {
                    _threadReady = 2;
                    DiagSafe("reader UI Automation init failed: " + ErrName(e));
                }
            }
            return _threadReady == 1;
        }

        private sealed class Located
        {
            public string Url = "";
            public List<DocRef> ToRead = new List<DocRef>();
            public List<string> ToReadUrls = new List<string>();
            public string Rule = "";
        }

        // _locate(): Document first (D1); the address bar only when no on-screen Document is in scope and one
        // gives no URL at all (or there is none) - and then only those URL-less Documents may be read.
        private Located Locate(long hwnd, IUIAutomationElement root)
        {
            var docs = _backend.Documents(root).Where(d => !d.Offscreen).ToList();
            var output = new Located();
            var inScope = docs.Where(d => d.Url.Length > 0 && Scope.InScope(d.Url)).ToList();
            if (inScope.Count > 0)
            {
                output.Url = inScope[0].Url;
                output.ToRead = inScope;
                output.ToReadUrls = inScope.Select(d => d.Url).ToList();
                output.Rule = "document";
                return output;
            }
            var unnamed = docs.Where(d => d.Url.Length == 0).ToList();
            if (docs.Count > 0 && unnamed.Count == 0)
            {
                output.Url = docs[0].Url;
                return output;
            }
            string bar = _backend.AddressBar(hwnd, root) ?? "";
            if (bar.Length > 0 && Scope.InScope(bar))
            {
                output.Url = bar;
                output.ToRead = unnamed;
                output.ToReadUrls = unnamed.Select(d => bar).ToList();
                output.Rule = "address_bar";
                return output;
            }
            output.Url = bar.Length > 0 ? bar : (docs.Count > 0 ? docs[0].Url : "");
            return output;
        }

        private Result Begin(Ctx ctx, out IUIAutomationElement root)
        {
            var r = new Result { Ctx = ctx, StartedAt = Clock.Now() };
            root = null;
            if (!ThreadReady()) { r.Error = "uia-init"; return r; }
            root = _backend.Root(ctx.Hwnd);
            if (root == null) r.Error = "no-window";
            return r;
        }

        private Result ScopeJob(Ctx ctx)
        {
            IUIAutomationElement root;
            var r = Begin(ctx, out root);
            try
            {
                if (root != null)
                {
                    r.TabBefore = r.TabAfter = _backend.TabId(ctx.Hwnd, root);
                    var loc = Locate(ctx.Hwnd, root);
                    r.ScopeRule = loc.Rule;
                    r.UrlBefore = r.UrlAfter = loc.Url;
                    r.DocUrls = loc.ToReadUrls;
                }
            }
            catch (Exception e) { r.Error = "exception:" + ErrName(e); }
            r.FinishedAt = Clock.Now();
            return r;
        }

        private Result ContentJob(Ctx ctx)
        {
            IUIAutomationElement root;
            var r = Begin(ctx, out root);
            try
            {
                if (root != null)
                {
                    r.TabBefore = _backend.TabId(ctx.Hwnd, root);
                    var loc = Locate(ctx.Hwnd, root);
                    r.UrlBefore = loc.Url;
                    r.ScopeRule = loc.Rule;
                    for (int i = 0; i < loc.ToRead.Count; i++)
                    {
                        r.Docs.Add(_backend.ReadDocument(loc.ToRead[i].Element));
                        r.DocUrls.Add(loc.ToReadUrls[i]);
                    }
                    r.Lines = Nodes.LinesFromNodes(r.Docs, true);
                    if (r.Docs.Count > 0 && r.Docs.All(d => d.Count == 0) && (ctx.Reason == "url" || ctx.Reason == "burst"))
                        r.Error = "loading";     // WP3: a page caught between two pages, not a blind one
                    r.TabAfter = _backend.TabId(ctx.Hwnd, root);
                    if (r.TabAfter != r.TabBefore) r.UrlAfter = r.UrlBefore;
                    else if (loc.ToRead.Count > 0)
                    {
                        string current = _backend.DocumentUrl(loc.ToRead[0].Element);
                        if (current != null && current == r.UrlBefore) r.UrlAfter = r.UrlBefore;
                        else r.UrlAfter = Locate(ctx.Hwnd, root).Url;
                    }
                    else r.UrlAfter = Locate(ctx.Hwnd, root).Url;
                }
            }
            catch (Exception e) { r.Error = "exception:" + ErrName(e); }
            r.FinishedAt = Clock.Now();
            return r;
        }
    }
}
