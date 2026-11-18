using System;
using System.Collections.Generic;
using System.IO;
using System.Runtime.InteropServices;
using System.Text;
using System.Windows.Forms;
using SolidWorks.Interop.sldworks;
using SolidWorks.Interop.swconst;
using SolidWorks.Interop.swpublished;

namespace SwQc
{
    /// <summary>
    /// SolidWorks COM 插件入口；使用 2016 API 基线，兼容较新的 API 版本。
    ///
    /// 菜单：工具 → 工程图QC → 审图 / 原生信息（调试）
    ///
    /// 「审图」按当前文档自动判断：
    ///   · 已打开工程图（.SLDDRW）→ 直接审这张；
    ///   · 已打开零件/装配 → 在同目录找同名 .SLDDRW 并打开；
    ///     找不到 → 弹「无对应工程图」；
    ///   · 没有打开任何文档 → 提示先打开。
    ///
    /// 菜单项**始终可用**（v1 的坑：仅在工程图时启用，导致打开零件后整组菜单消失）。
    /// </summary>
    [ComVisible(true)]
    [Guid("9F3C2A5B-7D41-4E8B-9C62-1A2B3C4D5E6F")]
    [ProgId("SwQc.Connect")]
    public class SwQcAddin : ISwAddin
    {
        private ISldWorks _swApp;
        private int _addinId = -1;
        private ICommandManager _cmdMgr;
        private ICommandGroup _cmdGroup;
        private QcResultForm _qcForm;
        // PDF视觉整图审核是独立通道，不能复用/关闭原生审核窗口。
        private PdfAuditForm _pdfAuditForm;

        /// <summary>"DLL 是否被 SW 加载"的最早期证据：类静态构造就写日志。</summary>
        static SwQcAddin()
        {
            // 2026-10-24 性能：DLL 加载时不做磁盘 IO（原来这里写日志）
        }
        private const int GroupId = 38917;   // 避免与「大工程师」等第三方插件撞 ID
        private const int CmdQc = 0;
        private const int CmdNative = 1;
        private const int CmdPdf = 3;       // 2026-10-25：PDF/AI 审图

        // ------------------------------------------------------------ ISwAddin
        public bool ConnectToSW(object ThisSW, int Cookie)
        {
            try
            {
                return ConnectToSWCore(ThisSW, Cookie);
            }
            catch (Exception ex)
            {
                Log("ConnectToSW 顶层异常：" + ex);
                MessageBox.Show("工程图QC 插件初始化失败：\n" + ex.Message +
                                "\n\n日志：%TEMP%\\SwQcAddin\\addin.log",
                    "工程图QC", MessageBoxButtons.OK, MessageBoxIcon.Error);
                return false;
            }
        }

        /// <summary>
        /// 添加命令项：MenuTBOption = 1(菜单) | 2(工具栏)。
        /// 之前传 0 = 既不在菜单也不在工具栏 → 点了完全没反应（实测踩到）。
        /// </summary>
        private int AddItemSafe(string name, string hint, string tip, int cmdId, string cb, int userId)
        {
            int[] options = new int[] { 3, 1, 2 };   // 先前两者都挂，再退菜单、再退工具栏
            foreach (int opt in options)
            {
                try
                {
                    int id = _cmdGroup.AddCommandItem2(name, -1, hint, tip,
                        cmdId, cb, "EnableQc", userId, opt);
                    Log("命令项[" + name + "] 注册成功 MenuTBOption=" + opt + " id=" + id);
                    return id;
                }
                catch (Exception e)
                {
                    Log("命令项[" + name + "] MenuTBOption=" + opt + " 失败: " + e.Message);
                }
            }
            return -1;
        }

        private bool ConnectToSWCore(object ThisSW, int Cookie)
        {
            _swApp = (ISldWorks)ThisSW;
            _addinId = Cookie;
            string revision = "未知";
            try { revision = _swApp.RevisionNumber(); } catch (Exception e) { Log("读取 SW 版本失败: " + e.Message); }
            int apiMajor;
            string majorPart = (revision ?? "").Split('.')[0];
            if (int.TryParse(majorPart, out apiMajor))
            {
                int releaseYear = apiMajor + 1992; // SW2016=24，SW2024=32
                Log("ConnectToSW: SW " + releaseYear + " (" + revision + "), addinId=" + _addinId);
                if (releaseYear < 2016 || releaseYear > 2024)
                    Log("SW 版本超出已核查的 2016–2024 范围，继续尝试兼容加载。");
            }
            else Log("ConnectToSW: SW 版本=" + revision + ", addinId=" + _addinId);
            bool callbackReady = false;
            try { callbackReady = _swApp.SetAddinCallbackInfo2(0, this, _addinId); }
            catch (Exception e) { Log("SetAddinCallbackInfo2 失败: " + e.Message); }
            if (!callbackReady)
            {
                try { callbackReady = _swApp.SetAddinCallbackInfo(0, this, _addinId); }
                catch (Exception e) { Log("SetAddinCallbackInfo 失败: " + e.Message); }
            }
            if (!callbackReady) throw new InvalidOperationException("SolidWorks 未接受插件命令回调注册。");

            try
            {
                _cmdMgr = _swApp.GetCommandManager(_addinId);
                int errs = 0;
                _cmdGroup = _cmdMgr.CreateCommandGroup2(GroupId, "工程图QC",
                    "工程图质量检查（本地规则，直读原生图纸）", "工程图 QC 审图", -1, true, ref errs);

                int idQc = AddItemSafe("审图",
                    "检查当前工程图；若当前是零件则自动找同名工程图", "审图",
                    CmdQc, "OnQc", 0);

                int idNative = AddItemSafe("原生信息（调试）",
                    "直读当前工程图的属性/尺寸/注释，用于验证 API 能拿到什么", "原生信息",
                    CmdNative, "OnNativeInfo", 1);

                // 2026-10-25 用户定稿方向：导 PDF → 最强 AI 审图（不读几何 API）
                int idPdf = AddItemSafe("PDF视觉整图审核",
                    "把当前工程图完整导出为 PDF，或直接选择 PDF，用视觉模型审核整张图纸",
                    "PDF视觉整图审核", CmdPdf, "OnPdfAudit", 3);
                if (idQc < 0 || idNative < 0 || idPdf < 0)
                    throw new InvalidOperationException("CommandManager 未能创建全部菜单命令。");

                // 工具栏也挂出来（用户反馈：只在菜单里找不到插件）
                _cmdGroup.HasMenu = true;
                _cmdGroup.HasToolbar = true;
                try
                {
                    string dir = Path.GetDirectoryName(typeof(SwQcAddin).Assembly.Location);
                    string s1 = Path.Combine(dir, "icons_small.bmp");
                    string s2 = Path.Combine(dir, "icons_large.bmp");
                    if (File.Exists(s1)) _cmdGroup.SmallIconList = s1;
                    if (File.Exists(s2)) _cmdGroup.LargeIconList = s2;
                    Log("图标: " + (File.Exists(s1) ? "小图标OK" : "缺小图标") + " / " + (File.Exists(s2) ? "大图标OK" : "缺大图标"));
                }
                catch (Exception e) { Log("设置图标失败: " + e.Message); }
                _cmdGroup.Activate();
                Log("CommandManager 菜单创建成功");

                // 在 CommandManager 上建一个「工程图QC」标签页（工具栏区域直接可见）
                try
                {
                    int[] cmdIds = new int[] { idQc, idNative, idPdf };
                    // swCommandTabButton_TextBelow = 2：按钮显示文字（之前用 1=NoText，
                    // 图标又没生效，标签页看起来就是一片空白）。
                    int[] textTypes = new int[] { 2, 2, 2 };
                    // 2026-10-24 性能：只给工程图建功能区标签。
                    // 零件/装配也建会让 SW 每次切换文档类型都重建功能区，拖慢交互。
                    // 菜单项（HasMenu）仍在所有文档类型可见，功能不受影响。
                    foreach (int docType in new int[] {
                        (int)swDocumentTypes_e.swDocDRAWING })
                    {
                        ICommandTab tab = _cmdMgr.AddCommandTab(docType, "工程图QC");
                        if (tab == null) continue;
                        ICommandTabBox box = tab.AddCommandTabBox();
                        if (box != null) box.AddCommands(cmdIds, textTypes);
                        Log("CommandTab 已创建 docType=" + docType);
                    }
                }
                catch (Exception e) { Log("CommandTab 创建失败: " + e.Message); }
            }
            catch (Exception ex)
            {
                Log("CommandManager 菜单创建失败：" + ex);
                // 兜底：旧版菜单接口，直接挂到「工具」菜单
                try
                {
                    if (!AddLegacyMenu()) throw new InvalidOperationException("旧版菜单接口未创建任何命令。");
                    Log("旧版菜单兜底成功");
                }
                catch (Exception ex2)
                {
                    Log("旧版菜单也失败：" + ex2);
                    MessageBox.Show("插件菜单创建失败：\n" + ex.Message + "\n" + ex2.Message,
                        "工程图QC", MessageBoxButtons.OK, MessageBoxIcon.Warning);
                    return false;
                }
            }
            return true;
        }

        public bool DisconnectFromSW()
        {
            try
            {
                if (_cmdMgr != null)
                {
                    try { _cmdMgr.RemoveCommandGroup2(GroupId, true); }
                    catch { try { _cmdMgr.RemoveCommandGroup(GroupId); } catch { } }
                    Marshal.ReleaseComObject(_cmdMgr);
                }
            }
            catch { }
            try { if (_swApp != null) Marshal.ReleaseComObject(_swApp); }
            catch { }
            _cmdGroup = null;
            _cmdMgr = null;
            _swApp = null;
            GC.Collect();
            GC.WaitForPendingFinalizers();
            return true;
        }

        // ------------------------------------------------------------ 菜单回调
        /// <summary>始终返回 1 —— 保证菜单在任何文档类型下都存在（v1 的坑）。</summary>
        public int EnableQc()
        {
            return 1;
        }

        public void OnQc()
        {
            Log("OnQc 被点击");
            try
            {
                IModelDoc2 drawing = ResolveDrawing(true);
                if (drawing == null) return;      // 提示已在内部处理

                string outDir = Path.Combine(Path.GetTempPath(), "SwQcAddin", "out");

                // 先用标题占位，保证窗口**立刻**出现（不等待任何 API 读取）
                var seed = new QcReport();
                seed.ReportDir = outDir;
                seed.Preset = QcRunner.LoadConfig().Rules;
                try { seed.FileName = drawing.GetTitle() ?? ""; } catch { }
                if (string.IsNullOrEmpty(seed.FileName)) seed.FileName = "（未命名工程图）";
                seed.HeaderOnly = true;

                if (_qcForm != null && !_qcForm.IsDisposed) { try { _qcForm.Close(); } catch { } }
                _qcForm = new QcResultForm(seed, drawing);
                _qcForm.FormClosed += delegate { _qcForm = null; };

                string tmp = null;
                string summary = "";

                // 窗口显示后再读表头（读零件属性可能较慢，不能挡住窗口）
                _qcForm.Shown += delegate
                {
                    try
                    {
                        Cursor.Current = Cursors.WaitCursor;
                        string json = SwDocReader.ReadNativeJson(_swApp, drawing, out summary);
                        string name = "";
                        try { name = Path.GetFileNameWithoutExtension(drawing.GetPathName()); } catch { }
                        if (string.IsNullOrEmpty(name)) { try { name = drawing.GetTitle(); } catch { } }
                        tmp = SwDocReader.SaveJson(json, name);
                        QcReport head = QcRunner.RunNative(tmp, outDir, true);
                        if (!string.IsNullOrEmpty(head.RawError))
                        {
                            MessageBox.Show("读取表头失败：\n" + head.RawError, "工程图QC");
                            return;
                        }
                        if (_qcForm != null && !_qcForm.IsDisposed) _qcForm.UpdateReport(head);
                        Log("表头已回填: " + summary);

                    }
                    catch (Exception ex)
                    {
                        Log("读表头异常: " + ex);
                        MessageBox.Show("读取图纸表头异常：" + ex.Message, "工程图QC");
                    }
                    finally { Cursor.Current = Cursors.Default; }
                };

                // 2026-10-26：记住当前文档标识，换图/关图后必须重读（原来闭包捕获旧 drawing → 一直读上一张）
                string docKey = "";
                try { docKey = drawing.GetPathName() ?? ""; } catch { }

                _qcForm.CheckRequested += delegate
                {
                    // 2026-10-24 性能：跑 Python（含 AI 7~25 秒）放到后台线程，
                    // 否则 SW 的 UI 线程被占满，表现为"卡死/卡滞"。
                    try
                    {
                        Cursor.Current = Cursors.WaitCursor;

                        // 2026-10-26：每次都重新取当前文档（用户可能换了图 / 关了图）
                        IModelDoc2 cur = null;
                        try { cur = _swApp.ActiveDoc as IModelDoc2; } catch { }
                        int curType = 0;
                        try { if (cur != null) curType = cur.GetType(); } catch { }
                        if (cur == null || curType != (int)swDocumentTypes_e.swDocDRAWING)
                        {
                            // 不用模态框（可能弹在 SW 后面，表现为"关不掉"），直接写在窗口提示里
                            if (_qcForm != null && !_qcForm.IsDisposed)
                                _qcForm.SetIdle("请先打开一张工程图（.SLDDRW），再点「检查」");
                            return;
                        }
                        string curKey = "";
                        try { curKey = cur.GetPathName() ?? ""; } catch { }
                        if (!string.Equals(curKey, docKey, StringComparison.OrdinalIgnoreCase))
                        {
                            // 换图了：作废旧 JSON 与旧表头
                            docKey = curKey;
                            drawing = cur;
                            try { if (_qcForm != null && !_qcForm.IsDisposed) _qcForm.BindDrawing(cur, true); } catch { }
                            tmp = null;
                            seed.FileName = "";
                            try { seed.FileName = cur.GetTitle() ?? ""; } catch { }
                            Log("检测到切换图纸 → 重新读取: " + curKey);
                        }
                        try
                        {
                            if (_qcForm != null && !_qcForm.IsDisposed && _qcForm.ForceRereadNext)
                            {
                                tmp = null;                     // 作废缓存：用户点了「重读图纸」
                                _qcForm.ForceRereadNext = false;
                                Log("重读图纸 → 强制重新提取 JSON");
                            }
                        }
                        catch { }

                        if (string.IsNullOrEmpty(tmp))
                        {
                            // COM 读取必须留在 UI 线程
                            string json = SwDocReader.ReadNativeJson(_swApp, drawing, out summary);
                            tmp = SwDocReader.SaveJson(json, Path.GetFileNameWithoutExtension(seed.FileName));
                        }
                        string mj = tmp;
                        var busyForm = _qcForm;
                        if (busyForm != null && !busyForm.IsDisposed)
                            busyForm.SetBusy("正在检查（含 AI，最长约 30 秒）…，本窗口不会卡住");
                        System.Threading.ThreadPool.QueueUserWorkItem(delegate
                        {
                            QcReport rr = null;
                            string err = null;
                            try { rr = QcRunner.RunNative(mj, outDir); }
                            catch (Exception exx) { err = exx.Message; }
                            try
                            {
                                if (busyForm != null && !busyForm.IsDisposed && busyForm.IsHandleCreated)
                                {
                                    busyForm.BeginInvoke((MethodInvoker)delegate
                                    {
                                        try
                                        {
                                            Cursor.Current = Cursors.Default;
                                            if (err != null)
                                            {
                                                busyForm.SetIdle("检查失败，请重试");
                                                MessageBox.Show(busyForm, "检查失败：" + err, "工程图QC");
                                                return;
                                            }
                                            if (rr == null)
                                            {
                                                busyForm.SetIdle("检查失败，请重试");
                                                MessageBox.Show(busyForm, "检查失败：未返回审核结果", "工程图QC");
                                                return;
                                            }
                                            if (!string.IsNullOrEmpty(rr.RawError))
                                            {
                                                busyForm.SetIdle("检查失败，请重试");
                                                MessageBox.Show(busyForm, rr.RawError, "工程图QC");
                                                return;
                                            }
                                            busyForm.UpdateReport(rr);
                                            Log("检查完成: " + rr.Items.Count + " 条；AI 状态: " +
                                                (string.IsNullOrEmpty(rr.AiStatus) ? "未知" : rr.AiStatus));
                                        }
                                        catch (Exception ex3)
                                        {
                                            try { busyForm.SetIdle("检查回调失败，请重试"); } catch { }
                                            Log("检查回调异常: " + ex3);
                                        }
                                    });
                                }
                            }
                            catch { }
                        });
                    }
                    catch (Exception ex)
                    {
                        Cursor.Current = Cursors.Default;
                        try { if (_qcForm != null && !_qcForm.IsDisposed) _qcForm.SetIdle("检查启动失败，请重试"); } catch { }
                        Log("检查启动异常: " + ex);
                    }
                };

                _qcForm.Show();
                _qcForm.BringToFront();
            }
            catch (Exception ex)
            {
                Log("OnQc 异常: " + ex);
                MessageBox.Show("审图异常：" + ex.Message, "工程图QC",
                    MessageBoxButtons.OK, MessageBoxIcon.Error);
            }
        }

        /// <summary>
        /// PDF/AI 审图（2026-10-25 用户定稿方向）。
        /// 当前是工程图 → 临时导出 PDF；否则弹窗选 PDF 文件。
        /// 审核完删除临时 PDF（用户要求：图片/PDF 不保留）。
        /// </summary>
        public void OnPdfAudit()
        {
            Log("[PDF-AUDIT] 命令被点击");
            try
            {
                if (_pdfAuditForm != null && !_pdfAuditForm.IsDisposed)
                {
                    _pdfAuditForm.Show();
                    _pdfAuditForm.BringToFront();
                    return;
                }

                string initialSource = "未选择图纸，请导入 PDF";
                IModelDoc2 doc = null;
                try { doc = _swApp.ActiveDoc as IModelDoc2; } catch { }
                if (doc != null)
                {
                    try
                    {
                        if (doc.GetType() == (int)swDocumentTypes_e.swDocDRAWING)
                            initialSource = doc.GetTitle() + "（当前图纸页）";
                    }
                    catch { }
                }
                var form = new PdfAuditForm(initialSource);
                _pdfAuditForm = form;
                form.AuditRequested += delegate(string importedPdf)
                {
                    BeginPdfAudit(form, importedPdf);
                };
                form.FormClosed += delegate
                {
                    if (ReferenceEquals(_pdfAuditForm, form)) _pdfAuditForm = null;
                };
                form.Show();
                form.BringToFront();
            }
            catch (Exception ex)
            {
                Log("[PDF-AUDIT] 命令异常: " + ex);
                MessageBox.Show("PDF视觉整图审核异常：" + ex.Message, "PDF视觉整图审核",
                    MessageBoxButtons.OK, MessageBoxIcon.Error);
            }
            finally
            {
                Cursor.Current = Cursors.Default;
            }
        }

        private void BeginPdfAudit(PdfAuditForm form, string importedPdf)
        {
            if (form == null || form.IsDisposed || form.AuditRunning) return;
            try
            {
                string configMessage;
                if (!PdfAuditRunner.ValidateConfiguration(out configMessage))
                {
                    DialogResult setup = MessageBox.Show(form,
                        configMessage + "\n\n是否现在配置并测试 PDF 视觉模型？",
                        "PDF视觉整图审核", MessageBoxButtons.YesNo, MessageBoxIcon.Information);
                    if (setup != DialogResult.Yes) return;
                    using (var settings = new PdfAuditSettingsForm())
                    {
                        if (settings.ShowDialog(form) != DialogResult.OK) return;
                    }
                    if (!PdfAuditRunner.ValidateConfiguration(out configMessage))
                    {
                        form.SetFailure(configMessage);
                        return;
                    }
                }

                string jobId = Guid.NewGuid().ToString("N");
                string jobDir = Path.Combine(Path.GetTempPath(), "SwQcAddin", "pdf_audit", jobId);
                Directory.CreateDirectory(jobDir);
                form.SetJobDirectory(jobDir);

                string pdfPath = importedPdf ?? "";
                int expectedPages = 0;
                if (!string.IsNullOrEmpty(pdfPath))
                {
                    if (!File.Exists(pdfPath))
                    {
                        form.SetFailure("导入的 PDF 已不存在，请重新导入。");
                        return;
                    }
                    form.BeginAudit(Path.GetFileName(pdfPath) + "（导入PDF）");
                }
                else
                {
                    IModelDoc2 doc = null;
                    try { doc = _swApp.ActiveDoc as IModelDoc2; } catch { }
                    int docType = -1;
                    try { if (doc != null) docType = doc.GetType(); } catch { }
                    if (doc == null || docType != (int)swDocumentTypes_e.swDocDRAWING)
                    {
                        form.SetFailure("当前没有活动的 SolidWorks 工程图，请先打开工程图或点击“导入PDF”。");
                        return;
                    }
                    form.BeginAudit(doc.GetTitle() + "（当前图纸页）");
                    form.SetStatus("正在把当前图纸页导出为 PDF…");
                    Cursor.Current = Cursors.WaitCursor;
                    PdfExportResult exported = SwPdfExporter.ExportCurrentSheet(_swApp, doc, jobDir);
                    Cursor.Current = Cursors.Default;
                    if (!exported.Success)
                    {
                        form.SetFailure(exported.Error + " 请点击“导入PDF”选择其他文件。" );
                        return;
                    }
                    pdfPath = exported.PdfPath;
                    expectedPages = exported.ExpectedPages;
                    if (!string.IsNullOrEmpty(exported.Warning))
                        Log("[PDF-AUDIT] " + exported.Warning);
                }

                Log("[PDF-AUDIT] 开始检测 source=" + Path.GetFileName(pdfPath));
                StartPdfDirectAudit(form, pdfPath, jobDir, expectedPages);
            }
            catch (Exception ex)
            {
                Log("[PDF-AUDIT] 启动检测异常: " + ex);
                form.SetFailure("启动 PDF 检测失败：" + ex.Message);
            }
            finally
            {
                Cursor.Current = Cursors.Default;
            }
        }

        private void StartPdfDirectAudit(PdfAuditForm form, string inputPdf, string jobDir, int expectedPages)
        {
            System.Threading.ThreadPool.QueueUserWorkItem(delegate
            {
                PdfAuditReport report;
                try
                {
                    report = PdfAuditRunner.Run(inputPdf, jobDir, expectedPages,
                        delegate(string msg) { try { if (!form.IsDisposed) form.SetStatus(msg); } catch { } },
                        delegate { return form.IsDisposed || form.CancelRequested; });
                }
                catch (Exception ex)
                {
                    report = new PdfAuditReport { status = "failed", error = "PDF 审核异常：" + ex.Message,
                        JobDirectory = jobDir };
                }
                try
                {
                    if (form.IsDisposed) { PdfAuditForm.CleanupJobDirectory(jobDir); return; }
                    PdfAuditReport finalReport = report;
                    form.BeginInvoke((MethodInvoker)delegate
                    {
                        if (form.IsDisposed) return;
                        form.SetReport(finalReport);
                        Log("[PDF-AUDIT] 完成 status=" + (finalReport.status ?? "") +
                            " issues=" + finalReport.AllIssues().Count +
                            " error=" + (finalReport.error ?? ""));
                    });
                }
                catch (Exception ex)
                {
                    Log("[PDF-AUDIT] 审核回调异常: " + ex);
                }
            });
        }

        private void StartPdfConfirmedAudit(PdfAuditForm form, string inputPdf, string jobDir,
            int expectedPages, string confirmedPath)
        {
            System.Threading.ThreadPool.QueueUserWorkItem(delegate
            {
                PdfAuditReport report;
                try
                {
                    report = PdfAuditRunner.RunConfirmedAudit(inputPdf, jobDir, expectedPages, confirmedPath,
                        delegate(string msg) { try { if (!form.IsDisposed) form.SetStatus(msg); } catch { } },
                        delegate { return form.IsDisposed || form.CancelRequested; });
                }
                catch (Exception ex)
                {
                    report = new PdfAuditReport { status = "failed", error = "PDF 专业审核异常：" + ex.Message,
                        JobDirectory = jobDir };
                }
                try
                {
                    if (form.IsDisposed) { PdfAuditForm.CleanupJobDirectory(jobDir); return; }
                    PdfAuditReport finalReport = report;
                    form.BeginInvoke((MethodInvoker)delegate
                    {
                        if (form.IsDisposed) return;
                        form.SetReport(finalReport);
                        Log("[PDF-AUDIT] 完成 status=" + (finalReport.status ?? "") +
                            " issues=" + finalReport.AllIssues().Count + " human_confirmed=" +
                            finalReport.human_confirmed);
                    });
                }
                catch (Exception ex)
                {
                    Log("[PDF-AUDIT] 专业审核回调异常: " + ex);
                }
            });
        }

        /// <summary>
        /// 兼容旧版可能残留的命令回调：不再弹独立窗口，只打开审图主窗口的 AI 页签。
        /// 新版菜单/工具栏不再注册该命令。
        /// </summary>
        public void OnAiSettings()
        {
            try
            {
                if (_qcForm == null || _qcForm.IsDisposed) OnQc();
                if (_qcForm != null && !_qcForm.IsDisposed) _qcForm.ShowAiSettingsTab();
                Log("已切换到审图窗口 AI接口设置页签");
            }
            catch (Exception ex)
            {
                Log("切换 AI接口设置页签异常: " + ex);
                MessageBox.Show("打开 AI接口设置页签失败：" + ex.Message, "工程图QC");
            }
        }

        /// <summary>调试：把原生读取结果落盘，方便把字段发回来分析。</summary>
        public void OnNativeInfo()
        {
            IModelDoc2 doc = _swApp.ActiveDoc as IModelDoc2;
            if (doc == null)
            {
                MessageBox.Show("当前没有打开文档。\n请打开工程图，或打开零件（会自动找同名工程图）。",
                    "工程图QC · 原生信息", MessageBoxButtons.OK, MessageBoxIcon.Information);
                return;
            }
            IModelDoc2 drawing = doc;
            int t = 0;
            try { t = doc.GetType(); } catch { }
            if (t != (int)swDocumentTypes_e.swDocDRAWING)
            {
                string cand = FindSiblingDrawing(doc);
                if (string.IsNullOrEmpty(cand))
                {
                    MessageBox.Show("无对应工程图。\n（按「同目录、同文件名、扩展名 .SLDDRW」查找）",
                        "工程图QC · 原生信息", MessageBoxButtons.OK, MessageBoxIcon.Warning);
                    return;
                }
                MessageBox.Show("检测到对应工程图：\n" + cand +
                                "\n\n请先打开它（或直接点「审图」，会自动打开）。",
                    "工程图QC · 原生信息", MessageBoxButtons.OK, MessageBoxIcon.Information);
                return;
            }

            string summary = "", json = "";
            try
            {
                Cursor.Current = Cursors.WaitCursor;
                json = SwDocReader.ReadNativeJson(_swApp, drawing, out summary);
            }
            catch (Exception ex)
            {
                MessageBox.Show("读取失败：" + ex.Message, "工程图QC");
                return;
            }
            finally { Cursor.Current = Cursors.Default; }

            string p = SwDocReader.SaveJson(json,
                Path.GetFileNameWithoutExtension(SafePath(drawing)));
            MessageBox.Show(
                "已直读当前工程图原生信息。\n\n" + summary + "\n\n文件：\n" + p +
                "\n\n把这个 .sw.json 文件发回来，就能按真实字段完善检查。",
                "工程图QC · 原生信息", MessageBoxButtons.OK, MessageBoxIcon.Information);
        }

        // ------------------------------------------------------------ 找目标工程图
        /// <summary>在同目录找与零件同名的 .SLDDRW；找不到返回 null。</summary>
        private string FindSiblingDrawing(IModelDoc2 doc)
        {
            string path = "";
            try { path = doc.GetPathName(); } catch { }
            if (string.IsNullOrEmpty(path)) return null;
            string dir, baseName;
            try
            {
                dir = Path.GetDirectoryName(path);
                baseName = Path.GetFileNameWithoutExtension(path);
            }
            catch { return null; }
            if (string.IsNullOrEmpty(dir)) return null;

            string candidate = Path.Combine(dir, baseName + ".SLDDRW");
            if (File.Exists(candidate)) return candidate;
            try
            {
                foreach (string f in Directory.GetFiles(dir, "*.SLDDRW"))
                {
                    if (string.Equals(Path.GetFileNameWithoutExtension(f), baseName,
                                      StringComparison.OrdinalIgnoreCase))
                        return f;
                }
            }
            catch { }
            return null;
        }

        private IModelDoc2 ResolveDrawing(bool openIfFound)
        {
            IModelDoc2 doc = _swApp.ActiveDoc as IModelDoc2;
            if (doc == null)
            {
                MessageBox.Show("请先打开一张工程图（.SLDDRW）。\n（也可以打开零件，我会自动找它同名的工程图）",
                    "工程图QC", MessageBoxButtons.OK, MessageBoxIcon.Information);
                return null;
            }

            int t = 0;
            try { t = doc.GetType(); } catch { }
            if (t == (int)swDocumentTypes_e.swDocDRAWING) return doc;

            string candidate = FindSiblingDrawing(doc);
            if (string.IsNullOrEmpty(candidate))
            {
                string path = "";
                try { path = doc.GetPathName(); } catch { }
                string hint = string.IsNullOrEmpty(path)
                    ? "当前零件还没保存过，无法按文件名找工程图。"
                    : ("按「同目录、同文件名」查找：" +
                       Path.Combine(Path.GetDirectoryName(path),
                                    Path.GetFileNameWithoutExtension(path) + ".SLDDRW"));
                MessageBox.Show(
                    "无对应工程图。\n\n" + hint + "\n\n" +
                    "建议：\n  · 在零件同目录创建同名工程图\n  · 或直接打开工程图后再点「审图」",
                    "工程图QC", MessageBoxButtons.OK, MessageBoxIcon.Warning);
                return null;
            }

            if (!openIfFound)
            {
                MessageBox.Show("检测到对应工程图：\n" + candidate +
                                "\n\n点「审图」即可检查它。",
                    "工程图QC", MessageBoxButtons.OK, MessageBoxIcon.Information);
                return null;
            }

            int err = 0, warn = 0;
            IModelDoc2 opened = null;
            try
            {
                opened = (IModelDoc2)_swApp.OpenDoc6(candidate,
                    (int)swDocumentTypes_e.swDocDRAWING,
                    (int)swOpenDocOptions_e.swOpenDocOptions_Silent,
                    "", ref err, ref warn);
            }
            catch { }
            if (opened == null)
            {
                MessageBox.Show("找到工程图但打开失败：\n" + candidate + "\n（错误码 " + err + "）",
                    "工程图QC", MessageBoxButtons.OK, MessageBoxIcon.Error);
                return null;
            }
            return opened;
        }

        static string SafePath(IModelDoc2 doc)
        {
            try { return doc.GetPathName(); } catch { return ""; }
        }

        // ------------------------------------------------------------ 日志与兜底
        /// <summary>把关键步骤写进 %TEMP%\SwQcAddin\addin.log，方便远程排查。</summary>
        public static void Log(string msg)
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

        /// <summary>旧版菜单兜底：直接在「工具」下挂扁平菜单项（CommandManager 失败时用）。</summary>
        private bool AddLegacyMenu()
        {
            int cookie = _addinId;
            int created = 0;
            if (_swApp.AddMenuItem4((int)swDocumentTypes_e.swDocDRAWING, cookie,
                "工程图QC · 审图", 0, "OnQc", "EnableQc", "检查当前工程图", "") > 0) created++;
            if (_swApp.AddMenuItem4((int)swDocumentTypes_e.swDocDRAWING, cookie,
                "工程图QC · 原生信息", 1, "OnNativeInfo", "EnableQc", "直读原生信息", "") > 0) created++;
            if (_swApp.AddMenuItem4((int)swDocumentTypes_e.swDocDRAWING, cookie,
                "工程图QC · PDF视觉整图审核", 3, "OnPdfAudit", "EnableQc", "视觉模型审核整张PDF图纸", "") > 0) created++;
            _swApp.AddMenuItem4((int)swDocumentTypes_e.swDocPART, cookie,
                "工程图QC · 审图", 0, "OnQc", "EnableQc", "找同名工程图并检查", "");
            _swApp.AddMenuItem4((int)swDocumentTypes_e.swDocASSEMBLY, cookie,
                "工程图QC · 审图", 0, "OnQc", "EnableQc", "找同名工程图并检查", "");
            return created == 3;
        }
        // ------------------------------------------------------------ COM 注册
        [ComRegisterFunction]
        public static void RegisterFunction(Type t)
        {
            WriteAddinKey("Software\\SolidWorks\\Addins\\{" + t.GUID.ToString() + "}");
            WriteAddinKey("Software\\SolidWorks\\AddInsStartup\\{" + t.GUID.ToString() + "}");
        }

        [ComUnregisterFunction]
        public static void UnregisterFunction(Type t)
        {
            RemoveKey("Software\\SolidWorks\\Addins\\{" + t.GUID.ToString() + "}");
            RemoveKey("Software\\SolidWorks\\AddInsStartup\\{" + t.GUID.ToString() + "}");
        }

        static void WriteAddinKey(string path)
        {
            foreach (Microsoft.Win32.RegistryKey root in new[] {
                Microsoft.Win32.Registry.LocalMachine, Microsoft.Win32.Registry.CurrentUser })
            {
                try
                {
                    using (var k = root.CreateSubKey(path))
                    {
                        if (k == null) continue;
                        k.SetValue("", 1, Microsoft.Win32.RegistryValueKind.DWord);
                        k.SetValue("Description", "工程图质量检查（本地规则，直读原生图纸）");
                        k.SetValue("Title", "工程图QC");
                    }
                }
                catch { }
            }
        }

        static void RemoveKey(string path)
        {
            foreach (Microsoft.Win32.RegistryKey root in new[] {
                Microsoft.Win32.Registry.LocalMachine, Microsoft.Win32.Registry.CurrentUser })
            {
                try { root.DeleteSubKeyTree(path, false); } catch { }
            }
        }
    }
}
