using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Text;
using System.Web.Script.Serialization;

namespace SwQc
{
    /// <summary>
    /// 调用 Python 引擎：把 DXF 交给 sw_qc_parser.py，读回 JSON 报告。
    /// 配置从 DLL 同目录的 qc_addin_config.ini 读取（安装脚本会生成）。
    /// </summary>
    public static class QcRunner
    {
        /// <summary>本会话“尺寸/公差页右键删除”的行指纹（kind|content|tol），关窗清空。</summary>
        public static List<string> ExcludedDimRows = new List<string>();
        /// <summary>只重读、不跑 AI（重读图纸按钮用）。</summary>
        public static bool SkipAiOnce = false;
        /// <summary>本会话手工新增/编辑的行（序列化成 JSON 传给引擎）。</summary>
        public static List<Dictionary<string, string>> ManualRows = new List<Dictionary<string, string>>();
        /// <summary>统一会话文档的结构化快照。检查/AI 只读此快照，不再从显示文字反推三表。</summary>
        public static string DocumentSnapshotJson = "";

        public static string PythonExe = "";

        /// <summary>人工指定的工艺类别（""=自动/AI 判定）。优先于 AI 与图面推断。</summary>
        public static string ForcedCategory = "";

        public static string CategoryMapFile()
        {
            string dir = "";
            try { dir = Path.GetDirectoryName(typeof(QcRunner).Assembly.Location) ?? ""; } catch { }
            if (string.IsNullOrEmpty(dir))
                dir = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "SwQcAddin");
            try { Directory.CreateDirectory(dir); } catch { }
            return Path.Combine(dir, "category_map.json");
        }

        public static Dictionary<string, string> LoadCategoryMap()
        {
            try
            {
                string f = CategoryMapFile();
                if (!File.Exists(f)) return new Dictionary<string, string>();
                var js = new JavaScriptSerializer();
                var d = js.Deserialize<Dictionary<string, string>>(File.ReadAllText(f, Encoding.UTF8));
                return d ?? new Dictionary<string, string>();
            }
            catch { return new Dictionary<string, string>(); }
        }

        public static string LoadCategoryFor(string dwgNo)
        {
            if (string.IsNullOrEmpty(dwgNo)) return "";
            var d = LoadCategoryMap();
            string v;
            return d.TryGetValue(dwgNo, out v) ? (v ?? "") : "";
        }

        // ---------- 几何公差「归属」记忆（按图号 + 公差签名） ----------
        public static string GtolLinkMapFile()
        {
            string dir = "";
            try { dir = Path.GetDirectoryName(typeof(QcRunner).Assembly.Location) ?? ""; } catch { }
            if (string.IsNullOrEmpty(dir))
                dir = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "SwQcAddin");
            try { Directory.CreateDirectory(dir); } catch { }
            return Path.Combine(dir, "gtol_link_map.json");
        }

        static Dictionary<string, Dictionary<string, string>> LoadAllGtolLinks()
        {
            try
            {
                string f = GtolLinkMapFile();
                if (!File.Exists(f)) return new Dictionary<string, Dictionary<string, string>>();
                var js = new JavaScriptSerializer();
                var d = js.Deserialize<Dictionary<string, Dictionary<string, string>>>(
                    File.ReadAllText(f, Encoding.UTF8));
                return d ?? new Dictionary<string, Dictionary<string, string>>();
            }
            catch { return new Dictionary<string, Dictionary<string, string>>(); }
        }

        public static string DatumMapFile()
        {
            string dir = "";
            try { dir = Path.GetDirectoryName(typeof(QcRunner).Assembly.Location) ?? ""; } catch { }
            if (string.IsNullOrEmpty(dir)) dir = Path.GetTempPath();
            return Path.Combine(dir, "datum_map.json");
        }

        /// <summary>取某图号的人工基准要素记忆：{A: 平面, B: 轴心（中心线）}</summary>
        public static Dictionary<string, string> LoadDatumMap(string dwgNo)
        {
            if (string.IsNullOrEmpty(dwgNo)) return new Dictionary<string, string>();
            try
            {
                string f = DatumMapFile();
                if (!System.IO.File.Exists(f)) return new Dictionary<string, string>();
                var js = new JavaScriptSerializer();
                var all = js.Deserialize<Dictionary<string, Dictionary<string, string>>>(
                    System.IO.File.ReadAllText(f, Encoding.UTF8));
                Dictionary<string, string> m;
                if (all != null && all.TryGetValue(dwgNo, out m) && m != null) return m;
            }
            catch { }
            return new Dictionary<string, string>();
        }

        public static void SaveDatumNear(string dwgNo, string letter, string nearContent)
        {
            SaveDatumKey(dwgNo, letter + "#near", nearContent);
        }

        public static void SaveDatum(string dwgNo, string letter, string kind)
        {
            SaveDatumKey(dwgNo, letter, kind);
        }

        static void SaveDatumKey(string dwgNo, string key, string kind)
        {
            if (string.IsNullOrEmpty(dwgNo) || string.IsNullOrEmpty(key)) return;
            try
            {
                var js = new JavaScriptSerializer();
                string f = DatumMapFile();
                Dictionary<string, Dictionary<string, string>> all =
                    new Dictionary<string, Dictionary<string, string>>();
                if (System.IO.File.Exists(f))
                {
                    try
                    {
                        all = js.Deserialize<Dictionary<string, Dictionary<string, string>>>(
                            System.IO.File.ReadAllText(f, Encoding.UTF8))
                            ?? new Dictionary<string, Dictionary<string, string>>();
                    }
                    catch { }
                }
                Dictionary<string, string> m;
                if (!all.TryGetValue(dwgNo, out m) || m == null)
                {
                    m = new Dictionary<string, string>();
                    all[dwgNo] = m;
                }
                if (string.IsNullOrEmpty(kind)) m.Remove(key);
                else m[key] = kind;
                System.IO.File.WriteAllText(f, js.Serialize(all), new UTF8Encoding(true));
            }
            catch { }
        }

        /// <summary>
        /// 仅清理同一尺寸行内部的重复几何公差，并同步旧版单值字段。
        /// 不允许跨尺寸行按“符号+数值+基准”去重：图纸上可以有多条内容相同、
        /// 但分别指向不同要素的几何公差。dim_analysis 已是引擎输出的最终尺寸表，
        /// C# 展示层必须原样信任它，不能再次根据人工记忆改写归属。
        /// </summary>
    public static void DedupeDimRows(QcReport rep, string dwgNo)
    {
        if (rep == null || rep.DimRows == null) return;
        try
        {
            foreach (QcDimRow row in rep.DimRows)
            {
                if (row == null) continue;
                var unique = new List<QcGtolLine>();
                var seen = new HashSet<string>();
                if (row.Gtols != null)
                {
                    foreach (QcGtolLine g in row.Gtols)
                    {
                        if (g == null) continue;
                        string key = !string.IsNullOrEmpty(g.GtolId) ? ("ID:" + g.GtolId)
                                   : (!string.IsNullOrEmpty(g.RawId) ? ("RAW:" + g.RawId)
                                      : GtolKey(g.Sym, g.Val, g.Dat1, g.Dat2, g.Mod, g.Dat3));
                        if (seen.Add(key)) unique.Add(g);
                    }
                }
                // `gtol` is the legacy single-value projection of gtols[0].
                // It is not a fourth tolerance.  Only use it when the modern
                // list is genuinely absent.
                if (unique.Count == 0 && !string.IsNullOrEmpty(row.GtolSym))
                {
                    string key2 = GtolKey(row.GtolSym, row.GtolVal, row.GtolDat1, row.GtolDat2, row.GtolMod, row.GtolDat3);
                    if (seen.Add(key2))
                    {
                        var g2 = new QcGtolLine();
                        g2.Sym = row.GtolSym; g2.Val = row.GtolVal;
                        g2.Dat1 = row.GtolDat1; g2.Dat2 = row.GtolDat2; g2.Dat3 = row.GtolDat3; g2.Mod = row.GtolMod;
                        unique.Add(g2);
                    }
                }
                row.Gtols = unique;
                if (unique.Count > 0)
                {
                    row.GtolSym = unique[0].Sym; row.GtolVal = unique[0].Val;
                    row.GtolDat1 = unique[0].Dat1; row.GtolDat2 = unique[0].Dat2; row.GtolDat3 = unique[0].Dat3;
                    row.GtolMod = unique[0].Mod;
                }
                else
                    row.GtolSym = row.GtolVal = row.GtolDat1 = row.GtolDat2 = row.GtolDat3 = row.GtolMod = "";
            }
        }
        catch { }
    }

    static string NormContent(string s)
    {
        string x = (s ?? "").Replace("Ⓔ", "").Replace("Ⓜ", "").Replace("⌀", "").Replace("φ", "")
                             .Replace("Ø", "").Replace(" ", "").Trim();
        return x;
    }

    public static Dictionary<string, string> LoadGtolLinks(string dwgNo)
        {
            if (string.IsNullOrEmpty(dwgNo)) return new Dictionary<string, string>();
            var all = LoadAllGtolLinks();
            Dictionary<string, string> m;
            return all.TryGetValue(dwgNo, out m) ? (m ?? new Dictionary<string, string>())
                                                 : new Dictionary<string, string>();
        }

        /// <summary>记住/更新一条：key=公差签名，dimContent=归属尺寸内容（空串=不指定）。</summary>
        public static void SaveGtolLink(string dwgNo, string key, string dimContent)
        {
            if (string.IsNullOrEmpty(dwgNo) || string.IsNullOrEmpty(key)) return;
            try
            {
                var all = LoadAllGtolLinks();
                Dictionary<string, string> m;
                if (!all.TryGetValue(dwgNo, out m) || m == null)
                {
                    m = new Dictionary<string, string>();
                    all[dwgNo] = m;
                }
                m[key] = dimContent ?? "";
                var js = new JavaScriptSerializer();
                File.WriteAllText(GtolLinkMapFile(), js.Serialize(all), new UTF8Encoding(true));
            }
            catch { }
        }

        /// <summary>公差签名：符号|值|基准1|基准2|修正符（视图不参与，避免视图改名失效）。</summary>

        /// <summary>v186：备注迁移（去 几何 N 条 / 手工新增 / 几何公差：… / 裸公差文本）</summary>
        public static string MigrateNote(string note)
        {
            var keep = new List<string>();
            foreach (string seg in (note ?? "").Split(new char[] { '；' }, StringSplitOptions.RemoveEmptyEntries))
            {
                string x = seg.Trim();
                if (x.Length == 0) continue;
                if (x.StartsWith("几何 ") || x.StartsWith("几何公差")) continue;
                if (x == "手工新增" || x.StartsWith("手工新增")) continue;
                if (x == "无基准") continue;
                char c0 = x[0];
                if ("—▱○⌭⌒⌓∠⊥∥⌖◎⌯↗⌰".IndexOf(c0) >= 0) continue;
                keep.Add(x);
            }
            return string.Join("；", keep.ToArray());
        }

        /// <summary>v186：行内几何公差归一化+去重，并回写旧单值字段</summary>
        public static void NormalizeRowGtols(QcDimRow r)
        {
            if (r == null) return;
            r.Note = MigrateNote(r.Note);
            var outList = new List<QcGtolLine>();
            var seen = new HashSet<string>();
            if (r.Gtols != null)
            {
                foreach (QcGtolLine g in r.Gtols)
                {
                    if (g == null) continue;
                    g.Sym = (g.Sym ?? "").Trim();
                    g.Val = (g.Val ?? "").Trim();
                    g.Mod = (g.Mod ?? "").Trim();
                    g.Dat1 = (g.Dat1 ?? "").Trim().ToUpperInvariant();
                    g.Dat2 = (g.Dat2 ?? "").Trim().ToUpperInvariant();
                    g.Dat3 = (g.Dat3 ?? "").Trim().ToUpperInvariant();
                    string k = !string.IsNullOrEmpty(g.GtolId) ? ("ID:" + g.GtolId)
                             : (!string.IsNullOrEmpty(g.RawId) ? ("RAW:" + g.RawId)
                                : GtolKey(g.Sym, g.Val, g.Dat1, g.Dat2, g.Mod, g.Dat3));
                    if (seen.Add(k)) outList.Add(g);
                }
            }
            r.Gtols = outList;
            if (outList.Count > 0)
            {
                r.GtolSym = outList[0].Sym; r.GtolVal = outList[0].Val;
                r.GtolDat1 = outList[0].Dat1; r.GtolDat2 = outList[0].Dat2; r.GtolDat3 = outList[0].Dat3;
                r.GtolMod = outList[0].Mod;
            }
            else
            {
                r.GtolSym = ""; r.GtolVal = ""; r.GtolDat1 = "";
                r.GtolDat2 = ""; r.GtolDat3 = ""; r.GtolMod = "";
            }
        }

        /// <summary>v186：整套报告行归一化</summary>
        public static void NormalizeReport(QcReport rep)
        {
            if (rep == null || rep.DimRows == null) return;
            foreach (QcDimRow r in rep.DimRows) NormalizeRowGtols(r);
        }

        /// <summary>v186：重读/关窗前的静默备份（不弹窗）</summary>
        public static string BackupManualRows(string dwgNo)
        {
            try
            {
                if (ManualRows == null || ManualRows.Count == 0) return "";
                string dir = Path.Combine(Path.GetTempPath(), "SwQcAddin", "backup");
                Directory.CreateDirectory(dir);
                string safe = string.IsNullOrEmpty(dwgNo) ? "unknown" : dwgNo;
                foreach (char bad in Path.GetInvalidFileNameChars()) safe = safe.Replace(bad, '_');
                string fn = Path.Combine(dir, safe + "_" + DateTime.Now.ToString("yyyyMMdd_HHmmss") + ".json");
                var js = new JavaScriptSerializer();
                File.WriteAllText(fn, js.Serialize(ManualRows), new UTF8Encoding(false));
                return fn;
            }
            catch { return ""; }
        }

        /// <summary>v186：清空本会话静态数据（关窗/重读用）</summary>
        public static void ResetSession()
        {
            try { ManualRows = new List<Dictionary<string, string>>(); } catch { }
            try { DocumentSnapshotJson = ""; } catch { }
            try { ExcludedDimRows = new List<string>(); } catch { }
            try { SkipAiOnce = false; } catch { }
        }

        public static string GtolKey(string sym, string val, string dat1, string dat2, string mod, string dat3 = "")
        {
            return (sym ?? "") + "|" + (val ?? "") + "|" + (dat1 ?? "") + "|" +
                   (dat2 ?? "") + "|" + (dat3 ?? "") + "|" + (mod ?? "");
        }

        /// <summary>清空某图号的几何公差归属记忆（重新指定时用）。</summary>
        public static void RemoveGtolLinks(string dwgNo)
        {
            if (string.IsNullOrEmpty(dwgNo)) return;
            try
            {
                var all = LoadAllGtolLinks();
                if (all.Remove(dwgNo))
                {
                    var js = new JavaScriptSerializer();
                    File.WriteAllText(GtolLinkMapFile(), js.Serialize(all), new UTF8Encoding(true));
                }
            }
            catch { }
        }

        public static void SaveCategoryFor(string dwgNo, string cat)
        {
            if (string.IsNullOrEmpty(dwgNo)) return;
            try
            {
                var d = LoadCategoryMap();
                if (string.IsNullOrEmpty(cat) || cat.StartsWith("自动"))
                    d.Remove(dwgNo);
                else
                    d[dwgNo] = cat;
                var js = new JavaScriptSerializer();
                File.WriteAllText(CategoryMapFile(), js.Serialize(d), new UTF8Encoding(true));
            }
            catch { }
        }
        public static string ParserPy = "";
        public static string Rules = "gb_baseline";
        public static int TimeoutSec = 180;

        public class Config
        {
            public string Python;
            public string Parser;
            public string Rules;
            public string Native;
            public string AiKey;
            /// <summary>按厂商分别保存的 Key（slot: deepseek/chatgpt/qwen/zhipu/moonshot/custom）</summary>
            public Dictionary<string, string> AiKeys =
                new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
            public string AiBase;
            public string AiModel;
            public string AiProvider;
            public string AiProxy;
            /// <summary>标准体系：GB / ISO / ASME（决定默认公差原则与引用标准）</summary>
            public string Standard;
            /// <summary>结果窗口是否总在最前（1=是，默认 0；置顶会拖慢 SW 重绘）</summary>
            public string TopMost;
            public int Timeout;
        }

        /// <summary>读取配置；缺省时按常见位置探测。</summary>
        public static Config LoadConfig()
        {
            var c = new Config();
            string dllDir = Path.GetDirectoryName(typeof(QcRunner).Assembly.Location);
            string ini = Path.Combine(dllDir, "qc_addin_config.ini");
            if (File.Exists(ini))
            {
                foreach (string raw in File.ReadAllLines(ini, Encoding.UTF8))
                {
                    string line = raw.Trim();
                    if (line.Length == 0 || line.StartsWith("#") || line.StartsWith(";")) continue;
                    int eq = line.IndexOf('=');
                    if (eq <= 0) continue;
                    string k = line.Substring(0, eq).Trim().ToLower();
                    string v = line.Substring(eq + 1).Trim();
                    if (k == "python") c.Python = v;
                    else if (k == "parser") c.Parser = v;
                    else if (k == "rules") c.Rules = v;
                    else if (k == "native") c.Native = v;
                    else if (k.StartsWith("ai_key_")) c.AiKeys[k.Substring(7)] = v;
                    else if (k == "ai_key") c.AiKey = v;
                    else if (k == "ai_base") c.AiBase = v;
                    else if (k == "ai_model") c.AiModel = v;
                    else if (k == "ai_provider") c.AiProvider = v;
                    else if (k == "ai_proxy") c.AiProxy = v;
                    else if (k == "standard") c.Standard = v;
                    else if (k == "qc_topmost") c.TopMost = v;
                    else if (k == "principle") c.Standard = (v.StartsWith("包容") ? "ASME" : "GB");
                    else if (k == "timeout") int.TryParse(v, out c.Timeout);
                }
            }
            // 2026-10-25：按厂商存 Key。切厂商后从对应槽位取；
            // 只存了 ai_key 没存槽位的老配置也继续可用。
            if (!string.IsNullOrEmpty(c.AiProvider))
            {
                string slot = c.AiProvider.ToLower();
                if (string.IsNullOrEmpty(c.AiKey) && c.AiKeys.ContainsKey(slot))
                    c.AiKey = c.AiKeys[slot];
                else if (!string.IsNullOrEmpty(c.AiKey) && !c.AiKeys.ContainsKey(slot))
                    c.AiKeys[slot] = c.AiKey;
            }
            if (string.IsNullOrEmpty(c.Rules)) c.Rules = "gb_baseline";
            if (string.IsNullOrEmpty(c.Standard)) c.Standard = "GB";
            if (c.Timeout <= 0) c.Timeout = 180;
            return c;
        }


        /// <summary>
        /// 把单个配置项写回 ini（保留其它行与注释）。
        /// 用户 2026-10-24：检查设置里的选项要能记住。
        /// </summary>
        public static void SaveSetting(string key, string value)
        {
            try
            {
                string dllDir = Path.GetDirectoryName(typeof(QcRunner).Assembly.Location);
                string ini = Path.Combine(dllDir, "qc_addin_config.ini");
                var lines = new List<string>();
                if (File.Exists(ini)) lines.AddRange(File.ReadAllLines(ini, Encoding.UTF8));

                bool done = false;
                for (int i = 0; i < lines.Count; i++)
                {
                    string t = lines[i].Trim();
                    if (t.Length == 0 || t.StartsWith("#") || t.StartsWith(";")) continue;
                    int eq = t.IndexOf('=');
                    if (eq <= 0) continue;
                    if (t.Substring(0, eq).Trim().ToLower() == key.ToLower())
                    {
                        lines[i] = key + "=" + value;
                        done = true;
                        break;
                    }
                }
                if (!done) lines.Add(key + "=" + value);
                File.WriteAllLines(ini, lines.ToArray(), new UTF8Encoding(false));
            }
            catch { }
        }
        /// <summary>对一张 DXF 跑检查，返回报告；失败时 RawError 说明原因。</summary>
        public static QcReport Run(string dxfPath, string outDir)
        {
            var cfg = LoadConfig();
            var rep = new QcReport();
            rep.ReportDir = outDir;
            rep.Preset = cfg.Rules;

            if (string.IsNullOrEmpty(cfg.Python) || !File.Exists(cfg.Python))
            {
                rep.RawError = "未找到 Python：请在 qc_addin_config.ini 里配置 python=...\n" + (cfg.Python ?? "");
                return rep;
            }
            if (string.IsNullOrEmpty(cfg.Parser) || !File.Exists(cfg.Parser))
            {
                rep.RawError = "未找到 sw_qc_parser.py：请在 qc_addin_config.ini 里配置 parser=...\n" + (cfg.Parser ?? "");
                return rep;
            }
            if (!Directory.Exists(outDir)) Directory.CreateDirectory(outDir);

            var sw = Stopwatch.StartNew();
            var psi = new ProcessStartInfo();
            psi.FileName = cfg.Python;
            psi.Arguments = Quote(cfg.Parser) + " " + Quote(dxfPath) +
                            " --features --rules " + cfg.Rules +
                            " --json --preview --out " + Quote(outDir);
            psi.UseShellExecute = false;
            psi.CreateNoWindow = true;
            psi.RedirectStandardOutput = true;
            psi.RedirectStandardError = true;
            psi.StandardOutputEncoding = Encoding.UTF8;
            psi.StandardErrorEncoding = Encoding.UTF8;
            psi.EnvironmentVariables["PYTHONIOENCODING"] = "utf-8";
            if (!string.IsNullOrEmpty(cfg.AiKey)) psi.EnvironmentVariables["QC_AI_KEY"] = cfg.AiKey;
            if (!string.IsNullOrEmpty(cfg.AiBase)) psi.EnvironmentVariables["QC_AI_BASE"] = cfg.AiBase;
            // 2026-10-24 修：必须把 provider/model 一起传，否则 Python 用默认 gpt-4o-mini
            // 去请求 DeepSeek 会 HTTP 400，表现就是"AI 没有介入"。
            if (!string.IsNullOrEmpty(cfg.AiProvider)) psi.EnvironmentVariables["QC_AI_PROVIDER"] = cfg.AiProvider;
            if (!string.IsNullOrEmpty(cfg.AiModel)) psi.EnvironmentVariables["QC_AI_MODEL"] = cfg.AiModel;
            if (!string.IsNullOrEmpty(cfg.Standard)) psi.EnvironmentVariables["QC_STANDARD"] = cfg.Standard;

            // 人工指定过的「几何公差→尺寸」归属，交给 Python 在关联前套用（含 AI 输入）
            // v219：归属/基准只在当前 QcDocument 会话中存在，不再从磁盘记忆恢复。
            try
            {
                var js2 = new JavaScriptSerializer();
                psi.EnvironmentVariables["QC_DIM_EXCLUDE"] = js2.Serialize(ExcludedDimRows ?? new List<string>());
            }
            catch { }

            // 工艺类别（人工指定优先；空/自动 时交给 Python 推断）
            if (!string.IsNullOrEmpty(ForcedCategory) && !ForcedCategory.StartsWith("自动"))
            {
                try { psi.EnvironmentVariables["QC_FORCE_CATEGORY"] = ForcedCategory; } catch { }
            }
            if (!string.IsNullOrEmpty(cfg.AiProxy))
            {
                psi.EnvironmentVariables["HTTP_PROXY"] = cfg.AiProxy;
                psi.EnvironmentVariables["HTTPS_PROXY"] = cfg.AiProxy;
            }

            string stdout = "", stderr = "";
            try
            {
                using (var p = Process.Start(psi))
                {
                    var tOut = p.StandardOutput.ReadToEndAsync();
                    var tErr = p.StandardError.ReadToEndAsync();
                    if (!p.WaitForExit(cfg.Timeout * 1000))
                    {
                        try { p.Kill(); } catch { }
                        rep.RawError = "检查超时（超过 " + cfg.Timeout + " 秒）";
                        return rep;
                    }
                    stdout = tOut.Result;
                    stderr = tErr.Result;
                    if (p.ExitCode != 0)
                    {
                        rep.RawError = "检查进程异常退出（退出码 " + p.ExitCode + "）。\n" + Tail(stderr, 800);
                        return rep;
                    }
                }
            }
            catch (Exception ex)
            {
                rep.RawError = "调用 Python 失败：" + ex.Message;
                return rep;
            }
            rep.ElapsedSec = sw.Elapsed.TotalSeconds;

            string baseName = Path.GetFileNameWithoutExtension(dxfPath);
            string json = Path.Combine(outDir, baseName + ".qc_report.json");
            rep.JsonPath = json;
            if (!File.Exists(json))
            {
                rep.RawError = "没有生成报告文件。\n" + Tail(stderr, 800) + "\n" + Tail(stdout, 800);
                return rep;
            }

            string png = Path.Combine(outDir, baseName + ".preview.png");
            if (File.Exists(png)) rep.PreviewPath = png;

            try
            {
                ParseJson(json, rep);
            }
            catch (Exception ex)
            {
                rep.RawError = "报告解析失败：" + ex.Message;
            }
            return rep;
        }

        /// <summary>
        /// 原生审图：把 SW 直读出来的 .sw.json 交给 sw_native_qc.py 判定。
        /// 不再需要 DXF。
        /// </summary>
        public static QcReport RunNative(string swJsonPath, string outDir)
        {
            return RunNative(swJsonPath, outDir, false);
        }

        /// <summary>headerOnly=true 时只读表头（窗口先弹出来），不跑判定。</summary>
        public static QcReport RunNative(string swJsonPath, string outDir, bool headerOnly)
        {
            var cfg = LoadConfig();
            var rep = new QcReport();
            rep.ReportDir = outDir;
            rep.Preset = cfg.Rules;

            string nativePy = cfg.Native;
            if (string.IsNullOrEmpty(nativePy) && !string.IsNullOrEmpty(cfg.Parser))
                nativePy = Path.Combine(Path.GetDirectoryName(cfg.Parser), "sw_native_qc.py");
            if (string.IsNullOrEmpty(nativePy) || !File.Exists(nativePy))
            {
                rep.RawError = "未找到原生审图脚本 sw_native_qc.py。\n请在 qc_addin_config.ini 里配置 native=...";
                return rep;
            }
            if (string.IsNullOrEmpty(cfg.Python) || !File.Exists(cfg.Python))
            {
                rep.RawError = "未找到 Python：请在 qc_addin_config.ini 里配置 python=...";
                return rep;
            }
            if (!Directory.Exists(outDir)) Directory.CreateDirectory(outDir);

            var sw = Stopwatch.StartNew();
            var psi = new ProcessStartInfo();
            psi.FileName = cfg.Python;
            psi.Arguments = Quote(nativePy) + " " + Quote(swJsonPath) +
                            " --rules " + cfg.Rules + " --json" +
                            (headerOnly ? " --header-only" : "") + " --out " + Quote(outDir);
            psi.UseShellExecute = false;
            psi.CreateNoWindow = true;
            psi.RedirectStandardOutput = true;
            psi.RedirectStandardError = true;
            psi.StandardOutputEncoding = Encoding.UTF8;
            psi.StandardErrorEncoding = Encoding.UTF8;
            psi.EnvironmentVariables["PYTHONIOENCODING"] = "utf-8";
            if (!string.IsNullOrEmpty(cfg.AiKey)) psi.EnvironmentVariables["QC_AI_KEY"] = cfg.AiKey;
            if (!string.IsNullOrEmpty(cfg.AiBase)) psi.EnvironmentVariables["QC_AI_BASE"] = cfg.AiBase;
            // 2026-10-24 修：必须把 provider/model 一起传，否则 Python 用默认 gpt-4o-mini
            // 去请求 DeepSeek 会 HTTP 400，表现就是"AI 没有介入"。
            if (!string.IsNullOrEmpty(cfg.AiProvider)) psi.EnvironmentVariables["QC_AI_PROVIDER"] = cfg.AiProvider;
            if (!string.IsNullOrEmpty(cfg.AiModel)) psi.EnvironmentVariables["QC_AI_MODEL"] = cfg.AiModel;
            if (!string.IsNullOrEmpty(cfg.Standard)) psi.EnvironmentVariables["QC_STANDARD"] = cfg.Standard;

            // 2026-10-27 修：原生审图必须带这些“人工记忆/删除排除”，否则界面与记忆不一致
            // v219：归属/基准不跨窗口恢复；统一快照由 QC_DOCUMENT_SNAPSHOT 提供。
            try
            {
                var jsEx = new JavaScriptSerializer();
                psi.EnvironmentVariables["QC_DIM_EXCLUDE"] = jsEx.Serialize(ExcludedDimRows ?? new List<string>());
            }
            catch { }
            try
            {
                if (ManualRows != null && ManualRows.Count > 0)
                {
                    var jsMr = new JavaScriptSerializer();
                    psi.EnvironmentVariables["QC_MANUAL_ROWS"] = jsMr.Serialize(ManualRows);
                }
            }
            catch { }
            try
            {
                if (!string.IsNullOrEmpty(DocumentSnapshotJson))
                    psi.EnvironmentVariables["QC_DOCUMENT_SNAPSHOT"] = DocumentSnapshotJson;
            }
            catch { }
            try
            {
                if (SkipAiOnce)
                {
                    psi.EnvironmentVariables["QC_NO_AI"] = "1";
                    SkipAiOnce = false;
                }
            }
            catch { }

            if (!string.IsNullOrEmpty(cfg.AiProxy))
            {
                psi.EnvironmentVariables["HTTP_PROXY"] = cfg.AiProxy;
                psi.EnvironmentVariables["HTTPS_PROXY"] = cfg.AiProxy;
            }

            string stdout = "", stderr = "";
            try
            {
                using (var p = Process.Start(psi))
                {
                    var tOut = p.StandardOutput.ReadToEndAsync();
                    var tErr = p.StandardError.ReadToEndAsync();
                    if (!p.WaitForExit(cfg.Timeout * 1000))
                    {
                        try { p.Kill(); } catch { }
                        rep.RawError = "审图超时（超过 " + cfg.Timeout + " 秒）";
                        return rep;
                    }
                    stdout = tOut.Result;
                    stderr = tErr.Result;
                    if (p.ExitCode != 0)
                    {
                        rep.RawError = "原生审图进程异常退出（退出码 " + p.ExitCode + "）。\n" + Tail(stderr, 800);
                        return rep;
                    }
                }
            }
            catch (Exception ex)
            {
                rep.RawError = "调用原生审图失败：" + ex.Message;
                return rep;
            }
            rep.ElapsedSec = sw.Elapsed.TotalSeconds;

            string stem = Path.GetFileName(swJsonPath);
            if (stem.EndsWith(".sw.json", StringComparison.OrdinalIgnoreCase))
                stem = stem.Substring(0, stem.Length - ".sw.json".Length);
            else
                stem = Path.GetFileNameWithoutExtension(stem);

            string json = Path.Combine(outDir, stem + ".qc_report.json");
            rep.JsonPath = json;

            if (!File.Exists(json))
            {
                // 有些环境里 python 写不了报告文件（被权限/安全软件挡住），
                // 脚本会改用 stdout 回传 —— 这里从标记里截取。
                string text = ExtractReport(stdout);
                if (string.IsNullOrEmpty(text))
                {
                    rep.RawError = "没有生成报告。\n" + Tail(stderr, 800) + "\n" + Tail(stdout, 800);
                    return rep;
                }
                try
                {
                    ParseJsonText(text, rep);
                    return rep;
                }
                catch (Exception ex)
                {
                    rep.RawError = "报告解析失败：" + ex.Message;
                    return rep;
                }
            }

            string png = Path.Combine(outDir, stem + ".preview.png");
            if (File.Exists(png)) rep.PreviewPath = png;

            try { ParseJson(json, rep); }
            catch (Exception ex) { rep.RawError = "报告解析失败：" + ex.Message; }
            return rep;
        }
        /// <summary>把配置写回 qc_addin_config.ini（AI 设置窗口用）。</summary>
        public static void SaveConfig(Config c)
        {
            string ini = Path.Combine(
                Path.GetDirectoryName(typeof(QcRunner).Assembly.Location),
                "qc_addin_config.ini");
            var lines = new List<string>();
            lines.Add("# 工程图 QC 插件配置");
            lines.Add("python=" + (c.Python ?? ""));
            lines.Add("parser=" + (c.Parser ?? ""));
            lines.Add("native=" + (c.Native ?? ""));
            lines.Add("rules=" + (string.IsNullOrEmpty(c.Rules) ? "gb_baseline" : c.Rules));
            lines.Add("timeout=" + (c.Timeout > 0 ? c.Timeout : 180));
            lines.Add("# AI 语义检查（可选）");
            lines.Add("ai_provider=" + (c.AiProvider ?? ""));
            lines.Add("ai_key=" + (c.AiKey ?? ""));
            // 各厂商分别保存（切换厂商不会互相覆盖）
            if (c.AiKeys != null)
                foreach (KeyValuePair<string, string> kv in c.AiKeys)
                    if (!string.IsNullOrEmpty(kv.Value))
                        lines.Add("ai_key_" + kv.Key + "=" + kv.Value);
            lines.Add("ai_base=" + (c.AiBase ?? ""));
            lines.Add("ai_model=" + (c.AiModel ?? ""));
            lines.Add("ai_proxy=" + (c.AiProxy ?? ""));
            File.WriteAllText(ini, string.Join("\r\n", lines.ToArray()),
                new UTF8Encoding(false));
        }

        /// <summary>测试 AI 接口是否可用（返回 "OK 返回 N 条" 或失败原因）。</summary>
        /// <summary>
        /// 探测 AI 服务地址是否可达（DNS + TCP）。返回 "" 表示可达。
        /// 2026-10-25 实测：国内直连 api.openai.com 会 DNS 污染 + 443 超时，
        /// 必须把原因直接告诉用户（该填代理还是该换中转地址）。
        /// </summary>
        static string ProbeAiHost(string baseUrl)
        {
            try
            {
                var u = new Uri(baseUrl);
                string host = u.Host;
                int port = u.Port > 0 ? u.Port : (u.Scheme == "https" ? 443 : 80);
                string ipTxt = "";
                try
                {
                    var ips = System.Net.Dns.GetHostAddresses(host);
                    if (ips != null && ips.Length > 0) ipTxt = ips[0].ToString();
                }
                catch { }
                var tcp = new System.Net.Sockets.TcpClient();
                try
                {
                    var ar = tcp.BeginConnect(host, port, null, null);
                    if (!ar.AsyncWaitHandle.WaitOne(5000))
                    {
                        return "无法连接 " + host + ":" + port + "（5 秒超时）"
                             + (ipTxt.Length > 0 ? "，DNS 解析为 " + ipTxt : "")
                             + "。\n本机网络访问不了这个地址：\n"
                             + "· 用 ChatGPT/OpenAI：国内需在「代理」填本机代理"
                             + "（如 http://127.0.0.1:7890），或把 API 地址改成国内可直连的"
                             + " OpenAI 兼容中转。\n"
                             + "· 用 DeepSeek/通义等国内服务：检查地址是否写错。";
                    }
                    tcp.EndConnect(ar);
                    return "";
                }
                finally { try { tcp.Close(); } catch { } }
            }
            catch (Exception ex) { return "地址无效：" + ex.Message; }
        }

                /// <summary>写一行到 %TEMP%\SwQcAddin\addin.log</summary>
        public static void LogLine(string msg)
        {
            try
            {
                string dir = Path.Combine(Path.GetTempPath(), "SwQcAddin");
                if (!Directory.Exists(dir)) Directory.CreateDirectory(dir);
                File.AppendAllText(Path.Combine(dir, "addin.log"),
                    DateTime.Now.ToString("yyyy-MM-dd HH:mm:ss ") + msg + "\r\n",
                    new UTF8Encoding(false));
            }
            catch { }
        }

        /// <summary>
        /// 把「图像 AI 审核」的结果合并进原生审图报告（2026-10-25）。
        /// 只追加问题/通过项/警告，不覆盖原生尺寸公差数据。
        /// </summary>
        public static void MergeInto(QcReport main, QcReport extra)
        {
            if (main == null || extra == null) return;
            int added = 0;
            if (extra.Items != null)
            {
                foreach (QcItem it in extra.Items)
                {
                    if (it == null) continue;
                    it.Standard = string.IsNullOrEmpty(it.Standard) ? "AI 图像审核" : it.Standard;
                    main.Items.Add(it);
                    added++;
                    if (it.Severity == "critical") main.Critical++;
                    else if (it.Severity == "major") main.Major++;
                    else main.Minor++;
                }
            }
            if (extra.Passed != null)
                foreach (QcPassed p in extra.Passed)
                {
                    if (p == null) continue;
                    main.Passed.Add(new QcPassed(
                        string.IsNullOrEmpty(p.Category) ? "技术要求" : p.Category,
                        p.Text));
                }
            if (extra.Warnings != null)
                foreach (string w in extra.Warnings)
                    if (!string.IsNullOrEmpty(w)) main.Warnings.Add("图像AI：" + w);
            if (!string.IsNullOrEmpty(extra.AiNote))
            {
                main.AiNote = extra.AiNote;
                // 让 AI 结论在结果窗口里看得见（技术要求组的一条通过项）
                main.Passed.Add(new QcPassed("技术要求",
                    "AI 图像审核：" + extra.AiNote));
            }
            LogLine("[合并] 图像AI 追加 " + added + " 条（合计 " + main.Items.Count + " 条）");
        }

        /// <summary>
        /// PDF 审核：把工程图 PDF 交给 pdf_vision_audit.py
        /// （矢量文字精确抽取 + 400dpi 图像 + AI 语义判断 + 双轮投票）。
        /// 2026-10-25 用户定稿方向：不读几何 API，走「导 PDF → AI 审图」。
        /// </summary>
        public static QcReport RunPdfAudit(string pdfPath, string outDir)
        {
            var cfg = LoadConfig();
            var rep = new QcReport();
            rep.ReportDir = outDir;
            rep.Preset = "pdf_vision";
            try { rep.FileName = Path.GetFileName(pdfPath); } catch { }

            if (string.IsNullOrEmpty(pdfPath) || !File.Exists(pdfPath))
            {
                rep.RawError = "PDF 不存在：" + pdfPath;
                return rep;
            }
            string script = null;
            if (!string.IsNullOrEmpty(cfg.Native))
                script = Path.Combine(Path.GetDirectoryName(cfg.Native), "pdf_vision_audit.py");
            if (string.IsNullOrEmpty(script) || !File.Exists(script))
            {
                rep.RawError = "找不到 pdf_vision_audit.py（应与 sw_native_qc.py 同目录）";
                return rep;
            }
            if (string.IsNullOrEmpty(cfg.Python) || !File.Exists(cfg.Python))
            {
                rep.RawError = "找不到 python：" + (cfg.Python ?? "");
                return rep;
            }
            try { if (!Directory.Exists(outDir)) Directory.CreateDirectory(outDir); } catch { }

            var psi = new ProcessStartInfo();
            psi.FileName = cfg.Python;
            psi.Arguments = Quote(script) + " " + Quote(pdfPath)
                          + " --out " + Quote(outDir) + " --vote 2";
            psi.UseShellExecute = false;
            psi.CreateNoWindow = true;
            psi.RedirectStandardOutput = true;
            psi.RedirectStandardError = true;
            psi.StandardOutputEncoding = Encoding.UTF8;
            psi.StandardErrorEncoding = Encoding.UTF8;
            psi.EnvironmentVariables["PYTHONIOENCODING"] = "utf-8";
            if (!string.IsNullOrEmpty(cfg.AiKey)) psi.EnvironmentVariables["QC_AI_KEY"] = cfg.AiKey;
            if (!string.IsNullOrEmpty(cfg.AiBase)) psi.EnvironmentVariables["QC_AI_BASE"] = cfg.AiBase;
            if (!string.IsNullOrEmpty(cfg.AiProvider)) psi.EnvironmentVariables["QC_AI_PROVIDER"] = cfg.AiProvider;
            if (!string.IsNullOrEmpty(cfg.AiModel)) psi.EnvironmentVariables["QC_AI_MODEL"] = cfg.AiModel;
            if (!string.IsNullOrEmpty(cfg.AiProxy))
            {
                psi.EnvironmentVariables["HTTP_PROXY"] = cfg.AiProxy;
                psi.EnvironmentVariables["HTTPS_PROXY"] = cfg.AiProxy;
            }

            int timeoutSec = cfg.Timeout > 0 ? cfg.Timeout : 180;
            string outText = "", errText = "";
            try
            {
                using (var p = Process.Start(psi))
                {
                    var tOut = p.StandardOutput.ReadToEndAsync();
                    var tErr = p.StandardError.ReadToEndAsync();
                    if (!p.WaitForExit(timeoutSec * 1000))
                    {
                        try { p.Kill(); } catch { }
                        rep.RawError = "PDF 审核超时（超过 " + timeoutSec + " 秒）";
                        return rep;
                    }
                    outText = tOut.Result ?? "";
                    errText = tErr.Result ?? "";
                    if (p.ExitCode != 0)
                    {
                        rep.RawError = "PDF 审核进程异常退出（退出码 " + p.ExitCode + "）。\n" + Tail(errText, 800);
                        return rep;
                    }
                }
            }
            catch (Exception ex)
            {
                rep.RawError = "启动 PDF 审核失败：" + ex.Message;
                return rep;
            }
            try { LogLine("[PDF审核] " + Path.GetFileName(pdfPath) + " => " + Tail(outText + " " + errText, 300)); }
            catch { }

            string reportPath = Path.Combine(outDir,
                Path.GetFileNameWithoutExtension(pdfPath) + ".pdf_report.json");
            if (!File.Exists(reportPath))
            {
                rep.RawError = "未生成报告。" + Tail(outText + " " + errText, 400);
                return rep;
            }
            string json = "";
            try { json = File.ReadAllText(reportPath, Encoding.UTF8); }
            catch (Exception ex) { rep.RawError = "读报告失败：" + ex.Message; return rep; }
            try { ParseJsonText(json, rep); }
            catch (Exception ex) { rep.RawError = "报告解析失败：" + ex.Message; }
            return rep;
        }

public static string TestAi()
        {
            var cfg = LoadConfig();
            // 2026-10-25：先真跑再诊断（raw TCP 在"只有系统代理"机器上会误报）
            string probeErr = string.IsNullOrEmpty(cfg.AiBase) ? "" : ProbeAiHost(cfg.AiBase);

            string nativePy = cfg.Native;
            if (string.IsNullOrEmpty(nativePy) && !string.IsNullOrEmpty(cfg.Parser))
                nativePy = Path.Combine(Path.GetDirectoryName(cfg.Parser), "sw_native_qc.py");
            if (string.IsNullOrEmpty(nativePy) || !File.Exists(nativePy))
                return "找不到 sw_native_qc.py";
            if (string.IsNullOrEmpty(cfg.Python) || !File.Exists(cfg.Python))
                return "找不到 python";

            var psi = new ProcessStartInfo();
            psi.FileName = cfg.Python;
            psi.Arguments = Quote(nativePy) + " " + Quote(nativePy) + " --ai-test";
            psi.UseShellExecute = false;
            psi.CreateNoWindow = true;
            psi.RedirectStandardOutput = true;
            psi.RedirectStandardError = true;
            psi.StandardOutputEncoding = Encoding.UTF8;
            psi.StandardErrorEncoding = Encoding.UTF8;
            psi.EnvironmentVariables["PYTHONIOENCODING"] = "utf-8";   // 防中文乱码
            if (!string.IsNullOrEmpty(cfg.AiKey)) psi.EnvironmentVariables["QC_AI_KEY"] = cfg.AiKey;
            if (!string.IsNullOrEmpty(cfg.AiBase)) psi.EnvironmentVariables["QC_AI_BASE"] = cfg.AiBase;
            // 2026-10-24 修：必须把 provider/model 一起传，否则 Python 用默认 gpt-4o-mini
            // 去请求 DeepSeek 会 HTTP 400，表现就是"AI 没有介入"。
            if (!string.IsNullOrEmpty(cfg.AiProvider)) psi.EnvironmentVariables["QC_AI_PROVIDER"] = cfg.AiProvider;
            if (!string.IsNullOrEmpty(cfg.AiModel)) psi.EnvironmentVariables["QC_AI_MODEL"] = cfg.AiModel;
 if (!string.IsNullOrEmpty(cfg.Standard)) psi.EnvironmentVariables["QC_STANDARD"] = cfg.Standard;
            if (!string.IsNullOrEmpty(cfg.AiModel)) psi.EnvironmentVariables["QC_AI_MODEL"] = cfg.AiModel;
 if (!string.IsNullOrEmpty(cfg.Standard)) psi.EnvironmentVariables["QC_STANDARD"] = cfg.Standard;
            if (!string.IsNullOrEmpty(cfg.AiProvider)) psi.EnvironmentVariables["QC_AI_PROVIDER"] = cfg.AiProvider;
            if (!string.IsNullOrEmpty(cfg.AiProxy))
            {
                psi.EnvironmentVariables["HTTP_PROXY"] = cfg.AiProxy;
                psi.EnvironmentVariables["HTTPS_PROXY"] = cfg.AiProxy;
            }
            try
            {
                using (var p = Process.Start(psi))
                {
                    var tOut = p.StandardOutput.ReadToEndAsync();
                    var tErr = p.StandardError.ReadToEndAsync();
                    if (!p.WaitForExit(120000))
                    {
                        try { p.Kill(); p.WaitForExit(2000); } catch { }
                        return "测试超时";
                    }
                    string outText = tOut.Result;
                    string errText = tErr.Result;

                    string result;
                    if (outText.Contains("AI_TEST_OK")) result = outText.Trim();
                    else if (outText.Contains("AI_TEST_FAIL")) result = outText.Trim();
                    else result = "未知结果：" + Tail(outText + " " + errText, 300);

                    // 2026-10-25：失败时补配置上下文 + 网络诊断，并写日志便于下次定位
                    string cfgLine = "厂商=" + (cfg.AiProvider ?? "") + " 地址=" + (cfg.AiBase ?? "")
                                   + " 模型=" + (cfg.AiModel ?? "")
                                   + " 代理=" + (string.IsNullOrEmpty(cfg.AiProxy) ? "（未填）" : cfg.AiProxy);
                    if (result.IndexOf("AI_TEST_FAIL") >= 0)
                    {
                        if (result.IndexOf("网络不可达") >= 0 && probeErr.Length > 0)
                            result += "\n\n网络诊断：" + probeErr;
                        result += "\n\n当前配置：" + cfgLine;
                    }
                    try { LogLine("[AI测试] " + cfgLine + " => " + result.Replace("\n", " ")); }
                    catch { }
                    return result;
                }
            }
            catch (Exception ex) { return "AI 测试异常：" + ex.Message; }
        }

        public static List<string> ListModels(out string err)
        {
            err = "";
            var cfg = LoadConfig();
            string baseUrl = cfg.AiBase;
            string provider = (cfg.AiProvider ?? "").ToLower();
            string[][] defaults = new string[][] {
                new[] { "deepseek", "https://api.deepseek.com/v1" },
                new[] { "chatgpt", "https://api.openai.com/v1" },
                new[] { "openai", "https://api.openai.com/v1" },
                new[] { "qwen", "https://dashscope.aliyuncs.com/compatible-mode/v1" },
                new[] { "zhipu", "https://open.bigmodel.cn/api/paas/v4" },
                new[] { "moonshot", "https://api.moonshot.cn/v1" },
            };
            if (string.IsNullOrEmpty(baseUrl))
            {
                foreach (string[] kv in defaults)
                    if (kv[0] == provider) { baseUrl = kv[1]; break; }
            }
            if (string.IsNullOrEmpty(baseUrl)) baseUrl = "https://api.openai.com/v1";
            if (string.IsNullOrEmpty(cfg.AiKey)) { err = "未填写 API Key"; return null; }

            var list = new List<string>();
            try
            {
                var req = (System.Net.HttpWebRequest)System.Net.WebRequest.Create(
                    baseUrl.TrimEnd('/') + "/models");
                req.Method = "GET";
                req.Timeout = 20000;
                req.Headers["Authorization"] = "Bearer " + cfg.AiKey;
                using (var resp = (System.Net.HttpWebResponse)req.GetResponse())
                using (var sr = new System.IO.StreamReader(resp.GetResponseStream()))
                {
                    string body = sr.ReadToEnd();
                    var ser = new System.Web.Script.Serialization.JavaScriptSerializer();
                    var root = ser.Deserialize<Dictionary<string, object>>(body);
                    object dataObj;
                    if (root != null && root.TryGetValue("data", out dataObj) &&
                        dataObj is System.Collections.IList)
                    {
                        foreach (object o in (System.Collections.IList)dataObj)
                        {
                            var d = o as Dictionary<string, object>;
                            object id;
                            if (d != null && d.TryGetValue("id", out id) && id != null)
                                list.Add(Convert.ToString(id));
                        }
                    }
                }
                if (list.Count == 0) err = "接口没有返回模型列表";
            }
            catch (Exception ex)
            {
                err = "拉取失败：" + ex.Message;
                return null;
            }
            return list;
        }

        public static void ParseJson(string path, QcReport rep)
        {
            ParseJsonText(File.ReadAllText(path, Encoding.UTF8), rep);
        }

        /// <summary>解析 JSON 文本（也用于从 stdout 回传的报告）。</summary>
        public static void ParseJsonText(string text, QcReport rep)
        {
            var ser = new JavaScriptSerializer();
            ser.MaxJsonLength = 64 * 1024 * 1024;
            var root = ser.Deserialize<Dictionary<string, object>>(text);

            object infoObj;
            if (root.TryGetValue("info", out infoObj))
            {
                var info = infoObj as Dictionary<string, object>;
                if (info != null) rep.FileName = Str(info, "file");
            }
            if (string.IsNullOrEmpty(rep.FileName)) rep.FileName = "（未命名图纸）";

            rep.PartType = Pick(root, "part_type", "type", "name", "label");
            object scObj;
            if (root.TryGetValue("drawing_scale", out scObj) && scObj is Dictionary<string, object>)
                rep.DrawingScale = Str((Dictionary<string, object>)scObj, "value");

            object passedObj;
            if (root.TryGetValue("passed", out passedObj) && passedObj is System.Collections.IList)
            {
                foreach (object o in (System.Collections.IList)passedObj)
                {
                    if (o == null) continue;
                    var pd = o as Dictionary<string, object>;
                    if (pd != null)
                        rep.Passed.Add(new QcPassed(Str(pd, "category"), Str(pd, "text")));
                    else
                    {
                        string txt = Convert.ToString(o);
                        rep.Passed.Add(new QcPassed(ClassifyPassedText(txt), txt));
                    }
                }
            }

            // 2026-10-25：未关联几何公差（让用户确认归属）
            try
            {
                object ug;
                if (root.TryGetValue("unlinked_gtols", out ug) && ug is System.Collections.IList)
                {
                    foreach (object o in (System.Collections.IList)ug)
                    {
                        var gd = o as Dictionary<string, object>;
                        if (gd == null) continue;
                        var u = new QcUnlinkedGtol();
                        u.GtolId = Str(gd, "gtol_id");
                        u.RawId = Str(gd, "raw_id");
                        u.GroupId = Str(gd, "group_id");
                        int.TryParse(Str(gd, "frame_order"), out u.FrameOrder);
                        int.TryParse(Str(gd, "frame_count"), out u.FrameCount);
                        if (u.FrameCount <= 0) u.FrameCount = 1;
                        u.FrameType = Str(gd, "frame_type");
                        u.FrameReason = Str(gd, "frame_reason");
                        u.ZoneType = Str(gd, "zone_type");
                        u.MeasuredFeature = Str(gd, "measured_feature");
                        u.Sym = Str(gd, "sym");
                        u.Val = Str(gd, "val");
                        u.Dat1 = Str(gd, "dat1");
                        u.Dat2 = Str(gd, "dat2");
                        u.Dat3 = Str(gd, "dat3");
                        u.Mod = Str(gd, "mod");
                        u.Label = Str(gd, "label");
                        u.View = Str(gd, "view");
                        rep.UnlinkedGtols.Add(u);
                    }
                }
            }
            catch { }

            // 2026-10-27 修：header 在 datums 之后解析，取图号必须直接从 root.header 读
            string _dwg0FromRoot = "";
            try
            {
                object _ho;
                if (root.TryGetValue("header", out _ho) && _ho is Dictionary<string, object>)
                {
                    object _dv;
                    if (((Dictionary<string, object>)_ho).TryGetValue("图号", out _dv))
                        _dwg0FromRoot = Convert.ToString(_dv ?? "").Trim();
                }
            }
            catch { }

            // 基准要素（含人工指定）
            try
            {
                object dts;
                if (root.TryGetValue("datums", out dts) && dts is System.Collections.IList)
                {
                    foreach (object o in (System.Collections.IList)dts)
                    {
                        var dd = o as Dictionary<string, object>;
                        if (dd == null) continue;
                        var dm = new QcDatum();
                        dm.Letter = Str(dd, "基准");
                        dm.View = Str(dd, "所在视图");
                        string tgt = Str(dd, "指向要素");
                        if (string.IsNullOrEmpty(tgt)) tgt = Str(dd, "指向要素（推断）");
                        dm.Target = tgt;
                        dm.Near = Str(dd, "附近尺寸（推断基准面）");
                        string _cx = Str(dd, "指向坐标");
                        dm.Coord = _cx;
                        rep.Datums.Add(dm);
                    }
                }
            }
            catch { }

            object dimAnal;
            if (root.TryGetValue("dim_analysis", out dimAnal) && dimAnal is System.Collections.IList)
            {
                foreach (object o in (System.Collections.IList)dimAnal)
                {
                    var d = o as Dictionary<string, object>;
                    if (d == null) continue;
                    rep.ToleranceRows.Add(new string[] {
                        Str(d, "kind"), Str(d, "content"), Str(d, "tol"),
                        Str(d, "it"), Str(d, "capability"), Str(d, "note") });
                    var dr = new QcDimRow();
                    dr.RowId = Str(d, "row_id");
                    dr.Kind = Str(d, "kind");
                    dr.Content = Str(d, "content");
                    dr.IT = Str(d, "it");
                    dr.Capability = Str(d, "capability");
                    dr.Note = Str(d, "note");
                    dr.Modifier = Str(d, "modifier");
                    dr.IsManual = Str(d, "is_manual") == "1";
                    // 2026-10-25：多几何公差 + 自动计入 + 最大作用边界
                    try
                    {
                        object gl;
                        if (d.TryGetValue("gtols", out gl) && gl is System.Collections.IList)
                        {
                            foreach (object go in (System.Collections.IList)gl)
                            {
                                var gd = go as Dictionary<string, object>;
                                if (gd == null) continue;
                                var line = new QcGtolLine();
                                line.GtolId = Str(gd, "gtol_id");
                                line.Sym = Str(gd, "sym");
                                line.Val = Str(gd, "val");
                                line.Dat1 = Str(gd, "dat1");
                                line.Dat2 = Str(gd, "dat2");
                                line.Dat3 = Str(gd, "dat3");
                                line.Mod = Str(gd, "mod");
                                line.Reason = Str(gd, "reason");
                                line.RawId = Str(gd, "raw_id");
                                line.GroupId = Str(gd, "group_id");
                                try { int.TryParse(Str(gd, "frame_order"), out line.FrameOrder); } catch { }
                                try { int.TryParse(Str(gd, "frame_count"), out line.FrameCount); } catch { }
                                if (line.FrameCount <= 0) line.FrameCount = 1;
                                line.FrameType = Str(gd, "frame_type");
                                line.FrameReason = Str(gd, "frame_reason");
                                line.ZoneType = Str(gd, "zone_type");
                                line.MeasuredFeature = Str(gd, "measured_feature");
                                object ib;
                                line.Include = gd.TryGetValue("include", out ib) && ib is bool && (bool)ib;
                                object vv;
                                if (gd.TryGetValue("value_mm", out vv) && vv != null)
                                    double.TryParse(Convert.ToString(vv),
                                        System.Globalization.NumberStyles.Float,
                                        System.Globalization.CultureInfo.InvariantCulture, out line.ValueMm);
                                dr.Gtols.Add(line);
                            }
                        }
                        dr.SigmaT = StrToD(d, "sigma_t");
                        dr.MmsShaft = StrToD(d, "mms_shaft");
                        dr.MmsHole = StrToD(d, "mms_hole");
                        dr.MmvcShaft = StrToD(d, "mmvc_shaft");
                        dr.MmvcHole = StrToD(d, "mmvc_hole");
                    }
                    catch { }
                    try
                    {
                        object gObj;
                        if (d.TryGetValue("gtol", out gObj) && gObj is Dictionary<string, object>)
                        {
                            var g = (Dictionary<string, object>)gObj;
                            dr.GtolSym = Str(g, "sym");
                            dr.GtolVal = Str(g, "val");
                            dr.GtolMod = Str(g, "mod");
                            dr.GtolDat1 = Str(g, "dat1");
                            dr.GtolDat2 = Str(g, "dat2");
                            dr.GtolDat3 = Str(g, "dat3");
                        }
                    }
                    catch { }
                    ParseDimRow(dr, Str(d, "tol"), d);
                    rep.DimRows.Add(dr);
                }
            }
            try { DedupeDimRows(rep, string.IsNullOrEmpty(_dwg0FromRoot) ? rep.DwgNo : _dwg0FromRoot); NormalizeReport(rep); } catch { }

            object gtolAnal;
            if (root.TryGetValue("gtol_analysis", out gtolAnal) && gtolAnal is System.Collections.IList)
            {
                foreach (object o in (System.Collections.IList)gtolAnal)
                {
                    var d = o as Dictionary<string, object>;
                    if (d == null) continue;
                    rep.ToleranceRows.Add(new string[] {
                        Str(d, "kind"), Str(d, "content"), Str(d, "tol"),
                        Str(d, "it"), Str(d, "capability"), Str(d, "note") });
                }
            }

            object headerObj;
            if (root.TryGetValue("header", out headerObj) && headerObj is Dictionary<string, object>)
            {
                foreach (KeyValuePair<string, object> kv in (Dictionary<string, object>)headerObj)
                {
                    if (kv.Value != null) rep.Header[kv.Key] = Convert.ToString(kv.Value);
                }
            }
            object statsObj;
            if (root.TryGetValue("stats", out statsObj) && statsObj is Dictionary<string, object>)
            {
                var st = (Dictionary<string, object>)statsObj;
                object v;
                if (st.TryGetValue("dimension_count", out v) && v != null)
                    int.TryParse(Convert.ToString(v), out rep.DimCount);
                if (st.TryGetValue("untoleranced_count", out v) && v != null)
                    int.TryParse(Convert.ToString(v), out rep.UntolerancedCount);
            }
            object ho;
            if (root.TryGetValue("header_only", out ho) && ho != null)
                bool.TryParse(Convert.ToString(ho), out rep.HeaderOnly);

            object nb;
            if (root.TryGetValue("notes_has_1804", out nb) && nb != null)
                bool.TryParse(Convert.ToString(nb), out rep.NotesHas1804);

            object warnObj;
            if (root.TryGetValue("warnings", out warnObj) && warnObj is System.Collections.IList)
            {
                foreach (object w in (System.Collections.IList)warnObj)
                {
                    if (w != null) rep.Warnings.Add(Convert.ToString(w));
                }
            }

            object aiObj;
            if (root.TryGetValue("ai", out aiObj) && aiObj is Dictionary<string, object>)
            {
                var ai = (Dictionary<string, object>)aiObj;
                object v;
                bool configured = ai.TryGetValue("configured", out v) && v != null &&
                                  Convert.ToString(v).ToLower() == "true";
                bool skipped = ai.TryGetValue("skipped", out v) && v != null &&
                               Convert.ToString(v).ToLower() == "true";
                int cnt = 0;
                if (ai.TryGetValue("count", out v) && v != null)
                    int.TryParse(Convert.ToString(v), out cnt);
                rep.AiCount = cnt;
                if (ai.TryGetValue("elapsed_sec", out v) && v != null)
                    double.TryParse(Convert.ToString(v), out rep.AiElapsed);
                string err = "";
                if (ai.TryGetValue("error", out v) && v != null) err = Convert.ToString(v);
                if (skipped && string.IsNullOrEmpty(err))
                    rep.AiStatus = "窗口打开阶段不调用 AI；点「检查」后执行";
                else if (!configured)
                    rep.AiStatus = "未配置（点「AI 设置」填写 Key 后启用）";
                else if (!string.IsNullOrEmpty(err))
                    rep.AiStatus = "调用失败：" + err;
                else
                    if (cnt == 0)
                    rep.AiStatus = (string.IsNullOrEmpty(rep.AiNote)
                        ? "已执行，AI 未发现问题（用时 " + rep.AiElapsed.ToString("0.0") + " 秒）"
                        : "已执行：AI 结论「" + Tail(rep.AiNote, 60) + "」（用时 "
                          + rep.AiElapsed.ToString("0.0") + " 秒）");
                else
                    rep.AiStatus = "已执行，AI 结论 " + cnt + " 条（用时 " + rep.AiElapsed.ToString("0.0") + " 秒）";
            }

            object checksObj;
            if (root.TryGetValue("checks", out checksObj) &&
                checksObj is System.Collections.IList)
            {
                foreach (object o in (System.Collections.IList)checksObj)
                {
                    var d = o as Dictionary<string, object>;
                    if (d == null) continue;
                    var it = new QcItem();
                    it.RuleId = Str(d, "rule_id");
                    it.Severity = Str(d, "severity");
                    it.Title = Str(d, "title");
                    it.Detail = Str(d, "detail");
                    it.Suggestion = Str(d, "suggestion");
                    it.Standard = Str(d, "standard");
                    it.Location = Str(d, "location");
                    it.Evidence = Str(d, "evidence");
                    it.Category = Str(d, "category");
                    if (it.Category.Length == 0) it.Category = ClassifyRule(Str(d, "rule_id"));
                    rep.Items.Add(it);
                    if (it.Severity == "critical") rep.Critical++;
                    else if (it.Severity == "major") rep.Major++;
                    else rep.Minor++;
                }
            }
        }


        // ================================================================
        // 2026-10-24 新增：三大类归档、通过项归类、尺寸行解析
        // ================================================================

        /// <summary>按规则ID前缀归档（报告里有 category 时优先用报告值）</summary>
        public static string ClassifyRule(string ruleId)
        {
            string r = (ruleId ?? "").ToUpperInvariant();
            string[] tech = { "TECH-", "GB-TECH", "GB-TOLTXT", "GB-GDT", "GB47067-GEN",
                              "GB47067-OBSOLETE", "ROUGH-", "WELD-", "MOLD-", "SM-",
                              "AUTO-", "BASE-", "GEN-", "CORE-RA" };
            string[] size = { "DIM-", "OUTLINE-", "TOL-", "GTOL-", "GDNT-", "CHAIN-",
                              "HOLE-", "THD-", "KEY-", "RING-", "FIT-", "GEAR-", "HYD-",
                              "PLASTIC-", "ENVELOPE-", "SIZE-" };
            foreach (string p in tech) if (r.StartsWith(p)) return "技术要求";
            foreach (string p in size) if (r.StartsWith(p)) return "尺寸";
            return "尺寸";   // 用户要求：拿不准的一律归「尺寸」
        }

        /// <summary>按文字给通过项归类（passed 为纯字符串时兜底）</summary>
        public static string ClassifyPassedText(string text)
        {
            string t = text ?? "";
            if (t.Contains("几何公差")) return "几何公差";
            if (t.Contains("技术要求") || t.Contains("钣金") || t.Contains("TG") ||
                t.Contains("表面处理") || t.Contains("AI 审查")) return "技术要求";
            if (t.Contains("标题栏") || t.Contains("图框") || t.Contains("材料") ||
                t.Contains("图号") || t.Contains("版本") || t.Contains("比例") ||
                t.Contains("多图纸")) return "图纸信息";
            // 尺寸/公差/几何公差/粗糙度 都归「尺寸」
            return "尺寸";
        }

        /// <summary>把「内容/公差」解析成数值：基本尺寸、上/下偏差、公差带宽</summary>
        static void ParseDimRow(QcDimRow dr, string tolText, Dictionary<string, object> raw)
        {
            double nom = GetD(raw, "nominal");
            if (Math.Abs(nom) < 1e-12)
            {
                var sb = new System.Text.StringBuilder();
                foreach (char ch in (dr.Content ?? ""))
                {
                    if (char.IsDigit(ch) || ch == '.') sb.Append(ch);
                    else if (sb.Length > 0) break;
                }
                double.TryParse(sb.ToString(), System.Globalization.NumberStyles.Float,
                                System.Globalization.CultureInfo.InvariantCulture, out nom);
            }
            dr.Nominal = nom;

            double up = GetD(raw, "tol_upper");
            double low = GetD(raw, "tol_lower");
            bool has = Math.Abs(up) > 1e-12 || Math.Abs(low) > 1e-12;

            if (!has && !string.IsNullOrEmpty(tolText) &&
                !tolText.Contains("无") && !tolText.Contains("未注") &&
                !tolText.Contains("基本尺寸") && tolText.Trim() != "—")
            {
                string tt = tolText.Trim();
                if (tt.StartsWith("±"))
                {
                    double v;
                    if (double.TryParse(tt.Replace("±", "").Trim(),
                            System.Globalization.NumberStyles.Float,
                            System.Globalization.CultureInfo.InvariantCulture, out v))
                    { up = v; low = -v; has = true; }
                }
                else
                {
                    var nums = new List<double>();
                    string cur = "";
                    foreach (char ch in tt)
                    {
                        if (ch == '+' || ch == '-')
                        {
                            if (cur.Length > 0) { double v0; if (double.TryParse(cur, System.Globalization.NumberStyles.Float, System.Globalization.CultureInfo.InvariantCulture, out v0)) nums.Add(v0); }
                            cur = ch.ToString();
                        }
                        else if (char.IsDigit(ch) || ch == '.') { cur += ch; }
                        else
                        {
                            if (cur.Length > 0) { double v1; if (double.TryParse(cur, System.Globalization.NumberStyles.Float, System.Globalization.CultureInfo.InvariantCulture, out v1)) nums.Add(v1); cur = ""; }
                        }
                    }
                    if (cur.Length > 0) { double v2; if (double.TryParse(cur, System.Globalization.NumberStyles.Float, System.Globalization.CultureInfo.InvariantCulture, out v2)) nums.Add(v2); }
                    if (nums.Count >= 2) { up = Math.Max(nums[0], nums[1]); low = Math.Min(nums[0], nums[1]); has = true; }
                    else if (nums.Count == 1) { up = nums[0]; low = 0; has = true; }
                }
            }

            if (!has && !string.IsNullOrEmpty(dr.IT) && dr.IT != "未注") has = true;

            dr.TolUp = up;
            dr.TolLow = low;
            dr.HasTol = has;
            dr.Band = up - low;
            dr.TolDisplay = has ? ("上 " + CalcPanel.N(up) + " 下 " + CalcPanel.N(low)) : "无";
            dr.IsOutline = (dr.Kind ?? "").StartsWith("轮廓") || (dr.Kind ?? "").StartsWith("板厚");
        }

        static double GetD(Dictionary<string, object> d, string key)
        {
            object v;
            if (d != null && d.TryGetValue(key, out v) && v != null)
            {
                double r;
                if (double.TryParse(Convert.ToString(v), System.Globalization.NumberStyles.Float,
                        System.Globalization.CultureInfo.InvariantCulture, out r))
                    return r;
            }
            return 0;
        }

        static double StrToD(Dictionary<string, object> d, string key)
        {
            try
            {
                object o;
                if (!d.TryGetValue(key, out o) || o == null) return 0.0;
                double v;
                if (double.TryParse(Convert.ToString(o),
                        System.Globalization.NumberStyles.Float,
                        System.Globalization.CultureInfo.InvariantCulture, out v))
                    return v;
            }
            catch { }
            return 0.0;
        }

        static string Str(Dictionary<string, object> d, string key)
        {
            object v;
            if (d != null && d.TryGetValue(key, out v) && v != null) return Convert.ToString(v);
            return "";
        }

        /// <summary>取值；若值是对象（dict），按候选子键继续取。</summary>
        static string Pick(Dictionary<string, object> d, string key,
                           string sub1, string sub2, string sub3)
        {
            object v;
            if (d == null || !d.TryGetValue(key, out v) || v == null) return "";
            var sub = v as Dictionary<string, object>;
            if (sub == null) return Convert.ToString(v);
            foreach (string k in new[] { sub1, sub2, sub3 })
            {
                object x;
                if (sub.TryGetValue(k, out x) && x != null) return Convert.ToString(x);
            }
            return "";
        }
        const string RepBegin = "<<<QC_REPORT_BEGIN>>>";
        const string RepEnd = "<<<QC_REPORT_END>>>";

        /// <summary>从进程 stdout 里截取 JSON 报告（标记之间）。</summary>
        static string ExtractReport(string stdout)
        {
            if (string.IsNullOrEmpty(stdout)) return null;
            int i = stdout.IndexOf(RepBegin, StringComparison.Ordinal);
            if (i < 0) return null;
            i += RepBegin.Length;
            int j = stdout.IndexOf(RepEnd, i, StringComparison.Ordinal);
            if (j < 0) return null;
            return stdout.Substring(i, j - i).Trim();
        }
        static string Quote(string s) { return "\"" + s + "\""; }

        static string Tail(string s, int n)
        {
            if (string.IsNullOrEmpty(s)) return "";
            return s.Length <= n ? s : s.Substring(s.Length - n);
        }
    }
}
