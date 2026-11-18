using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Text;
using System.Threading;
using System.Web.Script.Serialization;

namespace SwQc
{
    /// <summary>启动独立 PDF Python 引擎；不调用原生审图脚本。</summary>
    public static class PdfAuditRunner
    {
        private class PdfConfig
        {
            public string Python = "";
            public string Script = "";
            public string TestScript = "";
            public string Provider = "";
            public string Base = "";
            public string Model = "";
            public string Key = "";
            public string Proxy = "";
            public int Timeout = 600;
        }

        private static string Quote(string value)
        {
            return "\"" + (value ?? "").Replace("\"", "\\\"") + "\"";
        }

        private static PdfConfig LoadPdfConfig()
        {
            var baseCfg = QcRunner.LoadConfig();
            var c = new PdfConfig();
            c.Python = baseCfg.Python ?? "";
            // 视觉通道配置严格独立，不回退到原生通道的 ai_*，避免误用纯文本模型或改坏原通道。
            c.Provider = "";
            c.Base = "";
            c.Model = "";
            c.Key = "";
            c.Proxy = "";
            // PDF 视觉请求有独立时限，不能被通用审核的 timeout= 覆盖。
            // K3.1 对整页图纸的视觉推理通常比原生尺寸检查耗时更长。
            c.Timeout = 600;

            string dllDir = Path.GetDirectoryName(typeof(PdfAuditRunner).Assembly.Location);
            string ini = Path.Combine(dllDir, "qc_addin_config.ini");
            if (File.Exists(ini))
            {
                foreach (string raw in File.ReadAllLines(ini, Encoding.UTF8))
                {
                    string line = raw.Trim();
                    if (line.Length == 0 || line.StartsWith("#") || line.StartsWith(";")) continue;
                    int eq = line.IndexOf('=');
                    if (eq <= 0) continue;
                    string key = line.Substring(0, eq).Trim().ToLower();
                    string val = line.Substring(eq + 1).Trim();
                    if (key == "pdf_ai_provider") c.Provider = val;
                    else if (key == "pdf_ai_base") c.Base = val;
                    else if (key == "pdf_ai_model") c.Model = val;
                    else if (key == "pdf_ai_key") c.Key = val;
                    else if (key == "pdf_ai_proxy") c.Proxy = val;
                    else if (key == "pdf_timeout") { int n; if (int.TryParse(val, out n)) c.Timeout = Math.Max(60, n); }
                }
            }
            var candidates = new List<string>();
            candidates.Add(Path.Combine(dllDir, "pdf_whole_audit.py"));
            candidates.Add(Path.Combine(dllDir, "sw-qc-ai", "pdf_whole_audit.py"));
            if (!string.IsNullOrEmpty(baseCfg.Native))
                candidates.Add(Path.Combine(Path.GetDirectoryName(baseCfg.Native), "pdf_whole_audit.py"));
            foreach (string p in candidates)
            {
                if (File.Exists(p)) { c.Script = p; break; }
            }
            var testCandidates = new List<string>();
            testCandidates.Add(Path.Combine(dllDir, "pdf_connection_test.py"));
            testCandidates.Add(Path.Combine(dllDir, "sw-qc-ai", "pdf_connection_test.py"));
            if (!string.IsNullOrEmpty(baseCfg.Native))
                testCandidates.Add(Path.Combine(Path.GetDirectoryName(baseCfg.Native), "pdf_connection_test.py"));
            foreach (string p in testCandidates)
            {
                if (File.Exists(p)) { c.TestScript = p; break; }
            }
            return c;
        }

        public static bool ValidateConfiguration(out string message)
        {
            PdfConfig c = LoadPdfConfig();
            if (string.IsNullOrEmpty(c.Python) || !File.Exists(c.Python))
            {
                message = "找不到 Python，请检查 qc_addin_config.ini 的 python=。";
                return false;
            }
            if (string.IsNullOrEmpty(c.Script) || !File.Exists(c.Script))
            {
                message = "找不到独立 PDF 引擎 pdf_whole_audit.py。";
                return false;
            }
            if (string.IsNullOrEmpty(c.Provider) || string.IsNullOrEmpty(c.Model) || string.IsNullOrEmpty(c.Key))
            {
                message = "尚未配置 PDF 视觉模型。请选择千问、Kimi 或其他支持图片输入的模型，并填写 API Key。";
                return false;
            }
            string probe = (c.Provider + " " + c.Base + " " + c.Model).ToLower();
            if (probe.Contains("deepseek") || probe.Contains("text-"))
            {
                message = "当前 PDF 模型是纯文本模型，不能读取工程图。请改用千问或 Kimi 视觉模型。";
                return false;
            }
            message = c.Provider + " / " + c.Model;
            return true;
        }

        /// <summary>使用当前输入配置发送一张小图片，验证网络、鉴权、模型名和视觉输入能力。</summary>
        public static bool TestConnection(string provider, string baseUrl, string model,
            string key, string proxy, out string message)
        {
            PdfConfig c = LoadPdfConfig();
            if (string.IsNullOrEmpty(c.Python) || !File.Exists(c.Python))
            {
                message = "找不到 Python 运行环境。";
                return false;
            }
            if (string.IsNullOrEmpty(c.TestScript) || !File.Exists(c.TestScript))
            {
                message = "找不到视觉连接测试脚本 pdf_connection_test.py。";
                return false;
            }
            if (string.IsNullOrWhiteSpace(provider) || string.IsNullOrWhiteSpace(baseUrl) ||
                string.IsNullOrWhiteSpace(model) || string.IsNullOrWhiteSpace(key))
            {
                message = "请先完整填写提供商、API 地址、视觉模型和 API Key。";
                return false;
            }
            var psi = new ProcessStartInfo();
            psi.FileName = c.Python;
            psi.Arguments = Quote(c.TestScript) + " --timeout 120";
            psi.UseShellExecute = false;
            psi.CreateNoWindow = true;
            psi.RedirectStandardOutput = true;
            psi.RedirectStandardError = true;
            psi.StandardOutputEncoding = Encoding.UTF8;
            psi.StandardErrorEncoding = Encoding.UTF8;
            psi.EnvironmentVariables["PYTHONIOENCODING"] = "utf-8";
            psi.EnvironmentVariables["PDF_AI_PROVIDER"] = provider.Trim();
            psi.EnvironmentVariables["PDF_AI_BASE"] = baseUrl.Trim();
            psi.EnvironmentVariables["PDF_AI_MODEL"] = model.Trim();
            psi.EnvironmentVariables["PDF_AI_KEY"] = key.Trim();
            if (!string.IsNullOrWhiteSpace(proxy))
            {
                psi.EnvironmentVariables["HTTP_PROXY"] = proxy.Trim();
                psi.EnvironmentVariables["HTTPS_PROXY"] = proxy.Trim();
            }
            var stdout = new StringBuilder();
            var stderr = new StringBuilder();
            try
            {
                using (var p = new Process())
                {
                    p.StartInfo = psi;
                    p.OutputDataReceived += delegate(object sender, DataReceivedEventArgs e)
                    {
                        if (e.Data != null) lock (stdout) stdout.AppendLine(e.Data);
                    };
                    p.ErrorDataReceived += delegate(object sender, DataReceivedEventArgs e)
                    {
                        if (e.Data != null) lock (stderr) stderr.AppendLine(e.Data);
                    };
                    p.Start();
                    p.BeginOutputReadLine();
                    p.BeginErrorReadLine();
                    DateTime started = DateTime.UtcNow;
                    while (!p.WaitForExit(250))
                    {
                        if ((DateTime.UtcNow - started).TotalSeconds > 130)
                        {
                            try { p.Kill(); } catch { }
                            message = "连接测试超时（130 秒）。";
                            return false;
                        }
                    }
                    p.WaitForExit();
                }
                string output = stdout.ToString();
                const string ok = "@@CONNECTION_OK@@";
                const string error = "@@CONNECTION_ERROR@@";
                int okAt = output.IndexOf(ok, StringComparison.Ordinal);
                if (okAt >= 0)
                {
                    string detail = output.Substring(okAt + ok.Length).Trim();
                    message = "连接成功，图片输入正常：" + detail;
                    return true;
                }
                int errorAt = output.IndexOf(error, StringComparison.Ordinal);
                if (errorAt >= 0)
                    message = output.Substring(errorAt + error.Length).Trim();
                else
                    message = "连接失败：" + (stderr.Length > 0 ? stderr.ToString().Trim() : output.Trim());
                return false;
            }
            catch (Exception ex)
            {
                message = "连接测试启动失败：" + ex.Message;
                return false;
            }
        }

        private static string ParseProgressMessage(string line)
        {
            const string prefix = "@@PROGRESS@@";
            if (string.IsNullOrEmpty(line) || !line.StartsWith(prefix)) return "";
            try
            {
                var js = new JavaScriptSerializer();
                var d = js.Deserialize<Dictionary<string, object>>(line.Substring(prefix.Length));
                object value;
                return d != null && d.TryGetValue("message", out value) ? Convert.ToString(value) : "";
            }
            catch { return ""; }
        }

        public static PdfAuditReport RunExtraction(string pdfPath, string jobDir, int expectedPages,
            Action<string> onProgress, Func<bool> isCancelled)
        {
            return RunPhase(pdfPath, jobDir, expectedPages, "extract", "", onProgress, isCancelled);
        }

        public static PdfAuditReport RunConfirmedAudit(string pdfPath, string jobDir, int expectedPages,
            string confirmedPath, Action<string> onProgress, Func<bool> isCancelled)
        {
            return RunPhase(pdfPath, jobDir, expectedPages, "audit", confirmedPath, onProgress, isCancelled);
        }

        /// <summary>兼容开发期命令行自测：完整自动跑，不经过界面人工确认。</summary>
        public static PdfAuditReport Run(string pdfPath, string jobDir, int expectedPages,
            Action<string> onProgress, Func<bool> isCancelled)
        {
            return RunPhase(pdfPath, jobDir, expectedPages, "full", "", onProgress, isCancelled);
        }

        private static PdfAuditReport RunPhase(string pdfPath, string jobDir, int expectedPages,
            string mode, string confirmedPath, Action<string> onProgress, Func<bool> isCancelled)
        {
            var fail = new PdfAuditReport();
            // 任何提前返回都必须明确标记失败；否则界面会把引擎异常误显示成
            // “审核结果不完整、0 条问题”，掩盖真实原因。
            fail.status = "failed";
            fail.JobDirectory = jobDir;
            if (!File.Exists(pdfPath)) { fail.error = "PDF 不存在：" + pdfPath; return fail; }
            var cfg = LoadPdfConfig();
            if (string.IsNullOrEmpty(cfg.Python) || !File.Exists(cfg.Python))
            {
                fail.error = "找不到 Python，请检查 qc_addin_config.ini 的 python=。";
                return fail;
            }
            if (string.IsNullOrEmpty(cfg.Script) || !File.Exists(cfg.Script))
            {
                fail.error = "找不到独立 PDF 引擎 pdf_whole_audit.py。";
                return fail;
            }
            Directory.CreateDirectory(jobDir);
            var psi = new ProcessStartInfo();
            psi.FileName = cfg.Python;
            psi.Arguments = Quote(cfg.Script) + " " + Quote(pdfPath) +
                            " --out " + Quote(jobDir) +
                            " --expected-pages " + expectedPages +
                            " --timeout " + cfg.Timeout + " --keep-artifacts --mode " + mode;
            if (!string.IsNullOrEmpty(confirmedPath))
                psi.Arguments += " --confirmed " + Quote(confirmedPath);
            psi.UseShellExecute = false;
            psi.CreateNoWindow = true;
            psi.RedirectStandardOutput = true;
            psi.RedirectStandardError = true;
            psi.StandardOutputEncoding = Encoding.UTF8;
            psi.StandardErrorEncoding = Encoding.UTF8;
            psi.EnvironmentVariables["PYTHONIOENCODING"] = "utf-8";
            psi.EnvironmentVariables["PDF_AI_PROVIDER"] = cfg.Provider ?? "";
            psi.EnvironmentVariables["PDF_AI_BASE"] = cfg.Base ?? "";
            psi.EnvironmentVariables["PDF_AI_MODEL"] = cfg.Model ?? "";
            psi.EnvironmentVariables["PDF_AI_KEY"] = cfg.Key ?? "";
            if (!string.IsNullOrEmpty(cfg.Proxy))
            {
                psi.EnvironmentVariables["HTTP_PROXY"] = cfg.Proxy;
                psi.EnvironmentVariables["HTTPS_PROXY"] = cfg.Proxy;
            }

            var stdout = new StringBuilder();
            var stderr = new StringBuilder();
            try
            {
                using (var p = new Process())
                {
                    p.StartInfo = psi;
                    p.OutputDataReceived += delegate(object sender, DataReceivedEventArgs e)
                    {
                        if (e.Data == null) return;
                        lock (stdout) stdout.AppendLine(e.Data);
                        string msg = ParseProgressMessage(e.Data);
                        if (!string.IsNullOrEmpty(msg) && onProgress != null) onProgress(msg);
                    };
                    p.ErrorDataReceived += delegate(object sender, DataReceivedEventArgs e)
                    {
                        if (e.Data != null) lock (stderr) stderr.AppendLine(e.Data);
                    };
                    p.Start();
                    p.BeginOutputReadLine();
                    p.BeginErrorReadLine();
                    var start = DateTime.UtcNow;
                    while (!p.WaitForExit(250))
                    {
                        if (isCancelled != null && isCancelled())
                        {
                            try { p.Kill(); } catch { }
                            fail.error = "用户已取消 PDF 整图审核。";
                            return fail;
                        }
                        if ((DateTime.UtcNow - start).TotalSeconds > cfg.Timeout * 4)
                        {
                            try { p.Kill(); } catch { }
                            fail.error = "PDF 整图审核总超时。";
                            return fail;
                        }
                    }
                    p.WaitForExit();
                }
            }
            catch (Exception ex)
            {
                fail.error = "启动 PDF 审核引擎失败：" + ex.Message;
                return fail;
            }

            string reportPath = Path.Combine(jobDir, "pdf_whole_audit_report.json");
            if (!File.Exists(reportPath))
            {
                fail.error = "PDF 引擎没有生成报告。" + Environment.NewLine + stderr.ToString();
                return fail;
            }
            try
            {
                var js = new JavaScriptSerializer();
                js.MaxJsonLength = int.MaxValue;
                var report = js.Deserialize<PdfAuditReport>(File.ReadAllText(reportPath, Encoding.UTF8));
                if (report == null) report = fail;
                report.ReportPath = reportPath;
                report.JobDirectory = jobDir;
                return report;
            }
            catch (Exception ex)
            {
                fail.error = "读取 PDF 结构化报告失败：" + ex.Message;
                return fail;
            }
        }

        public static PdfConfirmationDraft LoadConfirmationDraft(string path)
        {
            if (string.IsNullOrEmpty(path) || !File.Exists(path))
                throw new FileNotFoundException("找不到 PDF 人工确认草稿。", path);
            var js = new JavaScriptSerializer();
            js.MaxJsonLength = int.MaxValue;
            var draft = js.Deserialize<PdfConfirmationDraft>(File.ReadAllText(path, Encoding.UTF8));
            if (draft == null) throw new InvalidDataException("PDF 人工确认草稿格式不正确。");
            return draft;
        }

        public static string SaveConfirmed(PdfConfirmationDraft draft, List<PdfConfirmRow> rows, string jobDir)
        {
            if (draft == null) throw new ArgumentNullException("draft");
            rows = rows ?? new List<PdfConfirmRow>();
            List<PdfConfirmRow> draftRows = draft.rows ?? new List<PdfConfirmRow>();
            var expected = new HashSet<string>(StringComparer.Ordinal);
            foreach (PdfConfirmRow row in draftRows)
            {
                string key = (row.object_type ?? "") + "|" + (row.entity_id ?? "") + "|" + row.page;
                if (!expected.Add(key))
                    throw new InvalidDataException("系统识别草稿包含重复实体编号，已停止审核。");
            }
            var actual = new HashSet<string>(StringComparer.Ordinal);
            var entityIds = new HashSet<string>(StringComparer.Ordinal);
            var dimensionIds = new HashSet<string>(StringComparer.Ordinal);
            foreach (PdfConfirmRow row in rows)
                if (row != null && row.object_type == "dimension" && !row.excluded)
                    dimensionIds.Add(row.entity_id ?? "");
            var datumLetters = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
            foreach (PdfConfirmRow row in rows)
                if (row != null && row.object_type == "datum" && !row.excluded &&
                    !string.IsNullOrWhiteSpace(row.letter))
                    datumLetters.Add(row.letter.Trim());
            foreach (PdfConfirmRow row in rows)
            {
                if (row == null || !row.confirmed)
                    throw new InvalidDataException("仍有尺寸、公差、基准或归属关系未经人工确认。");
                string rowKey = (row.object_type ?? "") + "|" + (row.entity_id ?? "") + "|" + row.page;
                if (!actual.Add(rowKey))
                    throw new InvalidDataException("人工确认数据包含重复实体编号，已停止审核。");
                if (!entityIds.Add(row.entity_id ?? ""))
                    throw new InvalidDataException("人工确认数据中的对象 ID 重复：" + (row.entity_id ?? ""));
                if (!expected.Contains(rowKey) && !row.manual_added)
                    throw new InvalidDataException("识别集合之外的对象必须标记为人工新增。");
                if (row.excluded) continue;
                if (row.page <= 0)
                    throw new InvalidDataException("对象 " + (row.entity_id ?? "") + " 的页码无效。");
                // 普通尺寸只要求图上标注可读；“对应要素/功能”是系统结合视图和上下文
                // 给出的推断，不得强迫用户为每个尺寸逐项定义。
                if (row.object_type == "dimension" && string.IsNullOrWhiteSpace(row.display))
                    throw new InvalidDataException("尺寸完整标注尚未确认。");
                if (row.object_type == "gtol")
                {
                    if (string.IsNullOrWhiteSpace(row.frame_lines_text))
                        throw new InvalidDataException("几何公差框尚未确认。");
                    foreach (string frameLine in row.frame_lines_text.Split(new[] { '\r', '\n' },
                        StringSplitOptions.RemoveEmptyEntries))
                    {
                        string[] parts = frameLine.Split('|');
                        if (parts.Length < 2 || string.IsNullOrWhiteSpace(parts[0]) ||
                            string.IsNullOrWhiteSpace(parts[1]))
                            throw new InvalidDataException("几何公差框格式不完整；每层必须包含符号和公差值。");
                        for (int i = 3; i < parts.Length && i < 6; i++)
                        {
                            string datum = (parts[i] ?? "").Trim();
                            if (datum.Length > 0 && !datumLetters.Contains(datum))
                                throw new InvalidDataException("几何公差引用的基准 " + datum + " 不存在或已排除。");
                        }
                    }
                    if (row.target_type == "dimension" &&
                        !dimensionIds.Contains(row.target_entity_id ?? ""))
                        throw new InvalidDataException("几何公差挂接的尺寸不存在。");
                    if (row.target_type != "dimension" && string.IsNullOrWhiteSpace(row.target_feature))
                        throw new InvalidDataException("几何公差的被测要素尚未确认。");
                }
                if (row.object_type == "datum")
                {
                    if (string.IsNullOrWhiteSpace(row.letter) ||
                        (string.IsNullOrWhiteSpace(row.target_feature) &&
                         string.IsNullOrWhiteSpace(row.target_entity_id)))
                        throw new InvalidDataException("基准字母或指向要素尚未确认。");
                    if (!string.IsNullOrWhiteSpace(row.target_entity_id) &&
                        !dimensionIds.Contains(row.target_entity_id))
                        throw new InvalidDataException("基准关联的尺寸不存在。");
                }
            }
            if (!expected.IsSubsetOf(actual))
                throw new InvalidDataException("原识别对象有遗漏；请逐项确认或明确标记为误识别。");
            var payload = new PdfConfirmedPayload();
            payload.schema_version = string.IsNullOrEmpty(draft.schema_version) ?
                "pdf-confirmation-1.1" : draft.schema_version;
            payload.source_sha256 = draft.source == null ? "" : (draft.source.sha256 ?? "");
            payload.confirmed_at = DateTime.UtcNow.ToString("o");
            payload.rows = rows;
            string path = Path.Combine(jobDir, "pdf_confirmation_confirmed.json");
            string tempPath = path + ".tmp-" + Guid.NewGuid().ToString("N");
            var js = new JavaScriptSerializer();
            js.MaxJsonLength = int.MaxValue;
            string serialized = js.Serialize(payload);
            try
            {
                // 先完整写临时文件，逐字节回读并反序列化；验证通过后再原子替换正式文件。
                File.WriteAllText(tempPath, serialized, new UTF8Encoding(false));
                string tempReadback = File.ReadAllText(tempPath, Encoding.UTF8);
                if (!string.Equals(tempReadback, serialized, StringComparison.Ordinal))
                    throw new InvalidDataException("人工确认数据临时文件回读不一致，已停止审核。");
                var verify = js.Deserialize<PdfConfirmedPayload>(tempReadback);
                if (verify == null || verify.rows == null || verify.rows.Count != payload.rows.Count ||
                    verify.source_sha256 != payload.source_sha256)
                    throw new InvalidDataException("人工确认 JSON 反序列化回读不一致，已停止审核。");
                if (File.Exists(path)) File.Replace(tempPath, path, null);
                else File.Move(tempPath, path);
                if (!string.Equals(File.ReadAllText(path, Encoding.UTF8), serialized,
                    StringComparison.Ordinal))
                    throw new InvalidDataException("人工确认正式文件回读不一致，已停止审核。");
            }
            finally
            {
                try { if (File.Exists(tempPath)) File.Delete(tempPath); } catch { }
            }
            return path;
        }
    }
}

