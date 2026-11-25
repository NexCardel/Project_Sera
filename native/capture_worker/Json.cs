// native/capture_worker/Json.cs - the frames' JSON: a writer (every char >= 0x7F escaped as \uXXXX per
// UTF-16 code unit, so any BSTR - lone surrogates included - reaches Python's json.loads exactly) and a
// small parser for Sera's requests (objects -> Dictionary, arrays -> List, numbers -> double).

using System;
using System.Collections.Generic;
using System.Globalization;
using System.Text;

namespace SeraCapture
{
    public sealed class JsonWriter
    {
        private readonly StringBuilder _sb;
        private readonly Stack<bool> _hasItems = new Stack<bool>();   // per open container: an item written already
        private bool _afterKey;                                       // the next value follows a key: no comma

        public JsonWriter() : this(4096) { }
        public JsonWriter(int capacity) { _sb = new StringBuilder(capacity); }

        public override string ToString() { return _sb.ToString(); }

        private void Sep()
        {
            if (_afterKey) { _afterKey = false; return; }
            if (_hasItems.Count == 0) return;
            if (_hasItems.Peek()) _sb.Append(',');
            else { _hasItems.Pop(); _hasItems.Push(true); }
        }

        public JsonWriter BeginArray() { Sep(); _sb.Append('['); _hasItems.Push(false); return this; }
        public JsonWriter EndArray() { _hasItems.Pop(); _sb.Append(']'); return this; }
        public JsonWriter BeginObject() { Sep(); _sb.Append('{'); _hasItems.Push(false); return this; }
        public JsonWriter EndObject() { _hasItems.Pop(); _sb.Append('}'); return this; }

        public JsonWriter Key(string k) { Sep(); Str(k); _sb.Append(':'); _afterKey = true; return this; }

        public JsonWriter Null() { Sep(); _sb.Append("null"); return this; }
        public JsonWriter Bool(bool v) { Sep(); _sb.Append(v ? "true" : "false"); return this; }
        public JsonWriter Int(long v) { Sep(); _sb.Append(v.ToString(CultureInfo.InvariantCulture)); return this; }
        public JsonWriter Num(double v)
        {
            Sep();
            if (double.IsNaN(v) || double.IsInfinity(v)) _sb.Append("0.0");
            else
            {
                string t = v.ToString("R", CultureInfo.InvariantCulture);
                _sb.Append(t);
                if (t.IndexOf('.') < 0 && t.IndexOf('E') < 0) _sb.Append(".0");   // stays a float in Python
            }
            return this;
        }
        public JsonWriter String(string v) { Sep(); if (v == null) _sb.Append("null"); else Str(v); return this; }

        private const string Hex = "0123456789abcdef";

        private void Str(string s)
        {
            _sb.Append('"');
            for (int i = 0; i < s.Length; i++)
            {
                char c = s[i];
                if (c == '"') _sb.Append('\\').Append('"');
                else if (c == '\\') _sb.Append('\\').Append('\\');
                else if (c >= 0x20 && c < 0x7F) _sb.Append(c);
                else
                {
                    int v = c;
                    _sb.Append('\\').Append('u')
                       .Append(Hex[(v >> 12) & 15]).Append(Hex[(v >> 8) & 15])
                       .Append(Hex[(v >> 4) & 15]).Append(Hex[v & 15]);
                }
            }
            _sb.Append('"');
        }
    }

    public static class JsonParser
    {
        public static object Parse(string s)
        {
            int i = 0;
            return Value(s, ref i);
        }

        private static void Ws(string s, ref int i)
        {
            while (i < s.Length && (s[i] == ' ' || s[i] == '\t' || s[i] == '\n' || s[i] == '\r')) i++;
        }

        private static object Value(string s, ref int i)
        {
            Ws(s, ref i);
            if (i >= s.Length) throw new FormatException("json: end of input");
            char c = s[i];
            if (c == '{')
            {
                var d = new Dictionary<string, object>();
                i++; Ws(s, ref i);
                if (s[i] == '}') { i++; return d; }
                while (true)
                {
                    Ws(s, ref i);
                    string k = (string)Value(s, ref i);
                    Ws(s, ref i);
                    if (s[i] != ':') throw new FormatException("json: ':' expected");
                    i++;
                    d[k] = Value(s, ref i);
                    Ws(s, ref i);
                    if (s[i] == ',') { i++; continue; }
                    if (s[i] == '}') { i++; return d; }
                    throw new FormatException("json: ',' or '}' expected");
                }
            }
            if (c == '[')
            {
                var l = new List<object>();
                i++; Ws(s, ref i);
                if (s[i] == ']') { i++; return l; }
                while (true)
                {
                    l.Add(Value(s, ref i));
                    Ws(s, ref i);
                    if (s[i] == ',') { i++; continue; }
                    if (s[i] == ']') { i++; return l; }
                    throw new FormatException("json: ',' or ']' expected");
                }
            }
            if (c == '"')
            {
                var sb = new StringBuilder();
                i++;
                while (true)
                {
                    char ch = s[i++];
                    if (ch == '"') return sb.ToString();
                    if (ch != '\\') { sb.Append(ch); continue; }
                    char e = s[i++];
                    switch (e)
                    {
                        case '"': sb.Append('"'); break;
                        case '\\': sb.Append('\\'); break;
                        case '/': sb.Append('/'); break;
                        case 'b': sb.Append('\b'); break;
                        case 'f': sb.Append('\f'); break;
                        case 'n': sb.Append('\n'); break;
                        case 'r': sb.Append('\r'); break;
                        case 't': sb.Append('\t'); break;
                        case 'u':
                            sb.Append((char)int.Parse(s.Substring(i, 4), NumberStyles.HexNumber, CultureInfo.InvariantCulture));
                            i += 4;
                            break;
                        default: throw new FormatException("json: bad escape");
                    }
                }
            }
            if (s.Length - i >= 4 && string.CompareOrdinal(s, i, "true", 0, 4) == 0) { i += 4; return true; }
            if (s.Length - i >= 5 && string.CompareOrdinal(s, i, "false", 0, 5) == 0) { i += 5; return false; }
            if (s.Length - i >= 4 && string.CompareOrdinal(s, i, "null", 0, 4) == 0) { i += 4; return null; }
            int start = i;
            while (i < s.Length && "+-0123456789.eE".IndexOf(s[i]) >= 0) i++;
            if (i == start) throw new FormatException("json: unexpected character");
            return double.Parse(s.Substring(start, i - start), NumberStyles.Float, CultureInfo.InvariantCulture);
        }

        // Helpers for reading Sera's messages.
        public static long L(object o) { return o == null ? 0 : (long)Convert.ToDouble(o, CultureInfo.InvariantCulture); }
        public static double D(object o) { return o == null ? 0.0 : Convert.ToDouble(o, CultureInfo.InvariantCulture); }
        public static string S(object o) { return o as string ?? ""; }
        public static bool B(object o) { return o is bool && (bool)o; }
    }
}
