// native/capture_worker/Program.cs - SeraCaptureWorker.exe: core/capture/worker_main.py in C# (UCW).
//
// Started by Sera (core/capture/cs_worker.py) with its stdin/stdout as the pipe. Frames are a 4-byte
// little-endian length and a UTF-8 JSON array whose first item is the message kind - the same kinds as
// core/capture/protocol.py. Sera's side turns them back into ReadContext / ReadResult objects, so the
// capture loop, SGT and the supervisor see exactly what the Python worker gives them.
//
//   Sera -> worker                          worker -> Sera
//   ["request", {seq,hwnd,url,reason,background}]   ["ready", pid, ms_to_ready]
//   ["close_lane", hwnd]                    ["beat", worker_now, stats]
//   ["abandon", hwnd, min_running_sec]      ["result", worker_now, result]   (result.seq = the request's)
//   ["raw", job_id, hwnd]                   ["gone", seq]
//   ["flash_watch", hwnd, key]              ["raw", job_id, {docs, ms} | {error}]
//   ["flash_stop"]                          ["flash", hwnd, kind, lines, age_sec]
//   ["watch_events", [hwnd...]]             ["echo", payload]
//   ["echo", payload]                       ["note", text]   ["crash", text]
//   ["stop"]
//
// Nothing is written to disk but a crash's stack (logs/capture_worker_fault.log, which Sera copies into
// hang.log). Notes are counts and times only; the pipe carries page text in memory only.

using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text;
using System.Threading;

namespace SeraCapture
{
    public static class Program
    {
        private const double BEAT_SEC = 0.5, SWEEP_SEC = 0.25, FLASH_PUMP_SEC = 0.1;
        private const int EXIT_PAUSED = 3;
        private const int MAX_FRAME = 64 * 1024 * 1024;

        private static Stream _in, _out;
        private static readonly object _sendLock = new object();
        private static readonly AutoResetEvent _wake = new AutoResetEvent(false);
        private static readonly ManualResetEvent _stopping = new ManualResetEvent(false);
        private static PageReader _reader;
        private static FlashWatcher _flashes;
        private static readonly List<long> _flashOrder = new List<long>();
        private static readonly Dictionary<long, string> _flashWindows = new Dictionary<long, string>();
        private static readonly object _flashLock = new object();

        private static string FaultLogPath()
        {
            string home = Environment.GetFolderPath(Environment.SpecialFolder.UserProfile);
            return Path.Combine(home, "AmanAssociates_Sera", "logs", "capture_worker_fault.log");
        }

        private static void Fault(string text)
        {
            try
            {
                string p = FaultLogPath();
                Directory.CreateDirectory(Path.GetDirectoryName(p));
                File.AppendAllText(p, DateTime.Now.ToString("yyyy-MM-dd HH:mm:ss") + " C# capture worker\n" + text + "\n", Encoding.UTF8);
            }
            catch (Exception) { }
        }

        // ---- frames ------------------------------------------------------------------------------------

        public static void Send(JsonWriter w)
        {
            byte[] body = Encoding.UTF8.GetBytes(w.ToString());
            byte[] head = BitConverter.GetBytes(body.Length);         // little-endian on Windows
            try
            {
                lock (_sendLock)
                {
                    _out.Write(head, 0, 4);
                    _out.Write(body, 0, body.Length);
                    _out.Flush();
                }
            }
            catch (Exception)
            {
                _stopping.Set();                                    // Sera's end is gone
                _wake.Set();
            }
        }

        private static void Note(string text)
        {
            Send(new JsonWriter(256).BeginArray().String("note").String("capture worker: " + text).EndArray());
        }

        private static bool ReadExactly(byte[] buf, int n)
        {
            int got = 0;
            while (got < n)
            {
                int k = _in.Read(buf, got, n - got);
                if (k <= 0)
                {
                    if (got > 0) throw new IOException("pipe closed mid-frame");
                    return false;
                }
                got += k;
            }
            return true;
        }

        private static List<object> ReadFrame()
        {
            var head = new byte[4];
            if (!ReadExactly(head, 4)) return null;
            int size = BitConverter.ToInt32(head, 0);
            if (size < 0 || size > MAX_FRAME) throw new IOException("frame of " + size + " bytes");
            var body = new byte[size];
            if (!ReadExactly(body, size)) throw new IOException("pipe closed mid-frame");
            return JsonParser.Parse(Encoding.UTF8.GetString(body)) as List<object>;
        }

        // ---- one message from Sera -------------------------------------------------------------------

        private static bool Handle(List<object> msg)
        {
            string kind = JsonParser.S(msg[0]);
            switch (kind)
            {
                case "request":
                {
                    var d = (Dictionary<string, object>)msg[1];
                    object v;
                    var ctx = new Ctx {
                        Seq = JsonParser.L(d["seq"]), Hwnd = JsonParser.L(d["hwnd"]),
                        Url = d.TryGetValue("url", out v) ? JsonParser.S(v) : "",
                        Reason = d.TryGetValue("reason", out v) ? JsonParser.S(v) : "frame",
                        Background = d.TryGetValue("background", out v) && JsonParser.B(v),
                        AskedAt = Clock.Now() };
                    string status = _reader.Request(ctx);
                    if (status != "started" && status != "queued")
                    {
                        // refused at once: Sera's loop hears it as an error result and asks again later
                        double t = Clock.Now();
                        var r = new Result { Ctx = ctx, StartedAt = t, FinishedAt = t, Error = status };
                        SendResult(t, r);
                    }
                    break;
                }
                case "close_lane":
                    _reader.CloseLane(JsonParser.L(msg[1]));
                    break;
                case "abandon":
                    _reader.AbandonSlowContent(JsonParser.L(msg[1]), JsonParser.D(msg[2]));
                    break;
                case "raw":
                {
                    long job = JsonParser.L(msg[1]);
                    _reader.RequestRaw(JsonParser.L(msg[2]), value => SendRaw(job, value));
                    break;
                }
                case "flash_watch":
                    FlashWatch(JsonParser.L(msg[1]), JsonParser.S(msg[2]));
                    break;
                case "flash_stop":
                    lock (_flashLock) { _flashWindows.Clear(); _flashOrder.Clear(); }
                    if (_flashes != null) _flashes.Stop();
                    break;
                case "watch_events":
                    // Page-change events (core/capture/page_events) are off unless SERA_CAPTURE_EVENTS=on, because
                    // the desktop-wide WinEvent hook made Edge stop answering UIA in stress tests. This worker
                    // never installs it: polling covers page changes, as in the Python worker's default.
                    break;
                case "echo":
                {
                    var w = new JsonWriter().BeginArray().String("echo");
                    WriteAny(w, msg.Count > 1 ? msg[1] : null);
                    Send(w.EndArray());
                    break;
                }
                case "stop":
                    return false;
            }
            return true;
        }

        private static void WriteAny(JsonWriter w, object o)
        {
            if (o == null) w.Null();
            else if (o is bool) w.Bool((bool)o);
            else if (o is double)
            {
                double d = (double)o;
                if (d == Math.Floor(d) && Math.Abs(d) < 1e15) w.Int((long)d); else w.Num(d);
            }
            else if (o is string) w.String((string)o);
            else if (o is List<object>) { w.BeginArray(); foreach (var x in (List<object>)o) WriteAny(w, x); w.EndArray(); }
            else if (o is Dictionary<string, object>)
            {
                w.BeginObject();
                foreach (var kv in (Dictionary<string, object>)o) { w.Key(kv.Key); WriteAny(w, kv.Value); }
                w.EndObject();
            }
            else w.Null();
        }

        private static void SendResult(double now, Result r)
        {
            var w = new JsonWriter(r.Docs.Count > 0 ? 65536 : 1024);
            w.BeginArray().String("result").Num(now);
            r.Write(w);
            Send(w.EndArray());
        }

        private static void SendRaw(long job, RawValue v)
        {
            var w = new JsonWriter(v.Docs != null ? 65536 : 256);
            w.BeginArray().String("raw").Int(job).BeginObject();
            if (v.Error != null) w.Key("error").String(v.Error);
            else { w.Key("docs"); Node.WriteDocs(w, v.Docs); w.Key("ms").Num(v.Ms); }
            Send(w.EndObject().EndArray());
        }

        private static void FlashWatch(long hwnd, string key)
        {
            if (_flashes == null) _flashes = new FlashWatcher(Note);
            lock (_flashLock)
            {
                if (!_flashWindows.ContainsKey(hwnd)) _flashOrder.Add(hwnd);
                _flashWindows[hwnd] = key;
                while (_flashOrder.Count > 8) { _flashWindows.Remove(_flashOrder[0]); _flashOrder.RemoveAt(0); }
            }
            _flashes.Watch(hwnd, key);
        }

        // ---- the result side ---------------------------------------------------------------------------

        private static void ResultsLoop()
        {
            while (!_stopping.WaitOne(0))
            {
                _wake.WaitOne(TimeSpan.FromSeconds(SWEEP_SEC));
                List<Result> results;
                try { results = _reader.Results(); }
                catch (Exception) { results = new List<Result>(); }
                double now = Clock.Now();
                foreach (var r in results) SendResult(now, r);
                if (_reader.Paused())
                {
                    Note("UIA reads stuck in the browser - this worker exits and Sera starts a fresh one");
                    _stopping.Set();
                    Environment.Exit(EXIT_PAUSED);
                    return;
                }
            }
        }

        private static void BeatLoop()
        {
            double lastBeat = 0.0;
            while (!_stopping.WaitOne(0))
            {
                double now = Clock.Now();
                if (now - lastBeat >= BEAT_SEC)
                {
                    lastBeat = now;
                    var w = new JsonWriter(512).BeginArray().String("beat").Num(now);
                    try { _reader.WriteStats(w, true); }
                    catch (Exception) { w = new JsonWriter(64).BeginArray().String("beat").Num(now).BeginObject().EndObject(); }
                    Send(w.EndArray());
                }
                if (_flashes != null) PumpFlashes(now);
                _stopping.WaitOne(TimeSpan.FromSeconds(FLASH_PUMP_SEC));
            }
        }

        private static void PumpFlashes(double now)
        {
            List<long> hwnds;
            lock (_flashLock) hwnds = _flashOrder.ToList();
            foreach (long hwnd in hwnds)
            {
                List<Flash> caught;
                try { caught = _flashes.Take(hwnd); }
                catch (Exception) { caught = new List<Flash>(); }
                foreach (var f in caught)
                {
                    var w = new JsonWriter(1024).BeginArray().String("flash").Int(hwnd).String(f.Kind);
                    Nodes.WriteLines(w, f.Lines);
                    Send(w.Num(Math.Max(0.0, now - f.Ts)).EndArray());
                }
            }
        }

        // ---- running -----------------------------------------------------------------------------------

        private static string Arg(string[] args, string name)
        {
            int i = Array.IndexOf(args, name);
            return i >= 0 && i + 1 < args.Length ? args[i + 1] : null;
        }

        private static double UiaReadyMs()
        {
            // How long making a UIA object takes in this process, on a throwaway MTA thread (the WP0 figure).
            double ms = -1;
            var t = new Thread(() =>
            {
                double t0 = Clock.Now();
                try { Uia.New(); } catch (Exception) { }
                ms = (Clock.Now() - t0) * 1000.0;
            }) { IsBackground = true };
            t.SetApartmentState(ApartmentState.MTA);
            t.Start();
            t.Join(15000);
            return ms;
        }

        [MTAThread]
        public static int Main(string[] args)
        {
            double t0 = Clock.Now();
            AppDomain.CurrentDomain.UnhandledException += (s, e) => Fault(e.ExceptionObject.ToString());
            _in = Console.OpenStandardInput();
            _out = Console.OpenStandardOutput();
            Console.SetOut(TextWriter.Null);         // a stray Console.Write would break a frame
            Console.SetError(TextWriter.Null);
            Scope.LoadExtra(Arg(args, "--scope-config"));
            UiaReadyMs();
            _reader = new PageReader(new UiaBackend(), Note,
                ctx => Send(new JsonWriter(64).BeginArray().String("gone").Int(ctx.Seq).EndArray()),
                () => _wake.Set());
            double readyMs = (Clock.Now() - t0) * 1000.0;
            Send(new JsonWriter(64).BeginArray().String("ready")
                 .Int(System.Diagnostics.Process.GetCurrentProcess().Id).Num(readyMs).EndArray());
            new Thread(ResultsLoop) { IsBackground = true, Name = "ucw-results" }.Start();
            new Thread(BeatLoop) { IsBackground = true, Name = "ucw-beat" }.Start();
            int code = 0;
            try
            {
                while (!_stopping.WaitOne(0))
                {
                    var msg = ReadFrame();
                    if (msg == null) break;                 // Sera closed the pipe: it is gone or stopping us
                    if (!Handle(msg)) break;
                }
            }
            catch (Exception e)
            {
                Send(new JsonWriter().BeginArray().String("crash")
                     .String("capture worker request loop: " + e.GetType().Name + "\n" + e).EndArray());
                code = 1;
            }
            _stopping.Set();
            _wake.Set();
            try { _reader.Close(); } catch (Exception) { }
            try { lock (_sendLock) _out.Close(); } catch (Exception) { }
            Environment.Exit(code);
            return code;
        }
    }
}
