using System;
using System.Collections.Generic;
using System.Runtime.InteropServices;
using System.Runtime.InteropServices.ComTypes;
using System.Security.Cryptography;
using System.Text;
using System.Web.Script.Serialization;
using SolidWorks.Interop.sldworks;
using SolidWorks.Interop.swconst;

namespace SwQc
{
    /// <summary>
    /// 把人工确认后的唯一 QcDocument 嵌入 SLDDRW。
    /// 双槽保存：新版本只写 A/B 中一个，另一槽保留上一有效版本；读取时选最高有效 revision。
    /// </summary>
    public sealed class QcDocumentStore : IDisposable
    {
        const string SlotA = "SwQcDocumentA";
        const string SlotB = "SwQcDocumentB";
        const string Format = "SwQc.QcDocument.v2";
        readonly IModelDoc2 _doc;
        DDrawingDocEvents_Event _events;
        string _committedEnvelope = "";
        int _committedRevision;
        string _pendingEnvelope = "";
        int _pendingRevision;
        bool _writeEventSeen, _writeEventOk;
        bool _disposed;

        public QcDocumentStore(IModelDoc2 doc)
        {
            if (doc == null) throw new ArgumentNullException("doc");
            _doc = doc;
            _events = doc as DDrawingDocEvents_Event;
            if (_events != null)
            {
                _events.LoadFromStorageNotify += OnLoadFromStorage;
                _events.SaveToStorageNotify += OnSaveToStorage;
                _events.AutoSaveToStorageNotify += OnAutoSaveToStorage;
            }
            string ignored;
            ReadBestEnvelope(out _committedEnvelope, out _committedRevision, out ignored);
        }

        public bool TryLoadDocument(out QcDocument document, out string error)
        {
            document = null; error = "";
            string envelope; int revision;
            if (!ReadBestEnvelope(out envelope, out revision, out error)) return false;
            if (string.IsNullOrEmpty(envelope)) return false;
            string payload; int decodedRevision;
            if (!TryDecodeEnvelope(envelope, out payload, out decodedRevision, out error)) return false;
            try
            {
                QcDocument loaded = QcDocument.FromSnapshotJson(payload);
                string canonical = loaded.ToSnapshotJson();
                if (!string.Equals(canonical, payload, StringComparison.Ordinal))
                { error = "文档内数据回读后逐字段不一致"; return false; }
                _committedEnvelope = envelope; _committedRevision = revision;
                document = loaded; return true;
            }
            catch (Exception ex) { error = "文档内数据无效：" + ex.Message; return false; }
        }

        public bool SaveVerified(QcDocument document, out QcDocument reloaded, out string error)
        {
            reloaded = null; error = "";
            if (_disposed) { error = "文档存储已关闭"; return false; }
            if (document == null) { error = "没有可保存的统一文档"; return false; }
            string validation;
            if (!document.ValidateForPersistence(out validation))
            { error = "保存前一致性校验失败：" + validation; return false; }
            string path = "";
            try { path = _doc.GetPathName() ?? ""; } catch { }
            if (string.IsNullOrWhiteSpace(path))
            { error = "工程图尚未保存到磁盘，请先在 SolidWorks 中保存工程图"; return false; }

            string payload = document.ToSnapshotJson();
            try
            {
                QcDocument probe = QcDocument.FromSnapshotJson(payload);
                if (!string.Equals(probe.ToSnapshotJson(), payload, StringComparison.Ordinal))
                { error = "保存前序列化回读不一致"; return false; }
            }
            catch (Exception ex) { error = "保存前回读校验失败：" + ex.Message; return false; }

            int revision = Math.Max(_committedRevision, 0) + 1;
            string envelope = EncodeEnvelope(payload, revision, document.DrawingNo);
            _pendingEnvelope = envelope; _pendingRevision = revision;
            _writeEventSeen = false; _writeEventOk = false;
            int saveErr = 0, saveWarn = 0;
            bool saved = false;
            try { saved = _doc.Save3((int)swSaveAsOptions_e.swSaveAsOptions_Silent, ref saveErr, ref saveWarn); }
            catch (Exception ex) { error = "SolidWorks 保存失败：" + ex.Message; }
            if (!saved || saveErr != 0)
            {
                _pendingEnvelope = ""; _pendingRevision = 0;
                if (string.IsNullOrEmpty(error)) error = "SolidWorks 保存失败，错误码 " + saveErr + "，警告码 " + saveWarn;
                return false;
            }
            if (!_writeEventSeen || !_writeEventOk)
            {
                _pendingEnvelope = ""; _pendingRevision = 0;
                error = "SolidWorks 未确认写入插件数据，已拒绝报告保存成功";
                return false;
            }

            string readEnvelope, readPayload, readError; int readRevision, decodedRevision;
            if (!ReadBestEnvelope(out readEnvelope, out readRevision, out readError) ||
                string.IsNullOrEmpty(readEnvelope) ||
                !TryDecodeEnvelope(readEnvelope, out readPayload, out decodedRevision, out readError))
            {
                _pendingEnvelope = ""; _pendingRevision = 0;
                error = "保存后无法回读：" + readError; return false;
            }
            if (readRevision != revision || !string.Equals(readPayload, payload, StringComparison.Ordinal))
            {
                _pendingEnvelope = ""; _pendingRevision = 0;
                error = "保存后回读内容或版本不一致，已拒绝加载"; return false;
            }
            try
            {
                QcDocument loaded = QcDocument.FromSnapshotJson(readPayload);
                if (!string.Equals(loaded.ToSnapshotJson(), payload, StringComparison.Ordinal))
                { error = "保存后逐字段复验不一致"; return false; }
                _committedEnvelope = readEnvelope; _committedRevision = readRevision;
                _pendingEnvelope = ""; _pendingRevision = 0;
                reloaded = loaded; return true;
            }
            catch (Exception ex) { error = "保存后结构校验失败：" + ex.Message; return false; }
        }

        int OnLoadFromStorage()
        {
            try { string e, err; int r; if (ReadBestEnvelope(out e, out r, out err)) { _committedEnvelope = e; _committedRevision = r; } }
            catch { }
            return 0;
        }

        int OnSaveToStorage() { return WriteCurrentEnvelope(); }
        int OnAutoSaveToStorage() { return WriteCurrentEnvelope(); }

        int WriteCurrentEnvelope()
        {
            _writeEventSeen = true; _writeEventOk = false;
            string envelope = !string.IsNullOrEmpty(_pendingEnvelope) ? _pendingEnvelope : _committedEnvelope;
            int revision = !string.IsNullOrEmpty(_pendingEnvelope) ? _pendingRevision : _committedRevision;
            if (string.IsNullOrEmpty(envelope) || revision <= 0) { _writeEventOk = true; return 0; }
            string error;
            _writeEventOk = WriteSlot((revision % 2 == 1) ? SlotA : SlotB, envelope, out error);
            return _writeEventOk ? 0 : 1;
        }

        bool ReadBestEnvelope(out string envelope, out int revision, out string error)
        {
            envelope = ""; revision = 0; error = "";
            string a, b, ea, eb; int ra = 0, rb = 0; string pa, pb;
            bool hasA = ReadSlot(SlotA, out a, out ea) && !string.IsNullOrEmpty(a)
                && TryDecodeEnvelope(a, out pa, out ra, out ea);
            bool hasB = ReadSlot(SlotB, out b, out eb) && !string.IsNullOrEmpty(b)
                && TryDecodeEnvelope(b, out pb, out rb, out eb);
            if (!hasA && !hasB)
            {
                if (!string.IsNullOrEmpty(ea) || !string.IsNullOrEmpty(eb))
                    error = "两个文档数据槽均无效：" + ea + (string.IsNullOrEmpty(eb) ? "" : "；" + eb);
                return string.IsNullOrEmpty(error);
            }
            if (hasA && (!hasB || ra >= rb)) { envelope = a; revision = ra; }
            else { envelope = b; revision = rb; }
            return true;
        }

        bool ReadSlot(string name, out string text, out string error)
        {
            text = ""; error = ""; object raw = null;
            try
            {
                raw = _doc.IGet3rdPartyStorage(name, false);
                if (raw == null) return true;
                IStream stream = raw as IStream;
                if (stream == null) { error = name + " 不是有效流"; return false; }
                System.Runtime.InteropServices.ComTypes.STATSTG stat; stream.Stat(out stat, 1);
                if (stat.cbSize <= 0) return true;
                if (stat.cbSize > 32 * 1024 * 1024) { error = name + " 数据过大"; return false; }
                byte[] bytes = new byte[(int)stat.cbSize];
                IntPtr count = Marshal.AllocCoTaskMem(4);
                try
                {
                    stream.Seek(0, 0, IntPtr.Zero); stream.Read(bytes, bytes.Length, count);
                    int n = Marshal.ReadInt32(count);
                    text = Encoding.UTF8.GetString(bytes, 0, Math.Max(0, Math.Min(n, bytes.Length)));
                }
                finally { Marshal.FreeCoTaskMem(count); }
                return true;
            }
            catch (Exception ex) { error = name + " 读取失败：" + ex.Message; return false; }
            finally { if (raw != null) try { _doc.IRelease3rdPartyStorage(name); } catch { } }
        }

        bool WriteSlot(string name, string text, out string error)
        {
            error = ""; object raw = null;
            try
            {
                raw = _doc.IGet3rdPartyStorage(name, true);
                IStream stream = raw as IStream;
                if (stream == null) { error = name + " 无法创建数据流"; return false; }
                byte[] bytes = Encoding.UTF8.GetBytes(text ?? "");
                stream.SetSize(bytes.Length); stream.Seek(0, 0, IntPtr.Zero);
                IntPtr count = Marshal.AllocCoTaskMem(4);
                try
                {
                    stream.Write(bytes, bytes.Length, count);
                    if (Marshal.ReadInt32(count) != bytes.Length) { error = name + " 写入长度不完整"; return false; }
                    stream.Commit(0);
                }
                finally { Marshal.FreeCoTaskMem(count); }
                return true;
            }
            catch (Exception ex) { error = name + " 写入失败：" + ex.Message; return false; }
            finally { if (raw != null) try { _doc.IRelease3rdPartyStorage(name); } catch { } }
        }

        public static string EncodeEnvelope(string payload, int revision, string drawingNo)
        {
            var d = new Dictionary<string, object>();
            d["format"] = Format; d["revision"] = revision; d["drawing_no"] = drawingNo ?? "";
            d["saved_utc"] = DateTime.UtcNow.ToString("o"); d["sha256"] = Sha256(payload ?? "");
            d["payload"] = payload ?? "";
            return new JavaScriptSerializer().Serialize(d);
        }

        public static bool TryDecodeEnvelope(string envelope, out string payload, out int revision, out string error)
        {
            payload = ""; revision = 0; error = "";
            try
            {
                var d = new JavaScriptSerializer().DeserializeObject(envelope) as Dictionary<string, object>;
                if (d == null || Convert.ToString(d["format"]) != Format) { error = "格式标识不匹配"; return false; }
                payload = Convert.ToString(d["payload"]) ?? "";
                if (!int.TryParse(Convert.ToString(d["revision"]), out revision) || revision <= 0)
                { error = "revision 无效"; return false; }
                string expected = Convert.ToString(d["sha256"]) ?? "";
                if (!string.Equals(expected, Sha256(payload), StringComparison.OrdinalIgnoreCase))
                { error = "SHA-256 校验失败"; return false; }
                return true;
            }
            catch (Exception ex) { error = ex.Message; return false; }
        }

        static string Sha256(string text)
        {
            using (SHA256 sha = SHA256.Create())
            {
                byte[] h = sha.ComputeHash(Encoding.UTF8.GetBytes(text ?? ""));
                var sb = new StringBuilder(h.Length * 2);
                foreach (byte b in h) sb.Append(b.ToString("x2"));
                return sb.ToString();
            }
        }

        public void Dispose()
        {
            if (_disposed) return; _disposed = true;
            if (_events != null)
            {
                try { _events.LoadFromStorageNotify -= OnLoadFromStorage; } catch { }
                try { _events.SaveToStorageNotify -= OnSaveToStorage; } catch { }
                try { _events.AutoSaveToStorageNotify -= OnAutoSaveToStorage; } catch { }
            }
            _events = null;
        }
    }
}
