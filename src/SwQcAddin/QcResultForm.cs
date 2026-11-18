using System;
using System.Collections.Generic;
using System.Drawing;
using System.Runtime.InteropServices;
using System.Windows.Forms;
using SwModelDoc = SolidWorks.Interop.sldworks.IModelDoc2;

namespace SwQc
{
    /// <summary>
    /// 审图结果窗口（2026-10-25 重写恢复版）。
    /// 页签：尺寸/公差 / 审图结果 / 检查设置 / 标准库 / AI接口设置 / 帮助。
    /// 关键行为（用户要求）：
    ///   · 审图结果按「图纸信息 / 尺寸 / 技术要求」三类分组，通过项也显示；
    ///   · 尺寸公差表：轮廓尺寸、带尺寸公差、带几何公差、粗糙度全部列出；
    ///   · 列表所有内容默认自动识别，**但都可以手工修改**，改完立即重算；
    ///   · 只有「找不到关联尺寸」的几何公差才出提醒条 + 「指定归属」。
    /// </summary>
    public class QcResultForm : Form
    {
        static readonly Color Brand = Color.FromArgb(31, 78, 121);
        static readonly Color Ink = Color.FromArgb(55, 65, 81);
        static readonly Color Muted = Color.FromArgb(107, 122, 141);
        static readonly Color Line = Color.FromArgb(218, 223, 230);
        static readonly Font F = new Font("微软雅黑", 9f);
        static readonly Font FB = new Font("微软雅黑", 9f, FontStyle.Bold);
        static readonly Font FSmall = new Font("微软雅黑", 8.5f);

        private QcReport _r;
        /// <summary>窗口会话唯一数据源；三张表只允许从这里投影。</summary>
        private QcDocument _doc;
        private QcDocumentStore _docStore;
        private bool _storageLoadAttempted;
        private bool _storageBlocked;
        private bool _ignoreStoredForSession;
        private string _storageError = "";
        private string _lastSyncError = "";
        private TabControl _tabs;
        private DataGridView _grid;
        private QcDetailView _detail;
        private DataGridView _tolGrid;
        private Label _tolTip;              // 尺寸/公差页顶部说明（未关联提醒写这里）
        private string _tolTipDefault = "";
        private List<DatumRow> _datums = new List<DatumRow>();
        private readonly HashSet<string> _deletedFp = new HashSet<string>();
        /// <summary>下一次检查强制重新从 SW 读取图纸（「重读图纸」按钮用）。</summary>
        public bool ForceRereadNext = false;
        private readonly List<QcDimRow> _manualRows = new List<QcDimRow>();
        // 2026-10-29：尺寸/公差表只读 + 单行编辑模式
        private QcDimRow _editRow = null;
        private QcDimRow _editBackup = null;
        private bool _rowEditing = false;
        private bool _confirmClosingEdit = false;
        private Button _btnEditDone = null;
        /// <summary>v188：初次读取到的“图纸全部几何公差”（重读时重建）</summary>
        private readonly List<QcUnlinkedGtol> _allGtols = new List<QcUnlinkedGtol>();
        private readonly List<QcUnlinkedGtol> _initialGtols = new List<QcUnlinkedGtol>();
        private Button _btnEditCancel = null;
        private readonly List<UndoOp> _undo = new List<UndoOp>();

        class UndoOp
        {
            public string Type = "";     // delete / add
            public QcDimRow Row;
            public int Index;
        }
        private CalcPanel _calc;
        private Label _t3, _aiLabel, _statusTime, _statusDir, _statusTip;
        private Label[] _cardNums = new Label[4];
        private bool _busy;
        /// <summary>自测/出图时抑制「指定归属」自动弹窗</summary>
        public static bool SuppressAutoPrompt = false;
        private Button _btnCheck;
        private Button _btnLink;           // 状态栏「指定归属」按钮（有未关联时亮起）
        private Button _btnDir;
        private ToolTip _tip;
        private Form _childDlg;            // 当前打开的子对话框（关闭主窗时要一起关）
        private bool _closing;             // 正在关闭            // 打开报告目录           // 状态栏「指定归属」按钮（有未关联时亮起）          // 底部「检查」按钮（检查中禁用）
        private int _runCount;             // 本次窗口第几次检查
        private DateTime _checkStart;      // 本次检查开始时间
        private System.Windows.Forms.Timer _flashTimer;  // 完成提示闪一下
        private SplitContainer _resSplit;   // 审图结果页左右分割（右侧显示完整信息）
   // 审图结果页左右分割（右侧显示完整信息）
        /// <summary>插件版本（读同级 version.txt，如 SwQcAddin_v116.dll）</summary>
        private string _verText = "";

        /// <summary>用户点「检查」→ 主插件去跑判定</summary>
        public event EventHandler CheckRequested;

        public QcResultForm(QcReport report) : this(report, null) { }

        public QcResultForm(QcReport report, SwModelDoc drawing)
        {
            _r = report ?? new QcReport();
            BindDrawing(drawing, false);
            BuildUi();
            UpdateReport(_r);
            // 2026-10-26：窗体显示后再设分割位置（之前只在 SizeChanged 里设，常没生效 → 右侧被挤没）
            Shown += delegate
            {
                try
                {
                    SetSplitSafe(_resSplit, 0.60, 420, 260);
                }
                catch { }
            };
        }

        /// <summary>切换工程图时必须切换文档存储，旧图的 QcDocument 绝不能继续投影到新图。</summary>
        public void BindDrawing(SwModelDoc drawing, bool resetSession)
        {
            try { if (_docStore != null) _docStore.Dispose(); } catch { }
            _docStore = null;
            _storageLoadAttempted = false;
            _ignoreStoredForSession = false;
            _storageBlocked = false;
            _storageError = "";
            if (resetSession)
            {
                _doc = null;
                try { QcRunner.ResetSession(); _deletedFp.Clear(); _manualRows.Clear(); _undo.Clear(); } catch { }
            }
            if (drawing != null)
            {
                try { _docStore = new QcDocumentStore(drawing); }
                catch (Exception ex) { _storageBlocked = true; _storageError = ex.Message; }
            }
        }

        /// <summary>右侧下半区：全部尺寸公差 + 全部几何公差（含未关联）。</summary>
        string BuildAllInfoText()
        {
            var sb = new System.Text.StringBuilder();
            try
            {
                var dims = new List<QcDimRow>();
                foreach (var r in (_r != null ? _r.DimRows : null) ?? new List<QcDimRow>())
                {
                    if (r == null || !r.HasTol || string.IsNullOrEmpty(r.Content)) continue;
                    if ((r.Kind ?? "").Contains("粗糙度")) continue;
                    if (string.IsNullOrEmpty(r.TolDisplay) || r.TolDisplay == "未注" || r.TolDisplay == "—") continue;
                    dims.Add(r);
                }
                sb.AppendLine("【尺寸公差】共 " + dims.Count + " 条");
                if (dims.Count == 0)
                {
                    sb.AppendLine("（无带公差的尺寸）");
                }
                else
                {
                    foreach (var r in dims)
                    {
                        sb.Append("· ").Append(r.Content);
                        if (!string.IsNullOrEmpty(r.TolDisplay)) sb.Append("　").Append(r.TolDisplay);
                        if (!string.IsNullOrEmpty(r.IT)) sb.Append("　").Append(r.IT);
                        sb.AppendLine();
                    }
                }
                sb.AppendLine();

                int nG = 0;
                var lines = new System.Text.StringBuilder();
                foreach (var r in (_r != null ? _r.DimRows : null) ?? new List<QcDimRow>())
                {
                    if (r == null || r.Gtols == null) continue;
                    if (r.Gtols.Count == 0 && string.IsNullOrEmpty(r.GtolSym)) continue;
                    foreach (var g in r.Gtols)
                    {
                        nG++;
                        string name = GtolSymbols.NameOf(g.Sym);
                        lines.Append("· ").Append(r.Content).Append(" → ")
                             .Append(g.Sym).Append(g.Val);
                        if (!string.IsNullOrEmpty(g.Mod)) lines.Append(g.Mod);
                        if (!string.IsNullOrEmpty(g.Dat1)) lines.Append(" | ").Append(g.Dat1);
                        if (!string.IsNullOrEmpty(g.Dat2)) lines.Append(" | ").Append(g.Dat2);
                        if (!string.IsNullOrEmpty(g.Dat3)) lines.Append(" | ").Append(g.Dat3);
                        lines.Append("　(").Append(name).Append(")");
                        if (!string.IsNullOrEmpty(g.Reason)) lines.Append("　").Append(g.Reason);
                        lines.AppendLine();
                    }
                }
                var unl = (_r != null ? _r.UnlinkedGtols : null) ?? new List<QcUnlinkedGtol>();
                sb.AppendLine("【几何公差】共 " + (nG + unl.Count) + " 条");
                if (nG + unl.Count == 0) sb.AppendLine("（无几何公差）");
                else
                {
                    sb.Append(lines.ToString());
                    foreach (var u in unl)
                    {
                        sb.Append("· (未关联) ").Append(u.Sym).Append(u.Val);
                        if (!string.IsNullOrEmpty(u.Mod)) sb.Append(u.Mod);
                        if (!string.IsNullOrEmpty(u.Dat1)) sb.Append(" | ").Append(u.Dat1);
                        if (!string.IsNullOrEmpty(u.Dat2)) sb.Append(" | ").Append(u.Dat2);
                        if (!string.IsNullOrEmpty(u.Dat3)) sb.Append(" | ").Append(u.Dat3);
                        sb.Append("　(").Append(GtolSymbols.NameOf(u.Sym)).Append(")");
                        if (!string.IsNullOrEmpty(u.View)) sb.Append("　视图 ").Append(u.View);
                        sb.AppendLine();
                    }
                }
            }
            catch (Exception ex) { sb.AppendLine("汇总生成失败：" + ex.Message); }
            return sb.ToString();
        }

        /// <summary>
        /// 安全设置分割位置：先把距离夹到 [Panel1MinSize, 总长-Panel2MinSize-分割条]，
        /// 容器太小时直接跳过（否则 WinForms 会抛 SplitterDistance 越界）。
        /// </summary>
        static void SetSplitSafe(SplitContainer sp, double ratio, int min1, int min2)
        {
            if (sp == null) return;
            try
            {
                int total = (sp.Orientation == Orientation.Vertical) ? sp.Width : sp.Height;
                int bar = sp.SplitterWidth;
                if (total < min1 + min2 + bar + 10) return;
                sp.Panel1MinSize = min1;
                sp.Panel2MinSize = min2;
                int d = (int)(total * ratio);
                int hi = total - min2 - bar;
                if (hi < min1) return;
                if (d < min1) d = min1;
                if (d > hi) d = hi;
                sp.SplitterDistance = d;
            }
            catch { }
        }

        /// <summary>稳妥关闭：先取消置顶，再 Hide→Close→Dispose，避免"点关闭没反应"。</summary>
        bool ConfirmDiscardEdit()
        {
            if (!_rowEditing) return true;
            if (MessageBox.Show(this, "正在编辑一行，关闭后未保存的修改将丢失。确定关闭？", "工程图QC",
                    MessageBoxButtons.YesNo, MessageBoxIcon.Warning) != DialogResult.Yes)
                return false;
            CancelRowEdit();
            return true;
        }

        void CloseFromUser()
        {
            if (_closing) return;
            if (!ConfirmDiscardEdit()) return;
            _closing = true;
            _busy = false;
            try { QcRunner.ResetSession(); ResetAllGtols(); } catch { }
            _doc = null;
            try { Cursor = Cursors.Default; } catch { }
            try { if (_childDlg != null && !_childDlg.IsDisposed) _childDlg.Close(); } catch { }
            try { TopMost = false; } catch { }
            try { Hide(); } catch { }
            try { Application.DoEvents(); } catch { }
            try { Close(); } catch { }
            try { if (!IsDisposed) Dispose(); } catch { }
        }

        protected override void OnFormClosing(FormClosingEventArgs e)
        {
            if (!_confirmClosingEdit && _rowEditing)
            {
                if (MessageBox.Show(this, "正在编辑一行，关闭后未保存的修改将丢失。确定关闭？", "工程图QC",
                        MessageBoxButtons.YesNo, MessageBoxIcon.Warning) != DialogResult.Yes)
                {
                    e.Cancel = true;
                    return;
                }
                _confirmClosingEdit = true;
                try { CancelRowEdit(); } catch { }
            }
            _closing = true;
            _busy = false;
            try { QcRunner.ResetSession(); ResetAllGtols(); } catch { }
            _doc = null;
            try
            {
                if (_flashTimer != null)
                {
                    _flashTimer.Stop();
                    _flashTimer.Dispose();
                    _flashTimer = null;
                }
            }
            catch { }
            try { if (_childDlg != null && !_childDlg.IsDisposed) _childDlg.Close(); } catch { }
            try { if (_docStore != null) { _docStore.Dispose(); _docStore = null; } } catch { }
            base.OnFormClosing(e);
        }

        // ================================================================ 界面
        void BuildUi()
        {
            Text = "工程图 QC 审查";
            try
            {
                string dir = System.IO.Path.GetDirectoryName(typeof(QcResultForm).Assembly.Location) ?? "";
                string vf = System.IO.Path.Combine(dir, "version.txt");
                if (System.IO.File.Exists(vf)) _verText = System.IO.File.ReadAllText(vf).Trim();
            }
            catch { }
            if (!string.IsNullOrEmpty(_verText)) Text += "  —  " + _verText;

            StartPosition = FormStartPosition.CenterScreen;
            ClientSize = new Size(1100, 800);
            MinimumSize = new Size(920, 620);
            BackColor = Color.White;
            Font = F;
            ControlBox = true;
            KeyPreview = true;
            // 2026-10-26：Esc 直接关窗（用户反馈"窗口关不掉"时的兜底）
            KeyDown += delegate(object s1, KeyEventArgs e1)
            {
                if (e1.KeyCode == Keys.Escape)
                {
                    e1.Handled = true;
                    CloseFromUser();
                    return;
                }
                if (e1.Control && e1.KeyCode == Keys.Z)
                {
                    // 单元格编辑中的 Ctrl+Z 应交给编辑控件撤销文字，不能误撤整行操作。
                    if (_tolGrid != null && _tolGrid.IsCurrentCellInEditMode &&
                        _tolGrid.EditingControl != null && _tolGrid.EditingControl.ContainsFocus)
                        return;
                    e1.Handled = true;
                    UndoLast();
                    return;
                }
                if (e1.Control && e1.KeyCode == Keys.R)
                {
                    e1.Handled = true;
                    RereadDrawing();
                }
            };
            try { TopMost = QcRunner.LoadConfig().TopMost == "1"; } catch { }

            // 方案A：首页顶部由“品牌栏 + 三步工作流”组成。
            var topArea = new Panel();
            topArea.Dock = DockStyle.Top;
            topArea.Height = 134;
            topArea.BackColor = Color.White;
            Controls.Add(topArea);

            var head = new Panel();
            head.Dock = DockStyle.Top;
            head.Height = 86;
            head.BackColor = Brand;
            topArea.Controls.Add(head);

            var l1 = new Label();
            l1.Text = "工程图 QC 审查";
            l1.Font = new Font("微软雅黑", 15f, FontStyle.Bold);
            l1.ForeColor = Color.White;
            l1.AutoSize = true;
            l1.Location = new Point(16, 8);
            head.Controls.Add(l1);

            var lNote = new Label();
            lNote.Text = "本地规则 · 图纸不出本机";
            lNote.Font = FSmall;
            lNote.ForeColor = Color.FromArgb(200, 215, 235);
            lNote.AutoSize = false;
            lNote.TextAlign = ContentAlignment.MiddleRight;
            lNote.Size = new Size(300, 20);
            lNote.Location = new Point(ClientSize.Width - 320, 30);
            lNote.Anchor = AnchorStyles.Top | AnchorStyles.Right;
            head.Controls.Add(lNote);

            // 2026-10-26 用户要求：界面上直接显示插件版本
            var lVer = new Label();
            lVer.Text = string.IsNullOrEmpty(_verText) ? "版本（未知）" : "版本 " + _verText;
            lVer.Font = new Font("微软雅黑", 9f, FontStyle.Bold);
            lVer.ForeColor = Color.FromArgb(235, 242, 252);
            lVer.AutoSize = false;
            lVer.TextAlign = ContentAlignment.MiddleRight;
            lVer.Size = new Size(300, 20);
            lVer.Location = new Point(ClientSize.Width - 320, 8);
            lVer.Anchor = AnchorStyles.Top | AnchorStyles.Right;
            head.Controls.Add(lVer);

            _t3 = new Label();
            _t3.Text = "";
            _t3.Font = FB;
            _t3.ForeColor = Color.White;
            _t3.AutoSize = false;
            _t3.Size = new Size(ClientSize.Width - 32, 24);
            _t3.Location = new Point(18, 44);
            _t3.Anchor = AnchorStyles.Top | AnchorStyles.Left | AnchorStyles.Right;
            _t3.AutoEllipsis = true;
            head.Controls.Add(_t3);

            var workflow = new Panel();
            workflow.Dock = DockStyle.Bottom;
            workflow.Height = 48;
            workflow.BackColor = Color.White;
            workflow.Padding = new Padding(12, 7, 12, 7);
            topArea.Controls.Add(workflow);

            var flowSteps = new Label();
            flowSteps.Text = "① 重新读图    →    ② 确认尺寸/归属    →    ③ 检查";
            flowSteps.Font = new Font("微软雅黑", 10f, FontStyle.Bold);
            flowSteps.ForeColor = Brand;
            flowSteps.BackColor = Color.FromArgb(229, 239, 250);
            flowSteps.TextAlign = ContentAlignment.MiddleCenter;
            flowSteps.Location = new Point(12, 7);
            flowSteps.Size = new Size(ClientSize.Width - 244, 34);
            flowSteps.Anchor = AnchorStyles.Top | AnchorStyles.Left | AnchorStyles.Right;
            workflow.Controls.Add(flowSteps);

            var topReread = new Button();
            topReread.Text = "重新读图";
            topReread.Font = FSmall;
            topReread.Size = new Size(94, 34);
            topReread.Location = new Point(ClientSize.Width - 222, 7);
            topReread.Anchor = AnchorStyles.Top | AnchorStyles.Right;
            topReread.Click += delegate { RereadDrawing(); };
            workflow.Controls.Add(topReread);

            var topCheck = new Button();
            _btnCheck = topCheck;
            topCheck.Text = "检 查";
            topCheck.Font = FB;
            topCheck.BackColor = Brand;
            topCheck.ForeColor = Color.White;
            topCheck.FlatStyle = FlatStyle.Flat;
            topCheck.Size = new Size(94, 34);
            topCheck.Location = new Point(ClientSize.Width - 116, 7);
            topCheck.Anchor = AnchorStyles.Top | AnchorStyles.Right;
            topCheck.Click += delegate { RequestCheck(); };
            workflow.Controls.Add(topCheck);

            var status = new Panel();
            status.Dock = DockStyle.Bottom;
            status.Height = 34;
            status.BackColor = Color.FromArgb(244, 246, 249);
            Controls.Add(status);

            _statusTime = new Label();
            _statusTime.AutoSize = false;
            _statusTime.Font = FSmall;
            _statusTime.ForeColor = Muted;
            _statusTime.Location = new Point(10, 8);
            // 状态栏最左显示版本（用户要求界面可见）
            var lVer2 = new Label();
            lVer2.AutoSize = false;
            lVer2.Font = FSmall;
            lVer2.ForeColor = Brand;
            lVer2.Text = string.IsNullOrEmpty(_verText) ? "" : ("版本 " + _verText);
            lVer2.Location = new Point(10, 8);
            lVer2.Size = new Size(220, 18);          // 2026-10-26 加宽，避免被截断
            status.Controls.Add(lVer2);
            _statusTime.Location = new Point(230, 8);
            _statusTime.Size = new Size(260, 18);
            status.Controls.Add(_statusTime);

            _statusDir = new Label();
            _statusDir.AutoSize = false;
            _statusDir.Font = FSmall;
            _statusDir.ForeColor = Muted;
            _statusDir.Visible = false;   // 报告目录改到「打开报告目录」按钮提示，避免状态栏重叠
            status.Controls.Add(_statusDir);

            _statusTip = new Label();
            _statusTip.AutoSize = false;
            _statusTip.Font = FSmall;
            _statusTip.ForeColor = Color.FromArgb(181, 71, 8);
            _statusTip.Location = new Point(500, 8);
            _statusTip.Size = new Size(150, 18);
            _statusTip.AutoEllipsis = true;
            // 状态栏随窗口宽度重排：右侧按钮固定，左侧文字区自适应，永不重叠
            status.Resize += delegate
            {
                try
                {
                    int btnLeft = ClientSize.Width - 550;              // 「确认保存」按钮左边界
                    int tipX = 500;
                    int tipW = Math.Max(120, btnLeft - tipX - 8);
                    _statusTip.Location = new Point(tipX, 8);
                    _statusTip.Size = new Size(tipW, 18);
                }
                catch { }
            };
            status.Controls.Add(_statusTip);

            var btnSaveDb = new Button();
            btnSaveDb.Text = "确认保存";
            btnSaveDb.Font = FSmall;
            btnSaveDb.Size = new Size(96, 28);
            btnSaveDb.Location = new Point(ClientSize.Width - 540, 2);
            btnSaveDb.Anchor = AnchorStyles.Top | AnchorStyles.Right;
            btnSaveDb.Click += delegate { ConfirmSaveToDb(); };
            status.Controls.Add(btnSaveDb);
            btnSaveDb.BringToFront();


            var btnDone = new Button();
            _btnEditDone = btnDone;
            btnDone.Text = "完成";
            btnDone.Font = FB;
            btnDone.BackColor = Color.FromArgb(46, 125, 50);
            btnDone.ForeColor = Color.White;
            btnDone.FlatStyle = FlatStyle.Flat;
            btnDone.Size = new Size(84, 28);
            btnDone.Location = new Point(ClientSize.Width - 520, 2);
            btnDone.Anchor = AnchorStyles.Top | AnchorStyles.Right;
            btnDone.Visible = false;
            btnDone.Click += delegate { CompleteRowEdit(); };
            status.Controls.Add(btnDone);

            var btnCancel = new Button();
            _btnEditCancel = btnCancel;
            btnCancel.Text = "取消";
            btnCancel.Font = FSmall;
            btnCancel.Size = new Size(74, 28);
            btnCancel.Location = new Point(ClientSize.Width - 608, 2);
            btnCancel.Anchor = AnchorStyles.Top | AnchorStyles.Right;
            btnCancel.Visible = false;
            btnCancel.Click += delegate { CancelRowEdit(); };
            status.Controls.Add(btnCancel);

            var btnDir = new Button();
            _btnDir = btnDir;
            btnDir.Text = "打开报告目录";
            btnDir.Font = FSmall;
            btnDir.Size = new Size(104, 28);
            btnDir.Location = new Point(ClientSize.Width - 228, 2);
            btnDir.Anchor = AnchorStyles.Top | AnchorStyles.Right;
            btnDir.Click += delegate
            {
                try
                {
                    string d = (_r != null && !string.IsNullOrEmpty(_r.ReportDir)) ? _r.ReportDir : ".";
                    System.Diagnostics.Process.Start("explorer.exe", d);
                }
                catch { }
            };
            status.Controls.Add(btnDir);

            var btnClose = new Button();
            btnClose.Text = "关闭";
            btnClose.Font = FSmall;
            btnClose.Size = new Size(74, 28);
            btnClose.Location = new Point(ClientSize.Width - 118, 2);
            btnClose.Anchor = AnchorStyles.Top | AnchorStyles.Right;
            btnClose.Click += delegate { CloseFromUser(); };
            status.Controls.Add(btnClose);

            _tabs = new TabControl();
            _tabs.Dock = DockStyle.Fill;
            _tabs.Font = F;
            Controls.Add(_tabs);
            _tabs.BringToFront();

            _tabs.TabPages.Add(BuildTolPage());
            _tabs.TabPages.Add(BuildResultPage());
            _tabs.TabPages.Add(BuildSettingsPage());
            _tabs.TabPages.Add(BuildLibPage());
            _tabs.TabPages.Add(BuildAiSettingsPage());
            _tabs.TabPages.Add(BuildHelpPage());

            EnsureTaskbarEntry();
        }

        void RequestCheck()
        {
            if (_busy) return;
            if (_storageBlocked)
            {
                MessageBox.Show(this, "工程图内确认数据校验失败，禁止继续检查或提交 AI。\r\n\r\n" +
                    _storageError + "\r\n\r\n请点「重新读图」明确放弃旧数据后重新识别。", "工程图QC",
                    MessageBoxButtons.OK, MessageBoxIcon.Error);
                return;
            }
            if (_rowEditing)
            {
                MessageBox.Show(this, "正在编辑尺寸行，请先点「完成」或「取消」，再进行检查。", "工程图QC");
                return;
            }
            // 每次检查前把当前三张表同步给引擎（尺寸/公差表是唯一真源）。
            if (!SyncManualToRunner())
            {
                MessageBox.Show(this, "三表一致性校验失败，已停止检查和 AI 提交：\r\n" + _lastSyncError,
                    "工程图QC", MessageBoxButtons.OK, MessageBoxIcon.Error);
                return;
            }
            if (CheckRequested != null) CheckRequested(this, EventArgs.Empty);
        }

        // ---------------------------------------------------------------- 审图结果
        TabPage BuildResultPage()
        {
            var page = new TabPage("审图结果");
            page.BackColor = Color.White;
            page.Padding = new Padding(8);

            _aiLabel = new Label();
            _aiLabel.Dock = DockStyle.Top;
            _aiLabel.Height = 26;
            _aiLabel.Font = FSmall;
            _aiLabel.ForeColor = Muted;
            _aiLabel.TextAlign = ContentAlignment.MiddleLeft;
            _aiLabel.Text = "AI 检查：未执行";
            page.Controls.Add(_aiLabel);

            var cards = new Panel();
            cards.Dock = DockStyle.Top;
            cards.Height = 64;
            cards.BackColor = Color.White;
            page.Controls.Add(cards);
            _cardNums[0] = MakeCard(cards, 0, "严重", "影响制造", Color.FromArgb(192, 57, 43));
            _cardNums[1] = MakeCard(cards, 1, "一般", "不合规范", Color.FromArgb(211, 126, 20));
            _cardNums[2] = MakeCard(cards, 2, "轻微", "优化建议", Color.FromArgb(120, 140, 60));
            _cardNums[3] = MakeCard(cards, 3, "合计", "本次报出", Brand);

            var split = new SplitContainer();
            split.Dock = DockStyle.Fill;
            split.Orientation = Orientation.Vertical;
            split.SplitterWidth = 6;
            split.BackColor = Line;
            // 构造阶段先给小值：此时容器宽度还是默认 150，直接给 420 会抛
            // 「SplitterDistance 必须在 Panel1MinSize 和 Width - Panel2MinSize 之间」
            split.Panel1MinSize = 120;
            split.Panel2MinSize = 120;
            _resSplit = split;
            page.Controls.Add(split);
            split.BringToFront();

            _grid = new DataGridView();
            _grid.Dock = DockStyle.Fill;
            _grid.BackgroundColor = Color.White;
            _grid.BorderStyle = BorderStyle.None;
            _grid.ReadOnly = true;
            _grid.AllowUserToAddRows = false;
            _grid.RowHeadersVisible = false;
            _grid.SelectionMode = DataGridViewSelectionMode.FullRowSelect;
            _grid.MultiSelect = false;
            _grid.AutoSizeColumnsMode = DataGridViewAutoSizeColumnsMode.Fill;
            _grid.EnableHeadersVisualStyles = false;
            _grid.ColumnHeadersDefaultCellStyle.Font = FB;
            _grid.ColumnHeadersDefaultCellStyle.BackColor = Color.FromArgb(242, 245, 249);
            _grid.ColumnHeadersHeight = 26;
            _grid.RowTemplate.Height = 24;
            _grid.Columns.Add(Col("等级", 8));
            _grid.Columns.Add(Col("内容", 46));
            _grid.Columns.Add(Col("审核结果", 46));
            _grid.SelectionChanged += delegate { ShowDetail(); };
            split.Panel1.Controls.Add(_grid);

            var right = new Panel();
            right.Dock = DockStyle.Fill;
            right.BackColor = Color.White;
            split.Panel2.Controls.Add(right);

            _detail = new QcDetailView();
            _detail.Dock = DockStyle.Fill;
            right.Controls.Add(_detail);

            split.SizeChanged += delegate
            {
                SetSplitSafe(split, 0.62, 420, 260);
            };

            return page;
        }

        static DataGridViewTextBoxColumn Col(string name, int fillWeight)
        {
            var c = new DataGridViewTextBoxColumn();
            c.HeaderText = name;
            c.Name = "col_" + name;
            c.FillWeight = fillWeight;
            c.SortMode = DataGridViewColumnSortMode.NotSortable;
            return c;
        }

        static Label MakeCard(Control parent, int idx, string title, string sub, Color color)
        {
            int x = 8 + idx * 132;
            var p = new Panel();
            p.Location = new Point(x, 4);
            p.Size = new Size(124, 56);
            p.BackColor = Color.FromArgb(250, 251, 253);
            p.BorderStyle = BorderStyle.FixedSingle;
            parent.Controls.Add(p);
            var num = new Label();
            num.AutoSize = false;
            num.Text = "0";
            num.Font = new Font("微软雅黑", 16f, FontStyle.Bold);
            num.ForeColor = color;
            num.TextAlign = ContentAlignment.MiddleCenter;
            num.Size = new Size(40, 54);
            num.Location = new Point(0, 0);
            p.Controls.Add(num);
            var t = new Label();
            t.AutoSize = false;
            t.Text = title;
            t.Font = FB;
            t.ForeColor = Ink;
            t.Size = new Size(78, 20);
            t.Location = new Point(42, 8);
            p.Controls.Add(t);
            var s = new Label();
            s.AutoSize = false;
            s.Text = sub;
            s.Font = FSmall;
            s.ForeColor = Muted;
            s.Size = new Size(78, 18);
            s.Location = new Point(42, 30);
            p.Controls.Add(s);
            return num;
        }

        // ---------------------------------------------------------------- 尺寸/公差
        TabPage BuildTolPage()
        {
            var page = new TabPage("尺寸/公差");
            page.BackColor = Color.White;
            page.Padding = new Padding(8);

            var split = new SplitContainer();
            split.Dock = DockStyle.Fill;
            split.Orientation = Orientation.Horizontal;
            split.SplitterWidth = 6;
            split.BackColor = Line;
            split.Panel1MinSize = 60;
            split.Panel2MinSize = 60;
            page.Controls.Add(split);

            var top = new Panel();
            top.Dock = DockStyle.Fill;
            top.BackColor = Color.White;
            split.Panel1.Controls.Add(top);

            // 顶部一行：左边说明（未关联时高亮），右边「指定归属」按钮
            var tipRow = new Panel();
            tipRow.Dock = DockStyle.Top;
            tipRow.Height = 54;
            top.Controls.Add(tipRow);

            var linkBtn = new Button();
            _btnLink = linkBtn;
            linkBtn.Text = "指定归属";
            linkBtn.Font = FSmall;
            linkBtn.Size = new Size(104, 28);
            linkBtn.Dock = DockStyle.Right;
            linkBtn.Enabled = false;
            linkBtn.Click += delegate
            {
                try
                {
                    bool anyGtol = false;
                    if (_r.UnlinkedGtols != null && _r.UnlinkedGtols.Count > 0) anyGtol = true;
                    if (!anyGtol && _r.DimRows != null)
                        foreach (QcDimRow rr in _r.DimRows)
                            if (rr != null && ((rr.Gtols != null && rr.Gtols.Count > 0)
                                               || !string.IsNullOrEmpty(rr.GtolSym))) { anyGtol = true; break; }
                    if (!anyGtol) { MessageBox.Show(this, "本图没有标注几何公差。", "指定归属"); return; }
                    EnsureAllGtols();
                using (var dlg = new QcGtolLinkForm(_r, _allGtols))
                    {
                        _childDlg = dlg;
                        try
                        {
                            if (dlg.ShowDialog(this) == DialogResult.OK)
                                ApplyLinkDialog(dlg);
                        }
                        finally { _childDlg = null; }
                    }
                }
                catch (Exception ex) { MessageBox.Show(this, "指定归属失败：" + ex.Message, "工程图QC"); }
            };
            var datumBtn = new Button();
            datumBtn.Text = "基准设置";
            datumBtn.Font = FSmall;
            datumBtn.Size = new Size(96, 28);
            datumBtn.Dock = DockStyle.Right;
            datumBtn.Click += delegate
            {
                try
                {
                    using (var dlg = new QcDatumForm(_r.DwgNo, _datums, (_r.DimRows ?? new List<QcDimRow>()).ConvertAll(x => x.Content)))
                    {
                        _childDlg = dlg;
                        try
                        {
                            if (dlg.ShowDialog(this) == DialogResult.OK)
                            {
                                try
                                {
                                    if (_doc != null)
                                    {
                                        _doc.ApplyDatumRows(_datums);
                                        _doc.ApplyToReport(_r);
                                        QcRunner.DocumentSnapshotJson = _doc.ToSnapshotJson();
                                    }
                                    ParseDatums();
                                    RefreshDatumNotesLocally();
                                    BindTolerance();
                                    EnsureUnlinkedBanner();
                                    ShowDetail();
                                    if (_statusTip != null) _statusTip.Text = "基准表已保存，相关尺寸表/归属表已同步（点「检查」更新审核结果）";
                                }
                                catch { }
                            }
                        }
                        finally { _childDlg = null; }
                    }
                }
                catch (Exception ex) { MessageBox.Show(this, "基准设置失败：" + ex.Message, "工程图QC"); }
            };
            var tip = new Label();
            tip.Dock = DockStyle.Fill;
            _tolTip = tip;
            tip.Font = FSmall;
            tip.ForeColor = Ink;
            tip.Text = "表格默认只读：可复制、勾选「计算」；要改数据请右键 →「编辑」，改完点「完成」保存。" +
                       "轮廓包络不再单列，命中尺寸会在备注写 轮廓-X=42；漏标才提示。" +
                       "勾选左侧「计算」方框，下方即生成该尺寸的公差计算。\r\n" +
                       "「≈ITx」表示公差值不在标准 IT 表上，按最接近的等级折算；依据 GB/T 1800.1、GB/T 1184。";
            _tolTipDefault = tip.Text;
            tipRow.Controls.Add(tip);
            tipRow.Controls.Add(linkBtn);
            tipRow.Controls.Add(datumBtn);

            _tolGrid = new DataGridView();
            _tolGrid.Dock = DockStyle.Fill;
            _tolGrid.BackgroundColor = Color.White;
            _tolGrid.BorderStyle = BorderStyle.None;
            // 默认只读：数据列只读、勾选列可点（列级 ReadOnly，见 TolCol / chk）
            _tolGrid.ReadOnly = false;
            _tolGrid.AllowUserToAddRows = false;
            _tolGrid.RowHeadersVisible = false;
            _tolGrid.SelectionMode = DataGridViewSelectionMode.FullRowSelect;
            _tolGrid.MultiSelect = false;
            _tolGrid.EditMode = DataGridViewEditMode.EditOnEnter;
            _tolGrid.AutoSizeColumnsMode = DataGridViewAutoSizeColumnsMode.None;
            _tolGrid.EnableHeadersVisualStyles = false;
            _tolGrid.ColumnHeadersDefaultCellStyle.Font = FB;
            _tolGrid.ColumnHeadersDefaultCellStyle.BackColor = Color.FromArgb(242, 245, 249);
            _tolGrid.ColumnHeadersHeight = 30;
            _tolGrid.RowTemplate.Height = 30;
            top.Controls.Add(_tolGrid);
            _tolGrid.BringToFront();

            var chk = new DataGridViewCheckBoxColumn();
            chk.HeaderText = "计算";
            chk.Name = "colCalc";
            chk.AutoSizeMode = DataGridViewAutoSizeColumnMode.None;
            chk.Width = 40;
            chk.MinimumWidth = 40;
            chk.SortMode = DataGridViewColumnSortMode.NotSortable;
            chk.ReadOnly = false;              // 勾选列保持可用
            _tolGrid.Columns.Add(chk);
            TolColFixed("类别", 60);
            TolColFixed("尺寸特征", 90);
            TolColFixed("公差 上/下", 110);
            // 2026-10-26 用户要求：几何公差排在「尺寸公差」后面
            TolColFixed("几何公差", 400);
            TolColFixed("IT 等级", 50);
            TolColFixed("加工信息", 280);
            // 2026-10-26 用户要求：备注列加宽，但**不能压缩其他列**
            TolColFixed("备注", 400);
            // 给关键列设最小宽度，保证加宽备注时它们不被压扁/截断
            try
            {
                _tolGrid.Columns["col_公差 上/下"].MinimumWidth = 110;
                _tolGrid.Columns["col_几何公差"].MinimumWidth = 190;
                _tolGrid.Columns["col_加工信息"].MinimumWidth = 220;
            }
            catch { }

            _tolGrid.CellValueChanged += delegate(object o, DataGridViewCellEventArgs e)
            {
                if (e.RowIndex >= 0 && e.ColumnIndex == 0) RefreshCalc();
            };
            _tolGrid.CurrentCellDirtyStateChanged += delegate
            {
                if (_tolGrid.IsCurrentCellDirty)
                    _tolGrid.CommitEdit(DataGridViewDataErrorContexts.Commit);
            };
            _tolGrid.DefaultCellStyle.WrapMode = DataGridViewTriState.False;
            _tolGrid.CellFormatting += delegate(object o, DataGridViewCellFormattingEventArgs e)
            {
                try
                {
                    if (e.RowIndex < 0 || e.ColumnIndex < 0) return;
                    if (_tolGrid.Columns[e.ColumnIndex].Name != "col_几何公差") return;
                    string txt = Convert.ToString(e.Value) ?? "";
                    e.CellStyle.Font = new Font("Microsoft YaHei", txt.Length > 40 ? 8.0F : 9.0F);
                }
                catch { }
            };
            _tolGrid.CellEndEdit += delegate(object o, DataGridViewCellEventArgs e)
            {
                if (e.RowIndex < 0) return;
                DataGridViewRow gr = _tolGrid.Rows[e.RowIndex];
                var r = gr.Tag as QcDimRow;
                if (r == null) return;
                string val = Convert.ToString(gr.Cells[e.ColumnIndex].Value) ?? "";
                ApplyCellToRow(r, e.ColumnIndex, val);
                if (r.HasTol && r.Band > 1e-9)
                {
                    gr.Cells[0].ReadOnly = false;
                    gr.Cells[0].Style.BackColor = Color.White;
                }
                RefreshCalc();
                // 2026-10-29：编辑中的值不自动写入快照；点「完成」才保存（见 CompleteRowEdit）
            };

            _calc = new CalcPanel();
            _calc.Dock = DockStyle.Fill;
            split.Panel2.Controls.Add(_calc);

            split.SizeChanged += delegate
            {
                SetSplitSafe(split, 0.46, 80, 120);
            };

            return page;
        }

        void TolColFixed(string name, int width)
        {
            var c = new DataGridViewTextBoxColumn();
            c.HeaderText = name;
            c.Name = "col_" + name;
            c.AutoSizeMode = DataGridViewAutoSizeColumnMode.None;
            c.Width = width;
            c.MinimumWidth = 60;
            c.ReadOnly = true;                 // 数据列默认只读
            c.SortMode = DataGridViewColumnSortMode.NotSortable;
            _tolGrid.Columns.Add(c);
        }

        void TolCol(string name, int weight)
        {
            var c = new DataGridViewTextBoxColumn();
            c.HeaderText = name;
            c.Name = "col_" + name;
            c.FillWeight = weight;
            c.MinimumWidth = 80;
            c.ReadOnly = true;                 // 数据列默认只读
            c.SortMode = DataGridViewColumnSortMode.NotSortable;
            _tolGrid.Columns.Add(c);
        }

        ComboBox _cboCategory;          // 工艺类别下拉（按图号记忆）

        // ---------------------------------------------------------------- 设置/标准库/帮助
        TabPage BuildSettingsPage()
        {
            var page = new TabPage("检查设置");
            page.BackColor = Color.White;
            page.Padding = new Padding(12);

            var gb = new GroupBox();
            gb.Text = "判定标准";
            gb.Font = F;
            gb.Location = new Point(12, 10);
            gb.Size = new Size(560, 130);
            page.Controls.Add(gb);

            var lstd = new Label();
            lstd.Text = "标准体系：";
            lstd.Font = F;
            lstd.AutoSize = true;
            lstd.Location = new Point(14, 28);
            gb.Controls.Add(lstd);

            var cbo = new ComboBox();
            cbo.DropDownStyle = ComboBoxStyle.DropDownList;
            cbo.Font = F;
            cbo.Items.AddRange(new object[] { "GB/T（默认）", "ISO", "ASME Y14.5" });
            cbo.Location = new Point(100, 25);
            cbo.Width = 180;
            try
            {
                string std = QcRunner.LoadConfig().Standard;
                cbo.SelectedIndex = std == "ASME" ? 2 : (std == "ISO" ? 1 : 0);
            }
            catch { }
            if (cbo.SelectedIndex < 0) cbo.SelectedIndex = 0;
            gb.Controls.Add(cbo);

            var lstd2 = new Label();
            lstd2.Text = "GB/T 与 ISO：独立原则；ASME Y14.5：包容原则（影响作用边界计算）";
            lstd2.Font = FSmall;
            lstd2.ForeColor = Muted;
            lstd2.AutoSize = false;
            lstd2.Size = new Size(520, 20);
            lstd2.Location = new Point(14, 56);
            gb.Controls.Add(lstd2);

            var save = new Button();
            save.Text = "保存标准体系";
            save.Font = F;
            save.Size = new Size(120, 26);
            save.Location = new Point(100, 84);
            save.Click += delegate
            {
                try
                {
                    string std = cbo.SelectedIndex == 2 ? "ASME" : (cbo.SelectedIndex == 1 ? "ISO" : "GB");
                    QcRunner.SaveSetting("standard", std);
                    CalcPanel.PrincipleDefault = (std == "ASME") ? "包容原则" : "独立原则";
                    MessageBox.Show("已保存：标准体系 = " + std, "检查设置");
                }
                catch (Exception ex) { MessageBox.Show("保存失败：" + ex.Message); }
            };
            gb.Controls.Add(save);

            var gbc = new GroupBox();
            gbc.Text = "工艺类别（按图号记忆，人工指定优先于 AI）";
            gbc.Font = F;
            gbc.Location = new Point(12, 150);
            gbc.Size = new Size(560, 112);
            page.Controls.Add(gbc);

            var lcat = new Label();
            lcat.Text = "零件类别：";
            lcat.Font = F;
            lcat.AutoSize = true;
            lcat.Location = new Point(14, 30);
            gbc.Controls.Add(lcat);

            _cboCategory = new ComboBox();
            _cboCategory.DropDownStyle = ComboBoxStyle.DropDownList;
            _cboCategory.Font = F;
            _cboCategory.Items.AddRange(new object[] {
                "自动（AI 判定）", "钣金件", "机加件", "注塑件", "塑料机加件", "未知" });
            _cboCategory.Location = new Point(100, 27);
            _cboCategory.Width = 200;
            _cboCategory.SelectedIndex = 0;
            _cboCategory.SelectedIndexChanged += delegate
            {
                try
                {
                    string cat = _cboCategory.SelectedIndex <= 0 ? "" : _cboCategory.Text;
                    QcRunner.ForcedCategory = cat;
                    string dwg = HeaderVal("图号");
                    if (dwg != "—") QcRunner.SaveCategoryFor(dwg, cat);
                    if (!string.IsNullOrEmpty(cat))
                        MessageBox.Show("已记录：图号 " + dwg + " → " + cat + "（下次审该图沿用）", "工艺类别");
                }
                catch { }
            };
            gbc.Controls.Add(_cboCategory);

            var lcat2 = new Label();
            lcat2.Text = "默认「自动」：由 AI + 图面推断；人工选过就按图号记住，优先于 AI。";
            lcat2.Font = FSmall;
            lcat2.ForeColor = Muted;
            lcat2.AutoSize = false;
            lcat2.Size = new Size(520, 40);
            lcat2.Location = new Point(14, 60);
            gbc.Controls.Add(lcat2);

            var gb2 = new GroupBox();
            gb2.Text = "窗口";
            gb2.Font = F;
            gb2.Location = new Point(12, 275);
            gb2.Size = new Size(560, 90);
            page.Controls.Add(gb2);

            var chkTop = new CheckBox();
            chkTop.Text = "结果窗口置顶（覆盖在 SolidWorks 之上）";
            chkTop.Font = F;
            chkTop.AutoSize = true;
            chkTop.Location = new Point(14, 28);
            try { chkTop.Checked = QcRunner.LoadConfig().TopMost == "1"; } catch { }
            chkTop.CheckedChanged += delegate
            {
                try { QcRunner.SaveSetting("qc_topmost", chkTop.Checked ? "1" : "0"); TopMost = chkTop.Checked; }
                catch { }
            };
            gb2.Controls.Add(chkTop);

            return page;
        }

        /// <summary>找最新的《规格库核对表_*.xlsx》：程序目录 → LOCALAPPDATA → D:\SwQcAddin → 交付包运行目录。</summary>
        static string FindSpecWorkbook()
        {
            var dirs = new List<string>();
            try
            {
                string asmDir = System.IO.Path.GetDirectoryName(
                    System.Reflection.Assembly.GetExecutingAssembly().Location);
                if (!string.IsNullOrEmpty(asmDir))
                {
                    dirs.Add(asmDir);
                    dirs.Add(System.IO.Path.Combine(asmDir, "..", "运行目录_SwQcAddin"));
                }
            }
            catch { }
            dirs.Add(System.IO.Path.Combine(
                Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "SwQcAddin"));
            dirs.Add(@"D:\SwQcAddin");
            dirs.Add(@"D:\工程图QC_交付包_20261026\运行目录_SwQcAddin");
            dirs.Add(Environment.CurrentDirectory);

            string best = null;
            DateTime bestTime = DateTime.MinValue;
            foreach (string d in dirs)
            {
                try
                {
                    if (string.IsNullOrEmpty(d) || !System.IO.Directory.Exists(d)) continue;
                    foreach (string f in System.IO.Directory.GetFiles(d, "规格库核对表*.xlsx"))
                    {
                        DateTime t = System.IO.File.GetLastWriteTime(f);
                        if (t > bestTime) { bestTime = t; best = f; }
                    }
                }
                catch { }
            }
            return best;
        }

        TabPage BuildLibPage()
        {
            var page = new TabPage("标准库");
            page.BackColor = Color.White;
            page.Padding = new Padding(12);

            var lb = new Label();
            lb.AutoSize = false;
            lb.Font = F;
            lb.ForeColor = Ink;
            lb.Size = new Size(860, 90);
            lb.Location = new Point(14, 14);
            lb.Text = "标准数值来自《规格库核对表》（IT 公差、基本偏差、几何公差、粗糙度、螺纹、挡圈……）。\r\n" +
                      "审核用的判定阈值与口径都写在这些表里，改表即可调整规则（不用改程序）。";
            page.Controls.Add(lb);

            var btn = new Button();
            btn.Text = "打开规格库核对表";
            btn.Font = F;
            btn.Size = new Size(170, 28);
            btn.Location = new Point(14, 110);
            btn.Click += delegate
            {
                try
                {
                    string p = FindSpecWorkbook();
                    if (p != null) System.Diagnostics.Process.Start(p);
                    else
                        MessageBox.Show(
                            "找不到规格库文件（规格库核对表_*.xlsx）。\r\n" +
                            "已查找：程序目录、%LOCALAPPDATA%\\SwQcAddin、D:\\SwQcAddin、交付包运行目录。\r\n" +
                            "请把《规格库核对表》放到上述任一目录。", "标准库");
                }
                catch (Exception ex) { MessageBox.Show("打开失败：" + ex.Message); }
            };
            page.Controls.Add(btn);
            return page;
        }

        /// <summary>
        /// 通用 AI 语义审核接口设置。直接内嵌在审图主窗口，避免与 PDF 视觉通道的
        /// 专用设置混淆；两套配置仍分别使用 ai_* 与 pdf_ai_* 键。
        /// </summary>
        TabPage BuildAiSettingsPage()
        {
            var page = new TabPage("AI接口设置");
            page.BackColor = Color.White;
            page.Padding = new Padding(0);
            try
            {
                var settings = new QcAiSettingsForm(true);
                settings.TopLevel = false;
                settings.FormBorderStyle = FormBorderStyle.None;
                settings.Dock = DockStyle.Fill;
                settings.ShowInTaskbar = false;
                page.Controls.Add(settings);
                settings.Show();
            }
            catch (Exception ex)
            {
                var error = new Label();
                error.Dock = DockStyle.Fill;
                error.TextAlign = ContentAlignment.MiddleCenter;
                error.Font = F;
                error.ForeColor = Color.DarkRed;
                error.Text = "AI接口设置加载失败：" + ex.Message;
                page.Controls.Add(error);
            }
            return page;
        }

        /// <summary>供旧版残留命令回调使用：只切换主窗口页签，不再弹独立设置窗。</summary>
        public void ShowAiSettingsTab()
        {
            if (_tabs == null || _tabs.IsDisposed) return;
            foreach (TabPage page in _tabs.TabPages)
            {
                if (page.Text == "AI接口设置")
                {
                    _tabs.SelectedTab = page;
                    break;
                }
            }
            if (!Visible) Show();
            WindowState = FormWindowState.Normal;
            BringToFront();
            Activate();
        }

        TabPage BuildHelpPage()
        {
            var page = new TabPage("帮助");
            page.BackColor = Color.White;
            page.Padding = new Padding(12);
            var tb = new RichTextBox();
            tb.Dock = DockStyle.Fill;
            tb.BorderStyle = BorderStyle.None;
            tb.Font = F;
            tb.ReadOnly = true;
            tb.Text =
                "【审图】读当前工程图（或同名工程图）→ 本地规则判定 + AI 文字审查。\r\n" +
                "【审图（PDF/AI）】把工程图导 PDF（三级兜底）→ 文字 + 图像 → AI 视觉审图。\r\n\r\n" +
                "【尺寸/公差】页：\r\n" +
                "  · 列表包含：轮廓尺寸、带尺寸公差、带几何公差、粗糙度；\r\n" +
                "  · 所有内容都可以直接双击修改，改完立刻重算最大作用边界；\r\n" +
                "  · 几何公差列写法：—0.01；⌭0.03；⊥0.05 A\r\n" +
                "  · 只有「找不到关联尺寸」的几何公差才出现黄色提醒条 + 指定归属。\r\n\r\n" +
                "最大作用边界：\r\n" +
                "  轴 = 最大实体尺寸 + Σt；孔 = 最大实体尺寸 − Σt\r\n" +
                "  计入：形状类（—○⌭）与位置类（◎⌖↗⌰）；不计：端面方向类（▱⊥∥∠）、轮廓度、对称度；\r\n" +
                "  涵盖关系只算父级（圆柱度⊃圆度/直线度；全跳动⊃圆跳动/圆柱度/同轴度/圆度；位置度⊃同轴度/对称度）；\r\n" +
                "  尺寸带 Ⓔ 时形状类不叠加；同一项目多值取严。\r\n\r\n" +
                "报告目录：%TEMP%\\SwQcAddin\\out";
            page.Controls.Add(tb);
            return page;
        }

        // ================================================================ 绑定数据
        void BindRows()
        {
            if (_grid == null) return;
            _grid.Rows.Clear();
            int crit = 0, major = 0, minor = 0;
            if (_r.Items != null)
                foreach (QcItem it in _r.Items)
                {
                    if (it.Severity == "critical") crit++;
                    else if (it.Severity == "major") major++;
                    else if (it.Severity == "minor") minor++;   // info 不计入问题统计
                }

            // <datum-rows> 基准分组（人工可指定）
            if (_datums != null && _datums.Count > 0)
            {
                int gi = _grid.Rows.Add("", "【基准】", "");
                _grid.Rows[gi].DefaultCellStyle.BackColor = Color.FromArgb(238, 243, 249);
                _grid.Rows[gi].DefaultCellStyle.Font = FB;
                foreach (DatumRow dr in _datums)
                {
                    string tgt = string.IsNullOrEmpty(dr.Manual) ? dr.Auto : (dr.Manual + "（人工指定）");
                    if (string.IsNullOrEmpty(tgt)) tgt = "未判断";
                    int ri = _grid.Rows.Add("—", "基准 " + dr.Letter + (string.IsNullOrEmpty(dr.View) ? "" : "（" + dr.View + "）"),
                                            "指向：" + tgt + "　→ 点「基准设置」可改");
                    _grid.Rows[ri].Tag = dr;
                }
            }
            foreach (string cat in new string[] { "图纸信息", "尺寸", "尺寸/几何公差", "技术要求" })
            {
                int hidx = _grid.Rows.Add("", "【" + cat + "】", "");
                _grid.Rows[hidx].DefaultCellStyle.BackColor = Color.FromArgb(232, 238, 246);
                _grid.Rows[hidx].DefaultCellStyle.Font = FB;
                _grid.Rows[hidx].Tag = null;

                if (_r.Items != null)
                    foreach (QcItem it in _r.Items)
                    {
                        string c = string.IsNullOrEmpty(it.Category) ? Classify(it) : it.Category;
                        if (cat == "尺寸/几何公差" && c == "几何公差") c = "尺寸/几何公差";
                        if (c != cat) continue;
                        string _title = it.Title ?? "";
                        if (it.Category == "尺寸/几何公差" || it.Category == "几何公差")
                            _title = _title.Replace("尺寸/几何公差 ", "").Replace("几何公差 ", "");
                        int i = _grid.Rows.Add(it.SeverityText, AiContentCell(it, _title), AiResultCell(it));
                        _grid.Rows[i].Tag = it;
                        _grid.Rows[i].Cells[1].ToolTipText = it.Title ?? "";
                        _grid.Rows[i].Cells[2].ToolTipText = it.Suggestion ?? "";
                        if (it.Severity == "critical") _grid.Rows[i].Cells[0].Style.ForeColor = Color.FromArgb(192, 57, 43);
                        else if (it.Severity == "major") _grid.Rows[i].Cells[0].Style.ForeColor = Color.FromArgb(211, 126, 20);
                        else _grid.Rows[i].Cells[0].Style.ForeColor = Color.FromArgb(120, 140, 60);
                    }

                if (_r.Passed != null)
                    foreach (QcPassed p in _r.Passed)
                    {
                        string c = string.IsNullOrEmpty(p.Category) ? ClassifyPassed(p.Text) : p.Category;
                        if (cat == "尺寸/几何公差" && c == "几何公差") c = "尺寸/几何公差";
                        if (c != cat) continue;
                        int i = _grid.Rows.Add("通过", "✓ " + p.Text, "—");
                        _grid.Rows[i].Cells[0].Style.ForeColor = Color.FromArgb(22, 122, 60);
                        _grid.Rows[i].Tag = p;                       // 2026-10-26：选中后右侧显示完整内容
                        _grid.Rows[i].Cells[1].ToolTipText = p.Text ?? "";
                    }
            }

            if (_cardNums[0] != null) _cardNums[0].Text = crit.ToString();
            if (_cardNums[1] != null) _cardNums[1].Text = major.ToString();
            if (_cardNums[2] != null) _cardNums[2].Text = minor.ToString();
            if (_cardNums[3] != null) _cardNums[3].Text = (crit + major + minor).ToString();
        }

        /// <summary>内容列：尺寸/几何公差只留数值；技术要求只留「第N条 条款」。</summary>
        static string AiContentCell(QcItem it, string raw)
        {
            string t = raw ?? "";
            if (it == null) return t;
            if (it.RuleId == "AI-GEOM-ITEM")
            {
                t = t.Replace("尺寸/几何公差 ", "").Replace("几何公差 ", "");
                int p = t.IndexOf("：");
                if (p > 0) t = t.Substring(0, p);
                t = System.Text.RegularExpressions.Regex.Replace(t, @"\s*[（(]\s*≈?IT\d+\s*[)）]", "").Trim();
            }
            else if (it.RuleId == "AI-TECH-ITEM")
            {
                t = t.Replace("技术要求第", "第");
            }
            return t;
        }

        /// <summary>审核结果列：合并 IT等级 + 审核结论 + 建议（用户要求的 3 列表格）。</summary>
        static string AiResultCell(QcItem it)
        {
            if (it == null) return "";
            if (it.RuleId != "AI-GEOM-ITEM" && it.RuleId != "AI-TECH-ITEM")
                return it.Suggestion ?? "";
            var sb = new System.Text.StringBuilder();
            string ttl = it.Title ?? "";
            string brief = "";
            int p = ttl.IndexOf("：");
            if (p > 0) { brief = ttl.Substring(p + 1); ttl = ttl.Substring(0, p); }
            if (it.RuleId == "AI-GEOM-ITEM")
            {
                string itg = "";
                try
                {
                    var mm = System.Text.RegularExpressions.Regex.Match(ttl, @"[（(]\s*(≈?IT\d+)\s*[)）]");
                    if (mm.Success) itg = mm.Groups[1].Value;
                    else
                    {
                        var mm2 = System.Text.RegularExpressions.Regex.Match(brief, @"(≈?IT\d+)");
                        if (mm2.Success) itg = mm2.Groups[1].Value;
                    }
                }
                catch { }
                if (!string.IsNullOrEmpty(itg)) sb.Append("IT等级 " + itg + "；");
            }
            string body = CleanText(it.Detail);
            if (!string.IsNullOrEmpty(body)) sb.Append(body);
            string sg = CleanText(it.Suggestion);
            if (!string.IsNullOrEmpty(sg)) sb.Append((sb.Length > 0 ? "。建议：" : "建议：") + sg);
            return sb.ToString();
        }

        static string Classify(QcItem it)
        {
            string id = (it.RuleId ?? "").ToUpper();
            string t = it.Title ?? "";
            if (id.StartsWith("MAT") || id.StartsWith("GB-MAT") || id.StartsWith("INFO") ||
                t.Contains("图框") || t.Contains("材料") || t.Contains("版本") || t.Contains("比例"))
                return "图纸信息";
            if (id.StartsWith("GTOL") || id.StartsWith("TOL") || id.StartsWith("DIM") ||
                id.StartsWith("IT") || id.StartsWith("ROUGH") || id.StartsWith("SURFACE") ||
                id.StartsWith("AI-") || t.Contains("几何") || t.Contains("粗糙度") ||
                t.Contains("技术要求") || t.Contains("表面处理"))
                return "技术要求";
            return "尺寸";
        }

        static string ClassifyPassed(string text)
        {
            // 与 QcRunner.ClassifyPassedText 保持同一口径
            string t = text ?? "";
            if (t.Contains("标题栏") || t.Contains("图框") || t.Contains("材料") ||
                t.Contains("图号") || t.Contains("版本") || t.Contains("比例") ||
                t.Contains("多图纸"))
                return "图纸信息";
            if (t.Contains("技术要求") || t.Contains("表面处理") || t.Contains("AI") ||
                t.Contains("多图纸已"))
                return "技术要求";
            return "尺寸";   // 尺寸/公差/几何公差/粗糙度类
        }

        /// <summary>公差列显示：尺寸没有独立公差时统一显示“无”。</summary>
        static string DispTol(QcDimRow r)
        {
            string k = (r == null) ? "" : (r.Kind ?? "");
            string t = (r == null) ? "" : (r.TolDisplay ?? "");
            if (k.Contains("粗糙度")) return "—";
            t = t.Trim();
            if (t.Length == 0 || t == "无" || t == "未注" || t == "基本尺寸" || t == "—" ||
                t == "上 0 下 0" || t == "上 +0 下 -0" || t == "±0") return "无";
            return t;
        }

        void BindTolerance()
        {
            if (_tolGrid == null) return;
            if (_tolGrid.ContextMenuStrip == null)
            {
                var menu = new ContextMenuStrip();
                var miDel = new ToolStripMenuItem("删除此行");
                miDel.Click += delegate
                {
                    try
                    {
                        if (_tolGrid.CurrentRow == null) return;
                        QcDimRow rr = _tolGrid.CurrentRow.Tag as QcDimRow;
                        if (rr == null) return;
                        DeleteTolRow(rr);
                    }
                    catch (Exception ex) { MessageBox.Show(this, "删除失败：" + ex.Message, "工程图QC"); }
                };
                var miAdd = new ToolStripMenuItem("新增一行");
                miAdd.Click += delegate { AddManualRow(); };
                var miEdit = new ToolStripMenuItem("编辑");
                miEdit.Click += delegate
                {
                    try
                    {
                        if (_tolGrid.CurrentRow == null) return;
                        QcDimRow rr = _tolGrid.CurrentRow.Tag as QcDimRow;
                        if (rr == null) return;
                        EditManualRow(rr);
                    }
                    catch { }
                };
                var miCopy = new ToolStripMenuItem("复制本行");
                miCopy.Click += delegate { CopyCurrentRow(); };
                var miRmDup = new ToolStripMenuItem("合并本行重复几何公差…");
                miRmDup.Click += delegate { MergeDuplicateGtol(); };
                menu.Items.Add(miDel);
                menu.Items.Add(new ToolStripSeparator());
                menu.Items.Add(miAdd);
                menu.Items.Add(miEdit);
                menu.Items.Add(miCopy);
                menu.Items.Add(miRmDup);
                menu.Items.Add(new ToolStripSeparator());
                menu.Items.Add(new ToolStripMenuItem("重读图纸 (Ctrl+R)", null,
                    delegate { RereadDrawing(); }));
                _tolGrid.ContextMenuStrip = menu;
            }
            _tolGrid.Rows.Clear();
            if (_r.DimRows == null || _r.DimRows.Count == 0)
            {
                // 2026-10-26 审计修复：列数由 7 → 8（新增"几何公差"列），这里必须补齐参数，否则列错位
                int ph = _tolGrid.Rows.Add(false, "—", "本图没有标注公差的尺寸 / 几何公差",
                    "—", "", "—", "按 GB/T 1804 一般公差，常规加工可满足", "只列出「有公差」的项");
                _tolGrid.Rows[ph].Cells[0].ReadOnly = true;
                _tolGrid.Rows[ph].Cells[0].Value = false;
                if (_calc != null) _calc.SetRows(null);
                return;
            }
            foreach (QcDimRow r in _r.DimRows)
            {
                int i = _tolGrid.Rows.Add(false, ShortKind(r.Kind), r.Content,
                    DispTol(r), GtolText(r), r.IT, r.Capability, r.Note);
                if (!CanCalc(r))
                {
                    _tolGrid.Rows[i].Cells[0].ReadOnly = true;
                    _tolGrid.Rows[i].Cells[0].Style.BackColor = Color.FromArgb(245, 246, 248);
                }
                _tolGrid.Rows[i].Tag = r;
                // 2026-10-26：悬停显示完整备注/几何公差，避免截断丢信息
                _tolGrid.Rows[i].Cells[7].ToolTipText = r.Note ?? "";
                if (!string.IsNullOrEmpty(GtolText(r)))
                    _tolGrid.Rows[i].Cells[4].ToolTipText = GtolText(r);
                string it2 = r.IT ?? "";
                if (it2.Replace("≈", "").StartsWith("IT"))
                {
                    int g;
                    if (int.TryParse(it2.Replace("≈", "").Substring(2), out g))
                    {
                        if (g <= 6) _tolGrid.Rows[i].DefaultCellStyle.BackColor = Color.FromArgb(253, 242, 242);
                        else if (g <= 7) _tolGrid.Rows[i].DefaultCellStyle.BackColor = Color.FromArgb(255, 249, 240);
                    }
                }
            }
            ApplyGridReadOnly();
            RefreshCalc();
        }

        // ------------------------------------------------------------ 只读/行编辑
        void ApplyGridReadOnly()
        {
            if (_tolGrid == null || _tolGrid.Columns.Count == 0) return;
            for (int ci = 0; ci < _tolGrid.Columns.Count; ci++)
                _tolGrid.Columns[ci].ReadOnly = (ci != 0);   // 只放开“计算”列
        }

        void BeginRowEdit(QcDimRow r)
        {
            if (_busy || r == null || _tolGrid == null) return;
            if (_rowEditing)
            {
                MessageBox.Show(this, "正在编辑一行，请先点「完成」或「取消」。", "工程图QC");
                return;
            }
            _rowEditing = true;
            _editRow = r;
            _editBackup = CloneRow(r);
            int idx = -1;
            for (int i = 0; i < _tolGrid.Rows.Count; i++)
                if (object.ReferenceEquals(_tolGrid.Rows[i].Tag, r)) { idx = i; break; }
            ApplyGridReadOnly();
            if (idx >= 0 && _tolGrid.Rows[idx].Cells.Count > 2)
            {
                _tolGrid.CurrentCell = _tolGrid.Rows[idx].Cells[2];
                _tolGrid.Rows[idx].Selected = true;
                try { _tolGrid.BeginEdit(true); } catch { }
            }
            if (_btnEditDone != null) _btnEditDone.Visible = true;
            if (_btnEditCancel != null) _btnEditCancel.Visible = true;
            if (_statusTip != null) _statusTip.Visible = false;
            if (_tolTip != null) _tolTip.Text = "编辑模式：修改本行后点「完成」保存（不会自动重跑 AI；需要更新审核结果请点「检查」）。";
        }

        void CompleteRowEdit()
        {
            if (!_rowEditing || _editRow == null) return;
            try
            {
                try { _tolGrid.EndEdit(); } catch { }
                DataGridViewRow gr = null;
                foreach (DataGridViewRow x in _tolGrid.Rows)
                    if (object.ReferenceEquals(x.Tag, _editRow)) { gr = x; break; }
                if (gr != null)
                    for (int ci = 1; ci < _tolGrid.Columns.Count; ci++)
                        ApplyCellToRow(_editRow, ci, Convert.ToString(gr.Cells[ci].Value) ?? "");
                // 几何公差不合法时不允许保存
                string gtxt = "";
                if (gr != null && gr.Cells.Count > 4)
                    gtxt = Convert.ToString(gr.Cells[4].Value) ?? "";
                if (!string.IsNullOrEmpty(gtxt.Trim()) && (_editRow.Gtols == null || _editRow.Gtols.Count == 0))
                {
                    MessageBox.Show(this, "几何公差格式无法识别，请检查后再点「完成」。", "工程图QC");
                    return;
                }
                _rowEditing = false;
                _editRow = null;
                _editBackup = null;
                if (_btnEditDone != null) _btnEditDone.Visible = false;
                if (_btnEditCancel != null) _btnEditCancel.Visible = false;
                if (_statusTip != null) _statusTip.Visible = true;
                if (_tolTip != null) _tolTip.Text = _tolTipDefault ?? "";
                ApplyGridReadOnly();
                if (!SyncManualToRunner())
                    throw new InvalidOperationException("三表一致性校验失败：" + _lastSyncError);
                BindTolerance();
                if (_statusTip != null) _statusTip.Text = "已保存（未重检）";
            }
            catch (Exception ex) { MessageBox.Show(this, "保存失败：" + ex.Message, "工程图QC"); }
        }

        void CancelRowEdit()
        {
            if (!_rowEditing) return;
            try
            {
                if (_editRow != null && _editBackup != null)
                {
                    _editRow.Kind = _editBackup.Kind; _editRow.Content = _editBackup.Content;
                    _editRow.TolDisplay = _editBackup.TolDisplay; _editRow.IT = _editBackup.IT;
                    _editRow.Capability = _editBackup.Capability; _editRow.Note = _editBackup.Note;
                    _editRow.GtolSym = _editBackup.GtolSym; _editRow.GtolVal = _editBackup.GtolVal;
                    _editRow.GtolDat1 = _editBackup.GtolDat1; _editRow.GtolDat2 = _editBackup.GtolDat2;
                    _editRow.GtolDat3 = _editBackup.GtolDat3;
                    _editRow.GtolMod = _editBackup.GtolMod;
                    _editRow.Gtols = (_editBackup.Gtols == null) ? new List<QcGtolLine>() : _editBackup.Gtols;
                }
            }
            catch { }
            _rowEditing = false;
            _editRow = null;
            _editBackup = null;
            if (_btnEditDone != null) _btnEditDone.Visible = false;
            if (_btnEditCancel != null) _btnEditCancel.Visible = false;
            if (_statusTip != null) _statusTip.Visible = true;
            if (_tolTip != null) _tolTip.Text = _tolTipDefault ?? "";
            ApplyGridReadOnly();
            BindTolerance();
            if (_statusTip != null) _statusTip.Text = "已取消编辑";
        }

        static void ApplyCellToRow(QcDimRow r, int col, string val)
        {
            if (r == null) return;
            val = val ?? "";
            switch (col)
            {
                case 1: r.Kind = val; break;
                case 2:
                    r.Content = val;
                    double nv;
                    if (double.TryParse(val.TrimStart('⌀', 'φ', 'Ø', 'Φ').Trim(),
                            System.Globalization.NumberStyles.Float,
                            System.Globalization.CultureInfo.InvariantCulture, out nv) && nv > 0)
                        r.Nominal = nv;
                    break;
                case 3: r.TolDisplay = val; ApplyTolText(r, val); break;
                case 4: ApplyGtolText(r, val); break;
                case 5: r.IT = val; break;
                case 6: r.Capability = val; break;
                case 7: r.Note = val; break;
            }
        }

        void CopyCurrentRow()
        {
            try
            {
                if (_tolGrid.CurrentRow == null) return;
                var cells = new List<string>();
                for (int i = 1; i < _tolGrid.Columns.Count; i++)
                    cells.Add(Convert.ToString(_tolGrid.CurrentRow.Cells[i].Value) ?? "");
                Clipboard.SetText(string.Join("\t", cells.ToArray()));
            }
            catch { }
        }

        void MergeDuplicateGtol()
        {
            try
            {
                if (_tolGrid == null || _tolGrid.CurrentRow == null) return;
                QcDimRow r = _tolGrid.CurrentRow.Tag as QcDimRow;
                if (r == null || r.Gtols == null) return;
                var seen = new HashSet<string>();
                var uniq = new List<QcGtolLine>();
                int dup = 0;
                foreach (QcGtolLine g in r.Gtols)
                {
                    if (g == null) continue;
                    string k = QcRunner.GtolKey(g.Sym, g.Val, g.Dat1, g.Dat2, g.Mod, g.Dat3);
                    if (seen.Add(k)) uniq.Add(g); else dup++;
                }
                if (dup == 0)
                {
                    MessageBox.Show(this, "本行没有发现完全相同的几何公差。", "工程图QC");
                    return;
                }
                if (MessageBox.Show(this, "发现 " + dup + " 条重复几何公差，是否合并为 1 条？",
                        "合并重复几何公差", MessageBoxButtons.YesNo, MessageBoxIcon.Question) != DialogResult.Yes)
                    return;
                r.Gtols = uniq;
                if (uniq.Count > 0)
                {
                    r.GtolSym = uniq[0].Sym; r.GtolVal = uniq[0].Val;
                    r.GtolDat1 = uniq[0].Dat1; r.GtolDat2 = uniq[0].Dat2; r.GtolDat3 = uniq[0].Dat3; r.GtolMod = uniq[0].Mod;
                }
                string note = r.Note ?? "";
                if (note.StartsWith("几何 ")) r.Note = "";
                SyncManualToRunner();
                BindTolerance();
                if (_statusTip != null) _statusTip.Text = "已合并重复几何公差（未重检）";
            }
            catch (Exception ex) { MessageBox.Show(this, "合并失败：" + ex.Message, "工程图QC"); }
        }

        static bool CanCalc(QcDimRow r)
        {
            if (r == null || !r.HasTol) return false;
            if (r.Band <= 1e-9) return false;
            string k = r.Kind ?? "";
            if (k.Contains("粗糙度") || k.Contains("几何") || k.Contains("Ra")) return false;
            return true;
        }

        static string ShortKind(string kind)
        {
            string k = kind ?? "";
            if (k.StartsWith("特征")) return "特征";
            if (k.StartsWith("轮廓")) return "轮廓" + (k.Length > 3 ? k.Substring(3) : "");
            if (k.StartsWith("板厚")) return "板厚";
            return k;
        }

        void RefreshCalc()
        {
            if (_calc == null || _tolGrid == null) return;
            var picked = new List<QcDimRow>();
            foreach (DataGridViewRow row in _tolGrid.Rows)
            {
                var r = row.Tag as QcDimRow;
                if (r == null || !r.HasTol) continue;
                object v = row.Cells[0].Value;
                bool on = v != null && (v is bool ? (bool)v : Convert.ToString(v) == "True");
                if (on) picked.Add(r);
            }
            picked.Sort(delegate(QcDimRow a, QcDimRow b) { return a.Band.CompareTo(b.Band); });
            _calc.SetRows(picked);
        }

        // ================================================================ 文本解析
        /// <summary>供自测调用</summary>
        public static string GtolTextOf(QcDimRow r) { return GtolText(r); }

        /// <summary>供自测调用</summary>
        public static void ApplyGtolTextOf(QcDimRow r, string txt) { ApplyGtolText(r, txt); }

        static string GtolText(QcDimRow r)
        {
            if (r == null) return "";
            if ((r.Gtols == null || r.Gtols.Count == 0))
            {
                if (string.IsNullOrEmpty(r.GtolSym)) return "";
                string _v = r.GtolVal ?? "";
                string _t = r.GtolSym + " " + _v;
                if (!string.IsNullOrEmpty(r.GtolMod)) _t += r.GtolMod;
                if (!string.IsNullOrEmpty(r.GtolDat1)) _t += " |" + r.GtolDat1;
                if (!string.IsNullOrEmpty(r.GtolDat2)) _t += " |" + r.GtolDat2;
                if (!string.IsNullOrEmpty(r.GtolDat3)) _t += " |" + r.GtolDat3;
                return _t;
            }
            var parts = new List<string>();
            foreach (QcGtolLine g in r.Gtols)
            {
                // 2026-10-26：符号 + ⌀ + 值 + 修正(Ⓜ/Ⓛ) + |基准
                string sym = g.Sym ?? "";
                string val = g.Val ?? "";
                string t = (sym.Length > 0 ? sym + " " : "")
                         + val;
                if (!string.IsNullOrEmpty(g.Mod)) t += g.Mod;
                if (!string.IsNullOrEmpty(g.Dat1)) t += " |" + g.Dat1;
                if (!string.IsNullOrEmpty(g.Dat2)) t += " |" + g.Dat2;
                if (!string.IsNullOrEmpty(g.Dat3)) t += " |" + g.Dat3;
                parts.Add(t);
            }
            return string.Join("；", parts.ToArray());
        }

        static void ApplyGtolText(QcDimRow r, string txt)
        {
            try
            {
                var old = (r.Gtols == null) ? new List<QcGtolLine>() : new List<QcGtolLine>(r.Gtols);
                var usedOld = new HashSet<QcGtolLine>();
                var list = new List<QcGtolLine>();
                foreach (string seg in (txt ?? "").Split(new char[] { '；', ';', '，' },
                                                         StringSplitOptions.RemoveEmptyEntries))
                {
                    string t = seg.Trim();
                    if (t.Length == 0) continue;

                    // 2026-10-26：φ/⌀ 直径符号
                    bool dia = false;
                    if (t.Length > 0 && "φ⌀ΦøØ".IndexOf(t[0]) >= 0) { dia = true; t = t.Substring(1).Trim(); }

                    // 2026-10-26：修正符号（显式映射，不能用字符包含判断——'Ⓜ' 里没有 ASCII 'M'）
                    string mod = "";
                    var modMap = new System.Collections.Generic.Dictionary<string, string>
                    {
                        {"Ⓜ","Ⓜ"}, {"(M)","Ⓜ"}, {"(m)","Ⓜ"}, {"M","Ⓜ"},
                        {"Ⓛ","Ⓛ"}, {"(L)","Ⓛ"}, {"(l)","Ⓛ"}, {"L","Ⓛ"},
                        {"Ⓢ","Ⓢ"}, {"(S)","Ⓢ"}, {"(s)","Ⓢ"},
                        {"Ⓔ","Ⓔ"}, {"(E)","Ⓔ"}, {"(e)","Ⓔ"},
                    };
                    foreach (var kv in modMap)
                    {
                        if (t.IndexOf(kv.Key, StringComparison.Ordinal) >= 0)
                        {
                            mod = kv.Value;
                            t = t.Replace(kv.Key, " ");
                        }
                    }
                    t = t.Trim();

                    // φ/⌀ 单独分组，避免被吃进符号（"—φ0.05" → sym="—", dia, num=0.05）
                    var m = System.Text.RegularExpressions.Regex.Match(
                        t, @"^(?<sym>[^0-9φ⌀Φ]*?)\s*(?<dia>[φ⌀Φ])?\s*(?<num>[+]?[0-9]+(?:\.[0-9]+)?)\s*(?<tail>.*)$");
                    if (!m.Success) continue;
                    var g = new QcGtolLine();
                    g.Sym = (m.Groups["sym"].Value ?? "").Trim();
                    g.Mod = mod;
                    bool dia2 = dia || m.Groups["dia"].Success;
                    g.Val = (dia2 ? "φ" : "") + m.Groups["num"].Value;
                    // 基准：只取字母（A/B/C 或 A-B）
                    string tail = (m.Groups["tail"].Value ?? "").Replace("基准", " ");
                    var dats = System.Text.RegularExpressions.Regex.Matches(tail, @"\b[A-Z][A-Z0-9]*(?:-[A-Z][A-Z0-9]*)?\b");
                    if (dats.Count > 0) g.Dat1 = dats[0].Value;
                    if (dats.Count > 1) g.Dat2 = dats[1].Value;
                    if (dats.Count > 2) g.Dat3 = dats[2].Value;
                    double v;
                    string numOnly = System.Text.RegularExpressions.Regex.Replace(g.Val, @"[^0-9.]", "");
                    if (double.TryParse(numOnly, System.Globalization.NumberStyles.Float,
                            System.Globalization.CultureInfo.InvariantCulture, out v)) g.ValueMm = v;
                    // Editing a row updates existing entities. Preserve stable identity,
                    // compound-frame membership and the saved measured feature.
                    QcGtolLine keep = null;
                    foreach (QcGtolLine x in old)
                    {
                        if (x == null || usedOld.Contains(x)) continue;
                        if (QcRunner.GtolKey(x.Sym, x.Val, x.Dat1, x.Dat2, x.Mod, x.Dat3) ==
                            QcRunner.GtolKey(g.Sym, g.Val, g.Dat1, g.Dat2, g.Mod, g.Dat3))
                        { keep = x; break; }
                    }
                    if (keep == null && list.Count < old.Count) keep = old[list.Count];
                    if (keep != null)
                    {
                        usedOld.Add(keep);
                        g.GtolId = keep.GtolId; g.RawId = keep.RawId;
                        g.GroupId = keep.GroupId; g.FrameOrder = keep.FrameOrder;
                        g.FrameCount = keep.FrameCount; g.FrameType = keep.FrameType;
                        g.FrameReason = keep.FrameReason; g.ZoneType = keep.ZoneType;
                        g.MeasuredFeature = keep.MeasuredFeature; g.Manual = keep.Manual;
                    }
                    else if (old.Count > 0)
                    {
                        string commonGroup = old[0] == null ? "" : (old[0].GroupId ?? "");
                        bool sameGroup = !string.IsNullOrEmpty(commonGroup);
                        foreach (QcGtolLine x in old)
                            if (x == null || x.GroupId != commonGroup) { sameGroup = false; break; }
                        if (sameGroup) { g.GroupId = commonGroup; g.FrameOrder = list.Count + 1; }
                    }
                    list.Add(g);
                }
                r.Gtols = list;
                // 同步兼容旧版的单条字段；序列化、右键编辑和部分计算仍会读取这些字段。
                if (list.Count > 0)
                {
                    r.GtolSym = list[0].Sym ?? "";
                    r.GtolVal = list[0].Val ?? "";
                    r.GtolDat1 = list[0].Dat1 ?? "";
                    r.GtolDat2 = list[0].Dat2 ?? "";
                    r.GtolDat3 = list[0].Dat3 ?? "";
                    r.GtolMod = list[0].Mod ?? "";
                }
                else
                {
                    r.GtolSym = ""; r.GtolVal = ""; r.GtolDat1 = "";
                    r.GtolDat2 = ""; r.GtolDat3 = ""; r.GtolMod = "";
                }
                bool env = (r.Modifier == "Ⓔ");
                try { QcRunner.NormalizeRowGtols(r); } catch { }
                r.SigmaT = GtolRules.Recompute(r.Gtols, env);
                r.MmsShaft = r.Nominal + r.TolUp;
                r.MmsHole = r.Nominal + r.TolLow;
                r.MmvcShaft = Math.Round(r.MmsShaft + r.SigmaT, 4);
                r.MmvcHole = Math.Round(r.MmsHole - r.SigmaT, 4);
                // 备注保持原样（几何公差列已有信息；备注只放基准指向/其它说明）
            }
            catch { }
        }

        static void ApplyTolText(QcDimRow r, string txt)
        {
            try
            {
                txt = (txt ?? "").Replace(" ", "").Replace("　", "");
                if (txt.Length == 0 || txt == "无" || txt == "未注" || txt == "基本尺寸" || txt == "—")
                { r.TolUp = 0; r.TolLow = 0; r.HasTol = false; r.Band = 0; return; }
                var nums = new List<double>();
                foreach (System.Text.RegularExpressions.Match m in
                         System.Text.RegularExpressions.Regex.Matches(txt, @"[+\-±]?[0-9]+(?:\.[0-9]+)?"))
                {
                    string t = m.Value;
                    bool neg = t.StartsWith("-");
                    if (t.StartsWith("±")) t = t.Substring(1);
                    double v;
                    if (double.TryParse(t.TrimStart('+'), System.Globalization.NumberStyles.Float,
                            System.Globalization.CultureInfo.InvariantCulture, out v))
                        nums.Add(neg ? -v : v);
                }
                if (txt.StartsWith("±") && nums.Count >= 1)
                {
                    r.TolUp = Math.Abs(nums[0]);
                    r.TolLow = -Math.Abs(nums[0]);
                }
                else if (nums.Count >= 2)
                {
                    r.TolUp = Math.Max(nums[0], nums[1]);
                    r.TolLow = Math.Min(nums[0], nums[1]);
                }
                else if (nums.Count == 1)
                {
                    r.TolUp = Math.Max(0, nums[0]);
                    r.TolLow = Math.Min(0, nums[0]);
                }
                r.Band = r.TolUp - r.TolLow;
                r.HasTol = r.Band > 1e-9;
                r.MmsShaft = r.Nominal + r.TolUp;
                r.MmsHole = r.Nominal + r.TolLow;
                r.MmvcShaft = Math.Round(r.MmsShaft + r.SigmaT, 4);
                r.MmvcHole = Math.Round(r.MmsHole - r.SigmaT, 4);
            }
            catch { }
        }

        // ================================================================ 更新/状态
        public void UpdateReport(QcReport r)
        {
            if (_closing || IsDisposed) return;   // 已在关闭：不再往窗口写数据
            UpdateReportCore(r);
        }

        void ResetAllGtols()
        {
            try { _allGtols.Clear(); } catch { }
            try { _initialGtols.Clear(); } catch { }
        }

        /// <summary>从本次报告收集“图纸全部几何公差”（尺寸行 + 未关联 + 几何公差分析），只建一次</summary>
        /// <summary>v193：按当前基准表(记忆)重算所有尺寸行的“基准指向”备注，立即刷新三表显示</summary>
        void RefreshDatumNotesLocally()
        {
            if (_doc != null && _r != null)
            {
                _doc.ApplyToReport(_r);
                return;
            }
            if (_r == null || _r.DimRows == null) return;
            Dictionary<string, string> dm = new Dictionary<string, string>();
            try { dm = QcRunner.LoadDatumMap(_r.DwgNo) ?? new Dictionary<string, string>(); } catch { }
            // 尺寸内容索引（用于把 42 解析成 ⌀42）
            var contents = new List<string>();
            foreach (QcDimRow r0 in _r.DimRows) if (r0 != null && !string.IsNullOrEmpty(r0.Content)) contents.Add(r0.Content);
            foreach (QcDimRow r in _r.DimRows)
            {
                if (r == null || r.Gtols == null || r.Gtols.Count == 0) continue;
                var keep = new List<string>();
                foreach (string seg in (r.Note ?? "").Split(new char[] { '；' }, StringSplitOptions.RemoveEmptyEntries))
                {
                    string x = seg.Trim();
                    if (x.Length == 0) continue;
                    if (x.StartsWith("基准 ")) continue;       // 旧的基准指向，重建
                    keep.Add(x);
                }
                var segs = new List<string>();
                foreach (QcGtolLine g in r.Gtols)
                {
                    if (g == null) continue;
                    foreach (string letter in new string[] { (g.Dat1 ?? "").Trim(), (g.Dat2 ?? "").Trim(), (g.Dat3 ?? "").Trim() })
                    {
                        if (letter.Length == 0) continue;
                        string near = "";
                        string kind = "";
                        try { dm.TryGetValue(letter + "#near", out near); dm.TryGetValue(letter, out kind); } catch { }
                        if (near == null) near = "";
                        string seg;
                        if (string.IsNullOrEmpty(near))
                        {
                            seg = "基准 " + letter + " → 待指定（请到「基准设置」指定）";
                        }
                        else
                        {
                            string target = near;
                            // 纯数字时优先用 ⌀ 尺寸内容
                            string num = System.Text.RegularExpressions.Regex.Match(near, @"[0-9]+(?:\.[0-9]+)?").Value;
                            if (!string.IsNullOrEmpty(num) && System.Text.RegularExpressions.Regex.IsMatch(near, @"^[0-9]+(?:\.[0-9]+)?$"))
                            {
                                string dia = null, plain = null;
                                foreach (string c0 in contents)
                                {
                                    if (c0.IndexOf(num, StringComparison.Ordinal) < 0) continue;
                                    if (c0.StartsWith("⌀") || c0.StartsWith("φ")) { dia = c0; break; }
                                    if (plain == null) plain = c0;
                                }
                                target = dia ?? (plain ?? near);
                            }
                            if (!target.Contains("平面") && !target.Contains("轴") && !target.Contains("孔") && !target.Contains("中心"))
                                target += " 平面";
                            seg = "基准 " + letter + " → " + target + "（人工指定）";
                        }
                        if (!segs.Contains(seg)) segs.Add(seg);
                    }
                }
                r.Note = string.Join("；", (keep.Count > 0 ? keep.ToArray() : new string[0])).Trim('；');
                if (segs.Count > 0)
                    r.Note = (r.Note.Length > 0 ? r.Note + "；" : "") + string.Join("；", segs.ToArray());
                try { QcRunner.NormalizeRowGtols(r); } catch { }
            }
        }

        void EnsureAllGtols()
        {
            if (_r == null) return;
            if (_doc != null)
            {
                _allGtols.Clear();
                _allGtols.AddRange(_doc.BuildAllGtolItems());
                return;
            }
            try
            {
                // ① 第一次：捕获“图纸全部几何公差”全集（用于发现表格缺失）
                if (_initialGtols.Count == 0)
                {
                    var seen0 = new HashSet<string>();
                    var seenSig0 = new HashSet<string>();
                    if (_r.DimRows != null)
                    {
                        foreach (QcDimRow r in _r.DimRows)
                        {
                            if (r == null || r.Gtols == null) continue;
                            foreach (QcGtolLine g in r.Gtols)
                            {
                                if (g == null) continue;
                                string sig = QcRunner.GtolKey(g.Sym, g.Val, g.Dat1, g.Dat2, g.Mod, g.Dat3);
                                if (!seen0.Add(r.Content + "||" + sig)) continue;
                                seenSig0.Add(sig);
                                _initialGtols.Add(new QcUnlinkedGtol
                                {
                                    Sym = g.Sym, Val = g.Val, Dat1 = g.Dat1, Dat2 = g.Dat2, Dat3 = g.Dat3, Mod = g.Mod,
                                    Label = "", View = "", Target = r.Content,
                                    RawId = g.RawId, GroupId = g.GroupId, FrameOrder = g.FrameOrder,
                                    MeasuredFeature = g.MeasuredFeature
                                });
                            }
                        }
                    }
                    if (_r.UnlinkedGtols != null)
                    {
                        foreach (QcUnlinkedGtol u in _r.UnlinkedGtols)
                        {
                            if (u == null) continue;
                            string sig = QcRunner.GtolKey(u.Sym, u.Val, u.Dat1, u.Dat2, u.Mod, u.Dat3);
                            if (seenSig0.Contains(sig)) continue;
                            if (!seen0.Add("unlinked||" + sig)) continue;
                            seenSig0.Add(sig);
                            _initialGtols.Add(new QcUnlinkedGtol
                            {
                                Sym = u.Sym, Val = u.Val, Dat1 = u.Dat1, Dat2 = u.Dat2, Dat3 = u.Dat3, Mod = u.Mod,
                                Label = u.Label, View = ""
                            });
                        }
                    }
                    if (_r.ToleranceRows != null)
                    {
                        foreach (string[] tr in _r.ToleranceRows)
                        {
                            if (tr == null || tr.Length < 2) continue;
                            string kind = (tr[0] ?? "").Trim();
                            if (!kind.Contains("几何公差")) continue;
                            var tmp = new QcDimRow();
                            ApplyGtolTextOf(tmp, tr[1] ?? "");
                            foreach (QcGtolLine g in tmp.Gtols)
                            {
                                if (g == null) continue;
                                string sig = QcRunner.GtolKey(g.Sym, g.Val, g.Dat1, g.Dat2, g.Mod, g.Dat3);
                                if (seenSig0.Contains(sig)) continue;
                                seenSig0.Add(sig);
                                _initialGtols.Add(new QcUnlinkedGtol
                                {
                                    Sym = g.Sym, Val = g.Val, Dat1 = g.Dat1, Dat2 = g.Dat2, Dat3 = g.Dat3, Mod = g.Mod,
                                    Label = "", View = "", Target = ""
                                });
                            }
                        }
                    }
                }

                // ② 每次打开：按“当前表格”重建，再加上初次全集里缺的（保证条数一致）
                _allGtols.Clear();
                var seenKey = new HashSet<string>();
                var curCount = new Dictionary<string, int>();
                if (_r.DimRows != null)
                {
                    foreach (QcDimRow r in _r.DimRows)
                    {
                        if (r == null || r.Gtols == null) continue;
                        foreach (QcGtolLine g in r.Gtols)
                        {
                            if (g == null) continue;
                            string sig = QcRunner.GtolKey(g.Sym, g.Val, g.Dat1, g.Dat2, g.Mod, g.Dat3);
                            if (!seenKey.Add(r.Content + "||" + sig)) continue;
                            int cc; curCount.TryGetValue(sig, out cc); curCount[sig] = cc + 1;
                            _allGtols.Add(new QcUnlinkedGtol
                            {
                                Sym = g.Sym, Val = g.Val, Dat1 = g.Dat1, Dat2 = g.Dat2, Dat3 = g.Dat3, Mod = g.Mod,
                                Label = "", View = "", Target = r.Content,
                                RawId = g.RawId, GroupId = g.GroupId, FrameOrder = g.FrameOrder,
                                MeasuredFeature = g.MeasuredFeature
                            });
                        }
                    }
                }
                // 初次全集统计
                var initCount = new Dictionary<string, int>();
                foreach (QcUnlinkedGtol g in _initialGtols)
                {
                    string sig = QcRunner.GtolKey(g.Sym, g.Val, g.Dat1, g.Dat2, g.Mod, g.Dat3);
                    int cc; initCount.TryGetValue(sig, out cc); initCount[sig] = cc + 1;
                }
                foreach (QcUnlinkedGtol g in _initialGtols)
                {
                    string sig = QcRunner.GtolKey(g.Sym, g.Val, g.Dat1, g.Dat2, g.Mod, g.Dat3);
                    int have; curCount.TryGetValue(sig, out have);
                    int need; initCount.TryGetValue(sig, out need);
                    if (have >= need) { curCount[sig] = have - 1; continue; }
                    curCount[sig] = have - 1;
                    _allGtols.Add(new QcUnlinkedGtol
                    {
                        Sym = g.Sym, Val = g.Val, Dat1 = g.Dat1, Dat2 = g.Dat2, Dat3 = g.Dat3, Mod = g.Mod,
                        Label = "（表格中缺失，可重新指定）", View = "", Target = g.Target
                    });
                }
            }
            catch { }
        }

        bool SyncManualToRunner()
        {
            _lastSyncError = "";
            try
            {
                if (_doc != null && _r != null)
                {
                    _doc.CommitDimensionView(_r.DimRows ?? new List<QcDimRow>());
                    string validation;
                    if (!_doc.ValidateForPersistence(out validation))
                        throw new InvalidOperationException(validation);
                    _doc.ApplyToReport(_r);
                    QcRunner.DocumentSnapshotJson = _doc.ToSnapshotJson();
                    QcRunner.ManualRows = new List<Dictionary<string, string>>();
                    return true;
                }
                var list = new List<Dictionary<string, string>>();
                // 不再只传“手工新增行”。整张尺寸/公差表按当前显示顺序传给引擎，
                // AI、审核结果和刷新后的表格都只能引用这一份最终数据。
                if (_r == null || _r.DimRows == null)
                {
                    QcRunner.ManualRows = list;
                    return true;
                }
                foreach (QcDimRow r in _r.DimRows)
                {
                    if (r == null) continue;
                    try { QcRunner.NormalizeRowGtols(r); } catch { }
                    var d = new Dictionary<string, string>();
                    d["source"] = "table";
                    d["dwg_no"] = _r.DwgNo ?? "";
                    d["is_manual"] = r.IsManual ? "1" : "0";
                    d["kind"] = r.Kind ?? "";
                    d["content"] = r.Content ?? "";
                    d["tol"] = r.TolDisplay ?? "";
                    d["it"] = r.IT ?? "";
                    d["capability"] = r.Capability ?? "";
                    d["note"] = r.Note ?? "";
                    d["gtol"] = GtolText(r);
                    list.Add(d);
                }
                QcRunner.ManualRows = list;
                return true;
            }
            catch (Exception ex) { _lastSyncError = ex.Message; return false; }
        }

        void TriggerRecheck()
        {
            try
            {
                if (_btnCheck != null && _btnCheck.Enabled)
                {
                    var t = new Timer();
                    t.Interval = 250;
                    t.Tick += delegate
                    {
                        try { t.Stop(); t.Dispose(); } catch { }
                        try { if (_btnCheck != null && _btnCheck.Enabled) _btnCheck.PerformClick(); } catch { }
                    };
                    t.Start();
                }
            }
            catch { }
        }

        void DeleteTolRow(QcDimRow rr)
        {
            if (rr == null || _r == null) return;
            try
            {
                string fp = DimFpOf(rr);
                _deletedFp.Add(fp);
                QcRunner.ExcludedDimRows = new List<string>(_deletedFp);
                int idx = -1;
                try { idx = _r.DimRows.IndexOf(rr); } catch { }
                if (_manualRows.Contains(rr)) _manualRows.Remove(rr);
                try { _r.DimRows.Remove(rr); } catch { }
                _undo.Add(new UndoOp { Type = "delete", Row = CloneRow(rr), Index = idx });
                if (_undo.Count > 50) _undo.RemoveAt(0);
                SyncManualToRunner();
                BindTolerance();
                EnsureUnlinkedBanner();
                ShowDetail();
                if (_statusTip != null) _statusTip.Text = "已删除（点「检查」更新审核结果）";
            }
            catch { }
        }

        void UndoLast()
        {
            if (_undo.Count == 0)
            {
                try { MessageBox.Show(this, "没有可撤回的操作。", "工程图QC"); } catch { }
                return;
            }
            try
            {
                UndoOp op = _undo[_undo.Count - 1];
                _undo.RemoveAt(_undo.Count - 1);
                if (op.Type == "delete" && op.Row != null)
                {
                    _deletedFp.Remove(DimFpOf(op.Row));
                    QcRunner.ExcludedDimRows = new List<string>(_deletedFp);
                    QcDimRow row = CloneRow(op.Row);
                    row.IsManual = op.Row.IsManual;
                    int at = op.Index;
                    if (at < 0 || at > _r.DimRows.Count) at = _r.DimRows.Count;
                    _r.DimRows.Insert(at, row);
                    if (row.IsManual) _manualRows.Add(row);
                    SyncManualToRunner();
                    BindTolerance();
                    EnsureUnlinkedBanner();
                    ShowDetail();
                    if (_statusTip != null) _statusTip.Text = "已撤销删除（点「检查」更新审核结果）";
                }
                else if (op.Type == "add" && op.Row != null)
                {
                    try { _r.DimRows.Remove(op.Row); } catch { }
                    _manualRows.Remove(op.Row);
                    SyncManualToRunner();
                    BindTolerance();
                    EnsureUnlinkedBanner();
                    ShowDetail();
                    if (_statusTip != null) _statusTip.Text = "已撤销新增（点「检查」更新审核结果）";
                }
            }
            catch (Exception ex) { try { MessageBox.Show(this, "撤回失败：" + ex.Message, "工程图QC"); } catch { } }
        }

        void AddManualRow()
        {
            try
            {
                using (var dlg = new QcManualRowForm("新增尺寸/公差行"))
                {
                    _childDlg = dlg;
                    try
                    {
                        if (dlg.ShowDialog(this) != DialogResult.OK) return;
                    }
                    finally { _childDlg = null; }
                    QcDimRow row = RowFromDialog(dlg);
                    row.IsManual = true;
                    if (_r == null) return;
                    _r.DimRows.Add(row);
                    _manualRows.Add(row);
                    _undo.Add(new UndoOp { Type = "add", Row = row, Index = _r.DimRows.Count - 1 });
                    if (_undo.Count > 50) _undo.RemoveAt(0);
                    SyncManualToRunner();
                    try { RefreshDatumNotesLocally(); } catch { }
                    BindTolerance();
                    EnsureUnlinkedBanner();
                    if (_statusTip != null) _statusTip.Text = "已新增（点「检查」更新审核结果）";
                }
            }
            catch (Exception ex) { try { MessageBox.Show(this, "新增失败：" + ex.Message, "工程图QC"); } catch { } }
        }

        void EditManualRow(QcDimRow rr)
        {
            if (rr == null) return;
            try
            {
                using (var dlg = new QcManualRowForm("编辑尺寸/公差行"))
                {
                    // 列表显示“—”=基本尺寸（不带±公差）：弹窗里也按“无公差”显示，避免 0/0 对不上
                    bool isBasic = (rr.TolDisplay == "上 0 下 0" || rr.TolDisplay == "无" ||
                                    rr.TolDisplay == "未注" || rr.TolDisplay == "基本尺寸" ||
                                    rr.TolDisplay == "—");
                    string tolPrefill = isBasic ? "" : (rr.TolDisplay ?? "");
                    dlg.LoadFrom(rr.Kind, rr.Content, tolPrefill, rr.IT,
                                 GtolText(rr), rr.Note);
                    _childDlg = dlg;
                    try
                    {
                        if (dlg.ShowDialog(this) != DialogResult.OK) return;
                    }
                    finally { _childDlg = null; }
                    QcDimRow nr = RowFromDialog(dlg);
                    nr.RowId = rr.RowId;       // 修改内容不改变尺寸身份，现有几何归属继续有效
                    nr.IsManual = rr.IsManual;
                    // 备注：编辑保持用户原文，不加“手工新增”前缀
                    nr.Note = dlg.Note ?? "";
                    // 几何公差：完整解析（支持多条/修正符/基准）
                    try
                    {
                        // 编辑必须从原实体开始，不能先用纯文本新建一条再猜身份。
                        // 否则双框会丢 GroupId/FrameOrder，刷新后被拆成两个“单框”。
                        QcDimRow oldCopy = CloneRow(rr);
                        nr.Gtols = oldCopy.Gtols ?? new List<QcGtolLine>();
                        ApplyGtolText(nr, dlg.GtolText ?? "");
                    }
                    catch { }
                    if (!rr.IsManual)
                    {
                        nr.Capability = rr.Capability;
                        // 备注允许清空：不再用旧值回填（v217）
                    }
                    // 原本是基本尺寸、且弹窗里没填公差 → 保持基本尺寸
                    bool noTolTyped = string.IsNullOrEmpty((dlg.TolUp ?? "").Trim())
                                   && string.IsNullOrEmpty((dlg.TolLow ?? "").Trim());
                    if (isBasic && noTolTyped)
                    {
                        nr.TolDisplay = "上 0 下 0";
                        nr.TolUp = 0; nr.TolLow = 0; nr.Band = 0; nr.HasTol = false;
                    }
                    int idx = _r.DimRows.IndexOf(rr);
                    if (idx >= 0) _r.DimRows[idx] = nr;
                    int mi = _manualRows.IndexOf(rr);
                    if (mi >= 0) _manualRows[mi] = nr;
                    SyncManualToRunner();     // 确认即保存到会话快照
                    try { RefreshDatumNotesLocally(); } catch { }
                    BindTolerance();          // 刷新表格
                    EnsureUnlinkedBanner();
                    ShowDetail();
                    if (_statusTip != null) _statusTip.Text = "已保存（点检查更新审核结果）";
                }
            }
            catch (Exception ex) { try { MessageBox.Show(this, "编辑失败：" + ex.Message, "工程图QC"); } catch { } }
        }

        static QcDimRow CloneRow(QcDimRow r)
        {
            if (r == null) return null;
            var c = new QcDimRow();
            c.RowId = r.RowId;
            c.Kind = r.Kind; c.Content = r.Content; c.TolDisplay = r.TolDisplay;
            c.IT = r.IT; c.Capability = r.Capability; c.Note = r.Note;
            c.Modifier = r.Modifier; c.GtolSym = r.GtolSym; c.GtolVal = r.GtolVal;
            c.GtolMod = r.GtolMod; c.GtolDat1 = r.GtolDat1; c.GtolDat2 = r.GtolDat2; c.GtolDat3 = r.GtolDat3;
            c.Nominal = r.Nominal; c.TolUp = r.TolUp; c.TolLow = r.TolLow;
            c.HasTol = r.HasTol; c.Band = r.Band; c.IsOutline = r.IsOutline; c.IsManual = r.IsManual;
            if (r.Gtols != null)
            {
                c.Gtols = new List<QcGtolLine>();
                foreach (QcGtolLine g in r.Gtols)
                {
                    if (g == null) continue;
                    var ng = new QcGtolLine();
                    ng.GtolId = g.GtolId; ng.RawId = g.RawId; ng.GroupId = g.GroupId;
                    ng.FrameOrder = g.FrameOrder; ng.MeasuredFeature = g.MeasuredFeature;
                    ng.FrameCount = g.FrameCount; ng.FrameType = g.FrameType;
                    ng.FrameReason = g.FrameReason; ng.ZoneType = g.ZoneType;
                    ng.Sym = g.Sym; ng.Val = g.Val; ng.Mod = g.Mod;
                    ng.Dat1 = g.Dat1; ng.Dat2 = g.Dat2; ng.Dat3 = g.Dat3; ng.Include = g.Include;
                    ng.ValueMm = g.ValueMm; ng.Reason = g.Reason;
                    c.Gtols.Add(ng);
                }
            }
            return c;
        }

        static QcDimRow RowFromDialog(QcManualRowForm dlg)
        {
            var row = new QcDimRow();
            row.Kind = string.IsNullOrEmpty(dlg.Kind) ? "尺寸特征" : dlg.Kind;
            row.Content = dlg.Content ?? "";
            string up = dlg.TolUp ?? "", low = dlg.TolLow ?? "";
            if (string.IsNullOrEmpty(up) && string.IsNullOrEmpty(low))
                row.TolDisplay = "无";
            else
                row.TolDisplay = "上 " + (up.Length > 0 ? up : "0") + " 下 " + (low.Length > 0 ? low : "0");
            row.IT = string.IsNullOrEmpty(dlg.ITText) ? "自动" : dlg.ITText;
            row.Note = string.IsNullOrEmpty(dlg.Note) ? "手工新增" : ("手工新增；" + dlg.Note);
            row.Capability = "手工新增行（本会话有效）";
            try
            {
                string num = System.Text.RegularExpressions.Regex.Match(row.Content,
                    @"[0-9]+(?:\.[0-9]+)?").Value;
                double v;
                if (double.TryParse(num, System.Globalization.NumberStyles.Float,
                        System.Globalization.CultureInfo.InvariantCulture, out v))
                    row.Nominal = v;
            }
            catch { }
            try
            {
                double upv, lowv;
                if (double.TryParse(up, System.Globalization.NumberStyles.Float,
                        System.Globalization.CultureInfo.InvariantCulture, out upv)) row.TolUp = upv;
                if (double.TryParse(low, System.Globalization.NumberStyles.Float,
                        System.Globalization.CultureInfo.InvariantCulture, out lowv)) row.TolLow = lowv;
                row.HasTol = !string.IsNullOrEmpty(up) || !string.IsNullOrEmpty(low)
                             || !string.IsNullOrEmpty(dlg.GtolText);
                row.Band = row.TolUp - row.TolLow;
            }
            catch { }
            string gt = dlg.GtolText ?? "";
            if (!string.IsNullOrEmpty(gt)) ApplyGtolText(row, gt); // 支持多框，不能只取第一段
            return row;
        }


        static string IniValue(string ini, string key)
        {
            try
            {
                foreach (string line in System.IO.File.ReadAllLines(ini))
                {
                    int p = line.IndexOf('=');
                    if (p <= 0) continue;
                    if (line.Substring(0, p).Trim().Equals(key, StringComparison.OrdinalIgnoreCase))
                        return line.Substring(p + 1).Trim();
                }
            }
            catch { }
            return "";
        }

        void ConfirmSaveToDb()
        {
            try
            {
                if (_r == null) return;
                if (_rowEditing)
                {
                    MessageBox.Show(this, "正在编辑尺寸行，请先点「完成」或「取消」，再确认保存。", "工程图QC");
                    return;
                }
                if (_storageBlocked)
                {
                    MessageBox.Show(this, "文档数据处于校验失败状态，禁止覆盖保存：\r\n" + _storageError,
                        "工程图QC", MessageBoxButtons.OK, MessageBoxIcon.Error);
                    return;
                }
                if (!SyncManualToRunner())
                {
                    MessageBox.Show(this, "保存前一致性校验失败，未写入工程图：\r\n" + _lastSyncError,
                        "工程图QC", MessageBoxButtons.OK, MessageBoxIcon.Error);
                    return;
                }
                bool documentSaved = false;
                if (_docStore != null)
                {
                    if (_doc == null)
                    {
                        MessageBox.Show(this, "尚未生成统一主数据，请先完成图纸读取。", "工程图QC");
                        return;
                    }
                    QcDocument loaded; string saveError;
                    if (!_docStore.SaveVerified(_doc, out loaded, out saveError))
                    {
                        MessageBox.Show(this, "确认保存失败；工程图内数据未通过回读验证：\r\n\r\n" + saveError,
                            "工程图QC", MessageBoxButtons.OK, MessageBoxIcon.Error);
                        return;
                    }
                    _doc = loaded;
                    _doc.ApplyToReport(_r);
                    QcRunner.DocumentSnapshotJson = _doc.ToSnapshotJson();
                    documentSaved = true;
                    _storageBlocked = false; _storageError = "";
                    BindTolerance(); ParseDatums(); EnsureUnlinkedBanner();
                    if (_statusTip != null) _statusTip.Text = "工程图内数据已保存，并通过哈希与逐字段回读验证";
                }
                string dir = string.IsNullOrEmpty(_r.ReportDir) ? "" : _r.ReportDir;
                if (string.IsNullOrEmpty(dir) || !System.IO.Directory.Exists(dir))
                {
                    MessageBox.Show(this, documentSaved
                        ? "工程图内确认数据已保存并验证一致。\r\n数据库未写入：找不到报告目录，请先点「检查」生成报告。"
                        : "找不到报告目录，请先点「检查」生成报告。", "工程图QC");
                    return;
                }
                string rep = "";
                string[] files = new string[0];
                try { files = System.IO.Directory.GetFiles(dir, "*.qc_report.json"); } catch { }
                if (files.Length == 0)
                {
                    try { files = System.IO.Directory.GetFiles(dir, "*report*.json", System.IO.SearchOption.AllDirectories); } catch { }
                }
                if (files.Length == 0)
                {
                    try { files = System.IO.Directory.GetFiles(dir, "*.json", System.IO.SearchOption.AllDirectories); } catch { }
                }
                DateTime best = DateTime.MinValue;
                foreach (string f in files)
                {
                    DateTime t = System.IO.File.GetLastWriteTime(f);
                    if (t > best) { best = t; rep = f; }
                }
                if (string.IsNullOrEmpty(rep))
                {
                    MessageBox.Show(this, documentSaved
                        ? "工程图内确认数据已保存并验证一致。\r\n数据库未写入：没有 qc_report.json，请先点「检查」。"
                        : "本目录没有 qc_report.json，请先点「检查」。", "工程图QC");
                    return;
                }
                string local = Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData);
                string ini = System.IO.Path.Combine(local, "SwQcAddin", "qc_addin_config.ini");
                string python = IniValue(ini, "python");
                string native = IniValue(ini, "native");
                if (string.IsNullOrEmpty(python) || !System.IO.File.Exists(python))
                {
                    MessageBox.Show(this, (documentSaved ? "工程图内确认数据已保存并验证一致。\r\n" : "") +
                        "未配置 Python，无法写入数据库。", "工程图QC");
                    return;
                }
                string root = System.IO.Path.GetFullPath(System.IO.Path.Combine(
                    System.IO.Path.GetDirectoryName(native ?? ""), "..", ".."));
                string saver = System.IO.Path.Combine(root, "数据库", "db_save.py");
                string db = System.IO.Path.Combine(root, "数据库", "qc_stats.db");
                if (!System.IO.File.Exists(saver))
                {
                    MessageBox.Show(this, (documentSaved ? "工程图内确认数据已保存并验证一致。\r\n" : "") +
                        "找不到数据库脚本：\r\n" + saver, "工程图QC");
                    return;
                }
                var psi = new System.Diagnostics.ProcessStartInfo();
                psi.FileName = python;
                psi.Arguments = "\"" + saver + "\" \"" + rep + "\" \"" + db + "\"";
                psi.UseShellExecute = false;
                psi.CreateNoWindow = true;
                psi.RedirectStandardOutput = true;
                psi.RedirectStandardError = true;
                psi.StandardOutputEncoding = System.Text.Encoding.UTF8;
                psi.StandardErrorEncoding = System.Text.Encoding.UTF8;
                using (var p = System.Diagnostics.Process.Start(psi))
                {
                    string o = p.StandardOutput.ReadToEnd();
                    string e = p.StandardError.ReadToEnd();
                    p.WaitForExit();
                    if (p.ExitCode == 0)
                    {
                        if (_statusTip != null) _statusTip.Text = documentSaved
                            ? "工程图内保存/回读验证通过；数据库已保存" : "已保存人工确认版本到数据库";
                        MessageBox.Show(this, (documentSaved ? "工程图内保存及回读验证：通过\r\n" : "") +
                            "数据库已保存：\r\n" + o, "工程图QC");
                    }
                    else
                    {
                        MessageBox.Show(this, (documentSaved ? "工程图内确认数据已保存并验证一致。\r\n" : "") +
                            "写入数据库失败：\r\n" + e, "工程图QC");
                    }
                }
            }
            catch (Exception ex) { MessageBox.Show(this, "确认保存失败：" + ex.Message, "工程图QC"); }
        }

        void RereadDrawing()
        {
            try
            {
                if (_rowEditing)
                {
                    MessageBox.Show(this, "正在编辑尺寸行，请先点「完成」或「取消」，再重读图纸。", "工程图QC");
                    return;
                }
                if (_busy) return;
                if (MessageBox.Show(this,
                        "重读图纸会清空本窗口内对尺寸、几何公差、归属、基准和备注的全部人工修改。\r\n\r\n确定重新读取当前图纸吗？",
                        "重读图纸", MessageBoxButtons.YesNo, MessageBoxIcon.Warning) != DialogResult.Yes)
                    return;
                // 用户明确要求：重读就是清空本次会话并重新创建，不合并、不恢复旧记忆。
                QcRunner.ResetSession();
                ResetAllGtols();
                _doc = null;
                _ignoreStoredForSession = true;
                _storageLoadAttempted = true;
                _storageBlocked = false;
                _storageError = "";
                try { _deletedFp.Clear(); _manualRows.Clear(); _undo.Clear(); } catch { }
                try { if (_r != null && _r.UnlinkedGtols != null) _r.UnlinkedGtols.Clear(); } catch { }
                QcRunner.SkipAiOnce = true;      // 只重读，不跑 AI
                ForceRereadNext = true;          // 强制重新提取图纸 JSON（不复用缓存）
                if (_statusTip != null)
                    _statusTip.Text = "正在重新读取图纸；原人工修改已清空";
                if (CheckRequested != null) CheckRequested(this, EventArgs.Empty);
            }
            catch { }
        }

        static string DimFpOf(QcDimRow r)
        {
            if (r == null) return "";
            return (((r.Kind ?? "") + "|" + (r.Content ?? "") + "|" + (r.TolDisplay ?? ""))).Replace("—", "-");
        }

        void ParseDatums()
        {
            _datums = new List<DatumRow>();
            try
            {
                if (_r != null && _r.Datums != null)
                {
                    foreach (QcDatum d in _r.Datums)
                    {
                        if (d == null) continue;
                        _datums.Add(new DatumRow
                        {
                            Letter = d.Letter ?? "",
                            Auto = d.Target ?? "",
                            Near = string.IsNullOrEmpty(d.NearManual) ? (d.Near ?? "") : d.NearManual,
                            View = d.View ?? "",
                            Coord = d.Coord ?? "",
                            Manual = d.Manual ?? "",
                        });
                    }
                }
            }
            catch { }
        }

        void UpdateReportCore(QcReport r)
        {
            if (r == null) return;
            bool hasSessionData = (r.DimRows != null && r.DimRows.Count > 0)
                               || (r.UnlinkedGtols != null && r.UnlinkedGtols.Count > 0)
                               || (r.Datums != null && r.Datums.Count > 0);
            if (!_storageLoadAttempted && !_ignoreStoredForSession && _docStore != null &&
                ((r.Header != null && r.Header.Count > 0) || hasSessionData))
            {
                _storageLoadAttempted = true;
                QcDocument saved; string loadError;
                if (_docStore.TryLoadDocument(out saved, out loadError))
                {
                    string currentNo = r.DwgNo ?? "";
                    if (!string.IsNullOrEmpty(currentNo) && !string.IsNullOrEmpty(saved.DrawingNo) &&
                        !string.Equals(currentNo.Trim(), saved.DrawingNo.Trim(), StringComparison.OrdinalIgnoreCase))
                    {
                        _storageBlocked = true;
                        _storageError = "文档内图号 " + saved.DrawingNo + " 与当前图号 " + currentNo + " 不一致";
                    }
                    else
                    {
                        _doc = saved;
                        _storageBlocked = false; _storageError = "";
                        if (_statusTip != null) _statusTip.Text = "已从工程图读取人工确认数据，并通过哈希/逐字段校验";
                    }
                }
                else if (!string.IsNullOrEmpty(loadError))
                {
                    _storageBlocked = true; _storageError = loadError;
                    MessageBox.Show(this, "工程图内的确认数据校验失败，已停止加载和输出。\r\n\r\n" +
                        loadError + "\r\n\r\n如需放弃旧数据并重新识别，请点「重读图纸」。",
                        "工程图QC", MessageBoxButtons.OK, MessageBoxIcon.Error);
                }
            }
            if (_storageBlocked)
            {
                r.DimRows = new List<QcDimRow>();
                r.UnlinkedGtols = new List<QcUnlinkedGtol>();
                r.Datums = new List<QcDatum>();
                _doc = null;
                if (_statusTip != null) _statusTip.Text = "文档数据校验失败，已阻止输出；请重读图纸或恢复备份";
            }
            else if (_doc == null && hasSessionData)
            {
                _doc = QcDocument.FromReport(r);
                string validateError;
                if (!_doc.ValidateForPersistence(out validateError))
                    throw new InvalidOperationException("初始主数据校验失败：" + validateError);
                _doc.ApplyToReport(r);
                QcRunner.DocumentSnapshotJson = _doc.ToSnapshotJson();
            }
            else if (_doc != null)
            {
                // 检查/AI 只更新审核结果，不得用引擎返回重新覆盖三张人工表。
                _doc.ApplyToReport(r);
                QcRunner.DocumentSnapshotJson = _doc.ToSnapshotJson();
            }
            _r = r;
            // 本会话右键删除的行：无论引擎是否已过滤，UI 再兜底剔除一次
            try
            {
                if (_deletedFp.Count > 0 && _r.DimRows != null)
                    _r.DimRows.RemoveAll(x => x != null && _deletedFp.Contains(DimFpOf(x)));
            }
            catch { }
            // 重新叠加本会话手工新增/编辑的行
            try
            {
                if (_manualRows.Count > 0 && _r.DimRows != null)
                {
                    foreach (QcDimRow m in _manualRows)
                    {
                        if (m == null) continue;
                        bool exists = false;
                        foreach (QcDimRow x in _r.DimRows)
                            if (x != null && x.IsManual && x.Content == m.Content && x.Kind == m.Kind) { exists = true; break; }
                        if (!exists)
                        {
                            QcDimRow c = CloneRow(m);
                            c.IsManual = true;
                            _r.DimRows.Add(c);
                        }
                    }
                }
            }
            catch { }
            ParseDatums();
            if (_t3 != null)
            {
                var sb = new System.Text.StringBuilder();
                sb.Append("图号 ").Append(HeaderVal("图号"));
                sb.Append("　·　名称 ").Append(HeaderVal("名称"));
                sb.Append("　·　材料 ").Append(HeaderVal("材料"));
                sb.Append("　·　类别 ").Append(HeaderVal("类别"));
                sb.Append("　·　版本 ").Append(HeaderVal("版本"));
                _t3.Text = sb.ToString();
            }
            try
            {
                if (_cboCategory != null)
                {
                    string dwg = HeaderVal("图号");
                    string saved = (dwg == "—") ? "" : QcRunner.LoadCategoryFor(dwg);
                    QcRunner.ForcedCategory = saved;
                    int idx = 0;
                    if (!string.IsNullOrEmpty(saved))
                    {
                        idx = 1;
                        for (int i = 1; i < _cboCategory.Items.Count; i++)
                            if (string.Equals(_cboCategory.Items[i].ToString(), saved)) { idx = i; break; }
                    }
                    if (_cboCategory.SelectedIndex != idx) _cboCategory.SelectedIndex = idx;
                }
            }
            catch { }

            BindRows();
            BindTolerance();
            try
            {
                if (_grid != null)
                {
                    foreach (DataGridViewRow row in _grid.Rows)
                    {
                        if (row.Tag != null) { row.Selected = true; break; }
                    }
                }
            }
            catch { }
            try
            {
                if (_tabs != null && _tabs.TabPages.Count > 0) _tabs.SelectedIndex = 0;
                if (_grid != null && _grid.Rows.Count > 0) _grid.FirstDisplayedScrollingRowIndex = 0;
                Activate();
                BringToFront();
            }
            catch { }
            if (_aiLabel != null)
            {
                _aiLabel.Text = "AI 检查：" + (string.IsNullOrEmpty(_r.AiStatus) ? "未执行" : _r.AiStatus);
                _aiLabel.ForeColor = AiColor(_r.AiStatus);
            }
            _busy = false;
            try
            {
                if (_btnCheck != null)
                {
                    _btnCheck.Enabled = true;
                    _btnCheck.Text = "检 查";
                    _btnCheck.BackColor = Brand;
                }
                try { Cursor = Cursors.Default; } catch { }
            }
            catch { }
            if (_statusTime != null)
            {
                string when = DateTime.Now.ToString("HH:mm:ss");
                _statusTime.Text = (_runCount > 0 ? ("第 " + _runCount + " 次 · ") : "") +
                                   "完成 " + when + " · 耗时 " + _r.ElapsedSec.ToString("0.0") + " 秒";
            }
            try
            {
                if (_statusDir != null)
                    _statusDir.Text = string.IsNullOrEmpty(_r.ReportDir) ? "" : "报告目录 " + _r.ReportDir;
                if (_btnDir != null && !string.IsNullOrEmpty(_r.ReportDir))
                {
                    if (_tip == null) _tip = new ToolTip();
                    _tip.SetToolTip(_btnDir, "报告目录：" + _r.ReportDir);
                }
            }
            catch { }
            if (_statusTip != null)
            {
                int w = (_r.Warnings != null) ? _r.Warnings.Count : 0;
                _statusTip.Text = w > 0 ? ("⚠ 提示 " + w + " 条（见报告）") : "";
            }
            // 完成提示闪一下（3 秒后恢复）
            try
            {
                if (_statusTip != null)
                {
                    _statusTip.ForeColor = Color.FromArgb(30, 130, 76);
                    _statusTip.Text = "✅ 第 " + Math.Max(_runCount, 1) + " 次检查完成";
                }
                if (_flashTimer == null)
                {
                    _flashTimer = new System.Windows.Forms.Timer();
                    _flashTimer.Interval = 3000;
                    _flashTimer.Tick += delegate
                    {
                        try
                        {
                            _flashTimer.Stop();
                            if (_statusTip != null)
                            {
                                int w = (_r.Warnings != null) ? _r.Warnings.Count : 0;
                                _statusTip.ForeColor = Color.FromArgb(181, 71, 8);
                                _statusTip.Text = w > 0 ? ("⚠ 提示 " + w + " 条（见报告）") : "";
                            }
                        }
                        catch { }
                    };
                }
                _flashTimer.Stop();
                _flashTimer.Start();
            }
            catch { }

            // 统一文档模式下不再二次套用磁盘归属记忆；归属已经是 Gtol.TargetRowId。
            if (_doc == null) ApplySavedGtolLinks();
            EnsureUnlinkedBanner();
        }

        string HeaderVal(string key)
        {
            try
            {
                if (_r.Header != null && _r.Header.ContainsKey(key) && !string.IsNullOrEmpty(_r.Header[key]))
                    return _r.Header[key];
            }
            catch { }
            return "—";
        }

        /// <summary>用「记忆」把未关联几何公差自动挂回尺寸；空串记忆=不指定（直接忽略）。</summary>
        void ApplySavedGtolLinks()
        {
            try
            {
                if (_r == null) return;
                string dwg = HeaderVal("图号");
                var map = QcRunner.LoadGtolLinks(dwg);
                if (map.Count == 0) return;
                // 2026-10-26：记忆优先——连「自动关联」的也按记忆改挂（解决挂错尺寸）
                try
                {
                    foreach (QcDimRow row0 in _r.DimRows)
                    {
                        if (row0 == null || row0.Gtols == null) continue;
                        for (int k = row0.Gtols.Count - 1; k >= 0; k--)
                        {
                            QcGtolLine g0 = row0.Gtols[k];
                            if (g0 == null || g0.Manual) continue;
                            string key0 = QcRunner.GtolKey(g0.Sym, g0.Val, g0.Dat1, g0.Dat2, g0.Mod, g0.Dat3);
                            string tgt;
                            if (!map.TryGetValue(key0, out tgt)) continue;
                            row0.Gtols.RemoveAt(k);
                            var u0 = new QcUnlinkedGtol();
                            u0.Sym = g0.Sym; u0.Val = g0.Val; u0.Dat1 = g0.Dat1;
                            u0.Dat2 = g0.Dat2; u0.Dat3 = g0.Dat3; u0.Mod = g0.Mod;
                            u0.Label = (GtolSymbols.NameOf(g0.Sym) + " " + g0.Val).Trim();
                            u0.View = row0.Content;
                            u0.Ignored = string.IsNullOrEmpty(tgt);
                            _r.UnlinkedGtols.Add(u0);
                            RefreshRowCalc(row0);
                        }
                    }
                }
                catch { }
                if (_r.UnlinkedGtols == null || _r.UnlinkedGtols.Count == 0) return;

                var keep = new List<QcUnlinkedGtol>();
                var touched = new List<QcDimRow>();
                bool changed = false;
                foreach (QcUnlinkedGtol u in new List<QcUnlinkedGtol>(_r.UnlinkedGtols))
                {
                    string key = QcRunner.GtolKey(u.Sym, u.Val, u.Dat1, u.Dat2, u.Mod, u.Dat3);
                    string dimTxt;
                    if (!map.TryGetValue(key, out dimTxt)) { keep.Add(u); continue; }
                    changed = true;
                    if (string.IsNullOrEmpty(dimTxt))
                    {
                        u.Ignored = true;      // 记住"不指定"：仍显示，允许再改
                        keep.Add(u);
                        continue;
                    }
                    QcDimRow target = null;
                    foreach (QcDimRow r in _r.DimRows)
                        if (r != null && r.Content == dimTxt) { target = r; break; }
                    if (target == null)
                    {
                        // 记忆里是自定义尺寸 → 新建一行承接
                        target = new QcDimRow();
                        target.Kind = "自定义";
                        target.Content = dimTxt;
                        target.HasTol = true;
                        target.Note = "人工填写尺寸（用户指定归属）";
                        _r.DimRows.Insert(0, target);   // 自定义行放最前
                    }
                    var line = new QcGtolLine();
                    line.Sym = u.Sym; line.Val = u.Val; line.Dat1 = u.Dat1;
                    line.Dat2 = u.Dat2; line.Dat3 = u.Dat3; line.Mod = u.Mod;
                    double v;
                    string num = System.Text.RegularExpressions.Regex.Match(
                        u.Val ?? "", @"[0-9]+(?:\.[0-9]+)?").Value;
                    if (double.TryParse(num, System.Globalization.NumberStyles.Float,
                            System.Globalization.CultureInfo.InvariantCulture, out v)) line.ValueMm = v;
                    line.Manual = true;
                    line.Reason = "人工指定归属（已记忆）";
                    // v186：同签名已存在则不重复添加
                    try
                    {
                        string mk = QcRunner.GtolKey((line.Sym ?? "").Trim(), (line.Val ?? "").Trim(),
                            (line.Dat1 ?? "").Trim().ToUpperInvariant(), (line.Dat2 ?? "").Trim().ToUpperInvariant(),
                            (line.Mod ?? "").Trim(), (line.Dat3 ?? "").Trim().ToUpperInvariant());
                        bool dup = false;
                        foreach (QcGtolLine gg in target.Gtols)
                        {
                            if (gg == null) continue;
                            if (QcRunner.GtolKey((gg.Sym ?? "").Trim(), (gg.Val ?? "").Trim(),
                                    (gg.Dat1 ?? "").Trim().ToUpperInvariant(), (gg.Dat2 ?? "").Trim().ToUpperInvariant(),
                                    (gg.Mod ?? "").Trim(), (gg.Dat3 ?? "").Trim().ToUpperInvariant()) == mk) { dup = true; break; }
                        }
                        if (dup) continue;
                    }
                    catch { }
                    // 先把它从其它尺寸行移除（移动而不是复制）
                    try
                    {
                        string _mk = QcRunner.GtolKey((line.Sym ?? "").Trim(), (line.Val ?? "").Trim(),
                            (line.Dat1 ?? "").Trim().ToUpperInvariant(), (line.Dat2 ?? "").Trim().ToUpperInvariant(),
                            (line.Mod ?? "").Trim(), (line.Dat3 ?? "").Trim().ToUpperInvariant());
                        foreach (QcDimRow _rr in _r.DimRows)
                        {
                            if (_rr == null || object.ReferenceEquals(_rr, target) || _rr.Gtols == null) continue;
                            _rr.Gtols.RemoveAll(delegate(QcGtolLine _gg)
                            {
                                return _gg != null && QcRunner.GtolKey((_gg.Sym ?? "").Trim(), (_gg.Val ?? "").Trim(),
                                    (_gg.Dat1 ?? "").Trim().ToUpperInvariant(), (_gg.Dat2 ?? "").Trim().ToUpperInvariant(),
                                    (_gg.Mod ?? "").Trim(), (_gg.Dat3 ?? "").Trim().ToUpperInvariant()) == _mk;
                            });
                            QcRunner.NormalizeRowGtols(_rr);
                        }
                    }
                    catch { }
                    target.Gtols.Add(line);
                    if (!touched.Contains(target)) touched.Add(target);
                }
                if (!changed) return;
                foreach (QcDimRow row in touched)
                {
                    try { QcRunner.NormalizeRowGtols(row); } catch { }
                    bool env = (row.Modifier == "Ⓔ");
                    row.SigmaT = GtolRules.Recompute(row.Gtols, env);
                    row.MmsShaft = row.Nominal + row.TolUp;
                    row.MmsHole = row.Nominal + row.TolLow;
                    row.MmvcShaft = Math.Round(row.MmsShaft + row.SigmaT, 4);
                    row.MmvcHole = Math.Round(row.MmsHole - row.SigmaT, 4);
                    row.Note = ComposeGeomNote(row);
            try
            {
                if (row.Gtols != null && row.Gtols.Count > 0 && string.IsNullOrEmpty(row.Note))
                {
                    var segs = new List<string>();
                    foreach (QcGtolLine gg in row.Gtols)
                    {
                        if (gg == null) continue;
                        string kind = string.IsNullOrEmpty(gg.MeasuredFeature) ? "表面" : gg.MeasuredFeature;
                        string tgt = string.IsNullOrEmpty(row.Content) ? "" : (" → " + row.Content);
                        segs.Add("指向：" + kind + tgt);
                    }
                    if (segs.Count > 0) row.Note = string.Join("；", segs.ToArray());
                }
            }
            catch { }
                }
                _r.UnlinkedGtols.Clear();
                _r.UnlinkedGtols.AddRange(keep);
                BindTolerance();
            }
            catch { }
        }

        /// <summary>检查完成后：还有未关联的，主动弹出一次「指定归属」。</summary>
        void PromptUnlinkedIfAny()
        {
            try
            {
                if (SuppressAutoPrompt) return;
                if (_r == null || _r.UnlinkedGtols == null || _r.UnlinkedGtols.Count == 0) return;
                EnsureAllGtols();
                using (var dlg = new QcGtolLinkForm(_r, _allGtols))
                {
                    _childDlg = dlg;
                    try
                    {
                        if (dlg.ShowDialog(this) == DialogResult.OK)
                            ApplyLinkDialog(dlg);
                    }
                    finally { _childDlg = null; }
                }
            }
            catch { }
        }

        void EnsureUnlinkedBanner()
        {
            // 2026-10-26：不再插黄色提醒条（会和页面说明重叠）；改为
            //   · 尺寸/公差页顶部说明高亮显示未关联条数
            //   · 状态栏「指定归属 (N)」按钮点击后才弹窗
            int n = (_r.UnlinkedGtols == null) ? 0 : _r.UnlinkedGtols.Count;
            int nManual = 0;   // 手动指定过的
            int nAuto = 0;     // 自动关联的
            int nSingle = 0;   // 只有单条 GtolSym、没有 Gtols 列表的
            try
            {
                foreach (QcDimRow rr in (_r.DimRows ?? new List<QcDimRow>()))
                {
                    if (rr == null) continue;
                    bool hasList = (rr.Gtols != null && rr.Gtols.Count > 0);
                    if (hasList)
                    {
                        foreach (QcGtolLine gg in rr.Gtols)
                        {
                            if (gg == null) continue;
                            if (gg.Manual) nManual++;
                            else nAuto++;
                        }
                    }
                    else if (!string.IsNullOrEmpty(rr.GtolSym))
                    {
                        nSingle++;   // 避免与 Gtols 重复计数
                    }
                }
            }
            catch { }
            int nTotal = n + nManual + nAuto + nSingle;   // 与弹窗行数同一口径
            try
            {
                if (_btnLink != null)
                {
                    bool canEdit = (nTotal > 0);
                    _btnLink.Enabled = canEdit;
                    _btnLink.Text = canEdit ? ("指定归属 (" + nTotal + ")") : "指定归属";
                    _btnLink.ForeColor = canEdit ? Color.FromArgb(181, 71, 8) : SystemColors.ControlText;
                }
            }
            catch { }
            try
            {
                if (_tolTip != null)
                {
                    int nReal = 0;
                    if (_r.UnlinkedGtols != null)
                        foreach (QcUnlinkedGtol uu in _r.UnlinkedGtols) if (!uu.Ignored) nReal++;
                    if (nReal > 0)
                    {
                        _tolTip.ForeColor = Color.FromArgb(181, 71, 8);
                        _tolTip.Text = "⚠ 有 " + nReal + " 条几何公差未关联到尺寸（不参与最大作用边界计算）——"
                                     + "点右侧「指定归属」按钮指定。\r\n" + _tolTipDefault;
                    }
                    else
                    {
                        _tolTip.ForeColor = Ink;
                        _tolTip.Text = _tolTipDefault;
                    }
                }
            }
            catch { }
        }

        /// <summary>按弹窗结果重建人工归属：先撤销旧的，再按新选择挂回并重写记忆。</summary>
        public void ApplyUnlinkedAssignments(List<int> assignment, bool asIndependent, Control banner)
        {
            // 旧签名保留（内部转调），实际逻辑见 ApplyLinkDialog
        }

        public void ApplyLinkDialog(QcGtolLinkForm dlg)
        {
            if (_r == null || dlg == null) return;
            if (_doc != null)
            {
                try
                {
                    // 事务式“移动”：只修改现有 GtolId 的 TargetRowId，绝不复制同一条公差。
                    _doc.ApplyAssignments(_r, dlg.Items, dlg.Assignment, dlg.Custom);
                    _doc.ApplyToReport(_r);
                    QcRunner.DocumentSnapshotJson = _doc.ToSnapshotJson();
                    ParseDatums();
                    BindTolerance();
                    EnsureUnlinkedBanner();
                    _allGtols.Clear();
                    _allGtols.AddRange(_doc.BuildAllGtolItems());
                    ShowDetail();
                    if (_statusTip != null) _statusTip.Text = "归属已保存，尺寸表与几何归属表已统一（点「检查」更新审核结果）";
                }
                catch (Exception ex)
                {
                    MessageBox.Show(this, "指定归属失败，修改已回滚：" + ex.Message, "工程图QC");
                }
                return;
            }
            try
            {
                string dwg = HeaderVal("图号");
                // 1) 撤销弹窗里涉及的所有几何公差（人工指定 + 自动关联），全部回到未关联
                var dialogKeys = new HashSet<string>();
                foreach (QcUnlinkedGtol it0 in dlg.Items)
                {
                    if (it0 == null) continue;
                    dialogKeys.Add(QcRunner.GtolKey(it0.Sym, it0.Val, it0.Dat1, it0.Dat2, it0.Mod, it0.Dat3));
                    if (it0.Members != null)
                        foreach (QcGtolLine mm in it0.Members)
                            if (mm != null) dialogKeys.Add(QcRunner.GtolKey(mm.Sym, mm.Val, mm.Dat1, mm.Dat2, mm.Mod, mm.Dat3));
                }
                var touched = new List<QcDimRow>();
                foreach (QcDimRow row in _r.DimRows)
                {
                    if (row == null || row.Gtols == null) continue;
                    int removed = row.Gtols.RemoveAll(g => g != null &&
                        (g.Manual || dialogKeys.Contains(QcRunner.GtolKey(g.Sym, g.Val, g.Dat1, g.Dat2, g.Mod, g.Dat3))));
                    if (removed > 0) touched.Add(row);
                }
                foreach (QcDimRow row in touched)
                {
                    try { QcRunner.NormalizeRowGtols(row); } catch { }
                    RefreshRowCalc(row);
                }
                try { RefreshDatumNotesLocally(); } catch { }
                BindTolerance();
                // 2) 记忆清空重写
                QcRunner.RemoveGtolLinks(dwg);
                // 3) 按弹窗结果重新挂
                var keep = new List<QcUnlinkedGtol>();
                // v195：同签名可能有多条（如 3.5 和 5 上各一条轮廓度），不能互相“移动删除”
                var keyCount = new Dictionary<string, int>();
                foreach (QcUnlinkedGtol _itc in dlg.Items)
                {
                    if (_itc == null) continue;
                    string _kc = QcRunner.GtolKey(_itc.Sym, _itc.Val, _itc.Dat1, _itc.Dat2, _itc.Mod, _itc.Dat3);
                    int _cc; keyCount.TryGetValue(_kc, out _cc); keyCount[_kc] = _cc + 1;
                }
                int n = Math.Min(dlg.Assignment.Count, dlg.Items.Count);
                for (int i = 0; i < n; i++)
                {
                    QcUnlinkedGtol u = dlg.Items[i];
                    if (u == null) continue;
                    string key = QcRunner.GtolKey(u.Sym, u.Val, u.Dat1, u.Dat2, u.Mod, u.Dat3);
                    int di = dlg.Assignment[i];
                    string customTxt = (dlg.Custom != null && i < dlg.Custom.Count) ? (dlg.Custom[i] ?? "") : "";
                    if (di < 0 && !string.IsNullOrEmpty(customTxt))
                    {
                        // 人工填写的自定义尺寸：没有就新建一行
                        QcDimRow crow = null;
                        foreach (QcDimRow rr in _r.DimRows)
                            if (rr != null && rr.Content == customTxt) { crow = rr; break; }
                        if (crow == null)
                        {
                            crow = new QcDimRow();
                            crow.Kind = "自定义";
                            crow.Content = customTxt;
                            crow.HasTol = true;
                            crow.IsOutline = false;
                            crow.Note = "人工填写尺寸（用户指定归属）";
                            _r.DimRows.Insert(0, crow);   // 自定义行放最前，方便看到
                        }
                        di = _r.DimRows.IndexOf(crow);
                    }
                    if (di >= 0 && di < _r.DimRows.Count)
                    {
                        QcDimRow row = _r.DimRows[di];
                        var line = new QcGtolLine();
                        line.Sym = u.Sym; line.Val = u.Val; line.Dat1 = u.Dat1;
                        line.Dat2 = u.Dat2; line.Dat3 = u.Dat3; line.Mod = u.Mod;
                        line.RawId = u.RawId; line.GroupId = u.GroupId; line.MeasuredFeature = u.MeasuredFeature;
                        line.FrameOrder = u.FrameOrder; line.FrameCount = u.FrameCount;
                        line.FrameType = u.FrameType; line.FrameReason = u.FrameReason; line.ZoneType = u.ZoneType;
                        double v;
                        string num = System.Text.RegularExpressions.Regex.Match(
                            u.Val ?? "", @"[0-9]+(?:\.[0-9]+)?").Value;
                        if (double.TryParse(num, System.Globalization.NumberStyles.Float,
                                System.Globalization.CultureInfo.InvariantCulture, out v)) line.ValueMm = v;
                        line.Manual = true;
                        line.Reason = "人工指定归属";
                        try
                        {
                            string mk = QcRunner.GtolKey((line.Sym ?? "").Trim(), (line.Val ?? "").Trim(),
                                (line.Dat1 ?? "").Trim().ToUpperInvariant(), (line.Dat2 ?? "").Trim().ToUpperInvariant(),
                                (line.Mod ?? "").Trim(), (line.Dat3 ?? "").Trim().ToUpperInvariant());
                            bool dup = false;
                            foreach (QcGtolLine gg in row.Gtols)
                            {
                                if (gg == null) continue;
                                if (QcRunner.GtolKey((gg.Sym ?? "").Trim(), (gg.Val ?? "").Trim(),
                                        (gg.Dat1 ?? "").Trim().ToUpperInvariant(), (gg.Dat2 ?? "").Trim().ToUpperInvariant(),
                                        (gg.Mod ?? "").Trim(), (gg.Dat3 ?? "").Trim().ToUpperInvariant()) == mk) { dup = true; break; }
                            }
                            if (dup) continue;
                        }
                        catch { }
                        int _cnt = 1;
                        try { keyCount.TryGetValue(key, out _cnt); } catch { }
                        if (_cnt <= 1)
                        {
                            try
                            {
                                string _mk = QcRunner.GtolKey((line.Sym ?? "").Trim(), (line.Val ?? "").Trim(),
                                    (line.Dat1 ?? "").Trim().ToUpperInvariant(), (line.Dat2 ?? "").Trim().ToUpperInvariant(),
                                    (line.Mod ?? "").Trim(), (line.Dat3 ?? "").Trim().ToUpperInvariant());
                                foreach (QcDimRow _rr in _r.DimRows)
                                {
                                    if (_rr == null || object.ReferenceEquals(_rr, row) || _rr.Gtols == null) continue;
                                    _rr.Gtols.RemoveAll(delegate(QcGtolLine _gg)
                                    {
                                        return _gg != null && QcRunner.GtolKey((_gg.Sym ?? "").Trim(), (_gg.Val ?? "").Trim(),
                                            (_gg.Dat1 ?? "").Trim().ToUpperInvariant(), (_gg.Dat2 ?? "").Trim().ToUpperInvariant(),
                                            (_gg.Mod ?? "").Trim(), (_gg.Dat3 ?? "").Trim().ToUpperInvariant()) == _mk;
                                    });
                                    QcRunner.NormalizeRowGtols(_rr);
                                }
                            }
                            catch { }
                        }
                        row.Gtols.Add(line);
                        if (u.Members != null && u.Members.Count > 0)
                        {
                            foreach (QcGtolLine mm in u.Members)
                            {
                                if (mm == null) continue;
                                string mk2 = QcRunner.GtolKey((mm.Sym ?? "").Trim(), (mm.Val ?? "").Trim(),
                                    (mm.Dat1 ?? "").Trim().ToUpperInvariant(), (mm.Dat2 ?? "").Trim().ToUpperInvariant(),
                                    (mm.Mod ?? "").Trim(), (mm.Dat3 ?? "").Trim().ToUpperInvariant());
                                bool dup2 = false;
                                foreach (QcGtolLine gg in row.Gtols)
                                    if (gg != null && QcRunner.GtolKey((gg.Sym ?? "").Trim(), (gg.Val ?? "").Trim(),
                                        (gg.Dat1 ?? "").Trim().ToUpperInvariant(), (gg.Dat2 ?? "").Trim().ToUpperInvariant(),
                                        (gg.Mod ?? "").Trim(), (gg.Dat3 ?? "").Trim().ToUpperInvariant()) == mk2) { dup2 = true; break; }
                                if (dup2) continue;
                                var l2 = new QcGtolLine();
                                l2.Sym = mm.Sym; l2.Val = mm.Val; l2.Dat1 = mm.Dat1; l2.Dat2 = mm.Dat2; l2.Dat3 = mm.Dat3; l2.Mod = mm.Mod;
                                l2.RawId = mm.RawId; l2.GroupId = mm.GroupId; l2.MeasuredFeature = mm.MeasuredFeature;
                                l2.FrameOrder = mm.FrameOrder; l2.FrameCount = mm.FrameCount;
                                l2.FrameType = mm.FrameType; l2.FrameReason = mm.FrameReason; l2.ZoneType = mm.ZoneType;
                                l2.Manual = true; l2.Reason = "人工指定归属";
                                double vv2; if (double.TryParse(mm.Val, out vv2)) l2.ValueMm = vv2;
                                row.Gtols.Add(l2);
                            }
                        }
                        if (!touched.Contains(row)) touched.Add(row);
                        QcRunner.SaveGtolLink(dwg, key, row.Content);
                    }
                    else
                    {
                        u.Ignored = true;
                        keep.Add(u);
                        QcRunner.SaveGtolLink(dwg, key, "");   // 记住"不指定"
                    }
                }
                foreach (QcDimRow row in touched) RefreshRowCalc(row);
                _r.UnlinkedGtols.Clear();
                _r.UnlinkedGtols.AddRange(keep);
                // 人工指定后，「自动关联不确定」提示已失效 → 清掉并刷新统计/列表
                try
                {
                    _r.Items.RemoveAll(x => x != null && x.RuleId == "GTOL-LINK-UNCERTAIN");
                    int crit = 0, major = 0, minor = 0;
                    foreach (QcItem x in _r.Items)
                    {
                        if (x == null) continue;
                        if (x.Severity == "critical") crit++;
                        else if (x.Severity == "major") major++;
                        else if (x.Severity == "minor") minor++;
                    }
                    if (_cardNums[0] != null) _cardNums[0].Text = crit.ToString();
                    if (_cardNums[1] != null) _cardNums[1].Text = major.ToString();
                    if (_cardNums[2] != null) _cardNums[2].Text = minor.ToString();
                    if (_cardNums[3] != null) _cardNums[3].Text = (crit + major + minor).ToString();
                    BindRows();
                }
                catch { }
                BindTolerance();
                EnsureUnlinkedBanner();
                try { _allGtols.Clear(); } catch { }
                ShowDetail();
                // v189：归属/基准保存后不自动检查；等用户点「检查」
                if (_statusTip != null) _statusTip.Text = "已保存（点「检查」更新审核结果）";
            }
            catch (Exception ex)
            {
                MessageBox.Show(this, "指定归属失败：" + ex.Message, "工程图QC");
            }
        }

        static string ComposeGeomNote(QcDimRow row)
        {
            // 备注只保留“基准指向 / 轮廓 / 孔组”等信息，不重复几何公差本身
            var keep = new List<string>();
            foreach (string seg in (row.Note ?? "").Split(new char[] { '；' }, StringSplitOptions.RemoveEmptyEntries))
            {
                string x = seg.Trim();
                if (x.Length == 0) continue;
                if (x.StartsWith("几何 ") || x.StartsWith("几何公差")) continue;
                keep.Add(x);
            }
            return string.Join("；", keep.ToArray());
        }

        static void RefreshRowCalc(QcDimRow row)
        {
            bool env = (row.Modifier == "Ⓔ");
            row.SigmaT = GtolRules.Recompute(row.Gtols, env);
            row.MmsShaft = row.Nominal + row.TolUp;
            row.MmsHole = row.Nominal + row.TolLow;
            row.MmvcShaft = Math.Round(row.MmsShaft + row.SigmaT, 4);
            row.MmvcHole = Math.Round(row.MmsHole - row.SigmaT, 4);
            row.Note = ComposeGeomNote(row);
        }

        static Color AiColor(string status)
        {
            string s = status ?? "";
            if (s.Contains("失败")) return Color.FromArgb(192, 57, 43);
            if (s.Contains("未配置") || s.Contains("未执行")) return Muted;
            return Color.FromArgb(22, 122, 60);
        }

        /// <summary>按文本关键词补上相关明细（几何公差 / 粗糙度 / 尺寸统计 / 材料表面处理）。</summary>
        void AppendRelatedInfo(System.Text.StringBuilder sb, string text)
        {
            try
            {
                string t = text ?? "";
                bool isSummary = t.Contains("图上有") || (t.Contains("尺寸") && t.Contains("个，其中"));
                if (!isSummary) return;          // 技术要求/AI/材料等行不追加任何明细
                if (t.Contains("几何公差") || t.Contains("形位"))
                {
                    int n = 0;
                    sb.AppendLine();
                    sb.AppendLine("———— 全部几何公差 ————");
                    foreach (QcDimRow r in (_r.DimRows ?? new List<QcDimRow>()))
                    {
                        if (r == null || r.Gtols == null) continue;
                        foreach (QcGtolLine g in r.Gtols)
                        {
                            n++;
                            sb.AppendLine("· " + (string.IsNullOrEmpty(r.Content) ? "尺寸" : r.Content) +
                                "　" + g.Sym + g.Val + (string.IsNullOrEmpty(g.Mod) ? "" : g.Mod) +
                                (string.IsNullOrEmpty(g.Dat1) ? "" : " | " + g.Dat1) +
                                (string.IsNullOrEmpty(g.Dat2) ? "" : " | " + g.Dat2) +
                                (string.IsNullOrEmpty(g.Dat3) ? "" : " | " + g.Dat3) +
                                "　(" + GtolSymbols.NameOf(g.Sym) + ")" +
                                (g.Include ? "　计入作用边界" : "　不计入") +
                                (g.Manual ? "　（人工指定）" : "") +
                                (string.IsNullOrEmpty(g.Reason) ? "" : "　" + g.Reason));
                        }
                    }
                    foreach (QcUnlinkedGtol u in (_r.UnlinkedGtols ?? new List<QcUnlinkedGtol>()))
                    {
                        n++;
                        sb.AppendLine("· (未关联) " + u.Sym + u.Val +
                            (string.IsNullOrEmpty(u.Mod) ? "" : u.Mod) +
                            (string.IsNullOrEmpty(u.Dat1) ? "" : " | " + u.Dat1) +
                            (string.IsNullOrEmpty(u.Dat2) ? "" : " | " + u.Dat2) +
                            (string.IsNullOrEmpty(u.Dat3) ? "" : " | " + u.Dat3) +
                            "　(" + GtolSymbols.NameOf(u.Sym) + ")" +
                            (string.IsNullOrEmpty(u.View) ? "" : "　视图 " + u.View) +
                            (u.Ignored ? "　（人工指定：不参与计算）" : ""));
                    }
                    if (n == 0) sb.AppendLine("（无）");
                    else if ((_r.UnlinkedGtols != null) && _r.UnlinkedGtols.Count > 0)
                        sb.AppendLine("共 " + n + " 条；其中 " + _r.UnlinkedGtols.Count +
                                      " 条未关联，可点「尺寸/公差」页顶部「指定归属」按钮指定。");
                    else sb.AppendLine("共 " + n + " 条（均已关联到尺寸）。");
                }
                if (t.Contains("粗糙度"))
                {
                    sb.AppendLine();
                    sb.AppendLine("———— 粗糙度标注 ————");
                    int n = 0;
                    foreach (QcDimRow r in (_r.DimRows ?? new List<QcDimRow>()))
                    {
                        if (r == null || !(r.Kind ?? "").Contains("粗糙度")) continue;
                        n++;
                        sb.AppendLine("· " + r.Content +
                            (string.IsNullOrEmpty(r.Capability) ? "" : "　" + r.Capability) +
                            (string.IsNullOrEmpty(r.Note) ? "" : "　" + r.Note));
                    }
                    sb.AppendLine(n == 0 ? "（无）" : "共 " + n + " 处（同时列在「尺寸/公差」页）。");
                }
                if (t.Contains("材料") || t.Contains("表面处理"))
                {
                    sb.AppendLine();
                    sb.AppendLine("———— 材料 / 表面处理 ————");
                    string sf = HeaderVal("表面处理");
                    if (sf == "—")
                    {
                        string bz = HeaderVal("备注");
                        if (bz != "—" && System.Text.RegularExpressions.Regex.IsMatch(
                                bz, "镀|氧化|阳极|钝化|喷涂|喷塑|磷化|发黑|处理"))
                            sf = bz;
                        else sf = "（未注明）";
                    }
                    sb.AppendLine("· 材料：" + HeaderVal("材料") + "　表面处理：" + sf);
                }
            }
            catch { }
        }

        /// <summary>右栏：按分区输出「整段结构化审核结果」（用户要求的表格化阅读方式）。</summary>
        void ShowSectionDetail(string section, DataGridViewRow selRow)
        {
            var sb = new System.Text.StringBuilder();
            const string SEP = "────────────────────────────";

            if (section == "技术要求")
            {
                sb.AppendLine("一、技术要求逐条审核");
                sb.AppendLine("（只列需要修改的条款；通过的不显示）");
                sb.AppendLine(SEP);
                int n = 0;
                var seenT = new HashSet<string>();
                foreach (QcItem it in (_r.Items ?? new List<QcItem>()))
                {
                    if (it == null || it.RuleId != "AI-TECH-ITEM") continue;
                    string body = CleanText(it.Detail);
                    string sugg = CleanText(it.Suggestion);
                    string key = body + "|" + sugg;
                    if (!seenT.Add(key)) continue;      // 去重
                    n++;
                    sb.AppendLine("【" + (it.Title ?? "") + "】");
                    sb.AppendLine("  审核结论：" + body);
                    sb.AppendLine("  风险等级：" + LevelText(it.Severity));
                    sb.AppendLine("  修改建议：" + (string.IsNullOrEmpty(sugg) ? "—" : sugg));
                    sb.AppendLine();
                }
                if (n == 0) sb.AppendLine("（本次 AI 未提出需要修改的技术要求条款）");
            }
            else
            {
                sb.AppendLine("二、尺寸/几何公差判断（尺寸公差 + 几何公差）");
                sb.AppendLine(SEP);
                int n = 0;
                var seenG = new HashSet<string>();
                foreach (QcItem it in (_r.Items ?? new List<QcItem>()))
                {
                    if (it == null || it.RuleId != "AI-GEOM-ITEM") continue;
                    string head = it.Title ?? "";
                    string brief = "";
                    int p = head.IndexOf("：");
                    if (p > 0) { brief = head.Substring(p + 1); head = head.Substring(0, p); }
                    string itg = "";
                    try
                    {
                        var m = System.Text.RegularExpressions.Regex.Match(head, @"[（(]\s*(≈?IT\d+)\s*[)）]");
                        if (m.Success) { itg = m.Groups[1].Value; head = head.Replace(m.Value, "").Trim(); }
                        else
                        {
                            var m2 = System.Text.RegularExpressions.Regex.Match(brief, @"(≈?IT\d+)");
                            if (m2.Success) itg = m2.Groups[1].Value;
                        }
                    }
                    catch { }
                    string body = CleanText(it.Detail);
                    string sugg = CleanText(it.Suggestion);
                    string key = head + "|" + body + "|" + sugg;
                    if (!seenG.Add(key)) continue;      // 去重
                    n++;
                    sb.AppendLine(n + "）" + head);
                    sb.AppendLine("  IT等级：" + (string.IsNullOrEmpty(itg) ? "—" : itg) +
                                  "　　风险等级：" + LevelText(it.Severity));
                    sb.AppendLine("  审核结果：" + body);
                    if (!string.IsNullOrEmpty(sugg)) sb.AppendLine("  修改建议：" + sugg);
                    sb.AppendLine();
                }
                if (n == 0) sb.AppendLine("（本次 AI 未提出公差项）");
                sb.AppendLine(SEP);
                sb.AppendLine("说明：公差项仅作为技术要求审核的辅助判断，不单独算作技术要求问题。");
            }
            _detail.Text = sb.ToString();
            try { _detail.Refresh(); } catch { }
        }

        /// <summary>清理文本：去掉重复前缀、压实空白，便于整齐排版。</summary>
        static string CleanText(string t)
        {
            if (string.IsNullOrEmpty(t)) return "";
            string x = t.Replace("\r", " ").Replace("\n", "；").Trim();
            x = System.Text.RegularExpressions.Regex.Replace(x, @"^[；;、\s]+", "");
            x = System.Text.RegularExpressions.Regex.Replace(x, @"^(风险|合理性|与技术要求/尺寸/材料的一致性)[：:]", "");
            x = System.Text.RegularExpressions.Regex.Replace(x, @"；\s*；", "；");
            x = System.Text.RegularExpressions.Regex.Replace(x, @"\s{2,}", " ");
            return x.Trim();
        }

        static string LevelText(string sev)
        {
            if (sev == "critical") return "严重";
            if (sev == "major") return "一般";
            if (sev == "minor") return "轻微";
            if (sev == "info" || string.IsNullOrEmpty(sev)) return "—";
            return sev;
        }

        void ShowDetail()
        {
            if (_detail == null || _grid == null) return;
            if (_grid.SelectedRows.Count == 0)
            {
                _detail.Text = "点击左侧任意一行，这里显示该行的完整说明。\r\n\r\n" +
                    "· 问题行：判定依据 / 标准 / 建议\r\n" +
                    "· 「图上有 N 处几何公差」：全部几何公差明细（含未关联）\r\n" +
                    "· 「粗糙度」：每条 Ra 与加工能力\r\n" +
                    "· 在「尺寸/公差」页可指定未关联几何公差的归属";
                return;
            }
            QcItem it = _grid.SelectedRows[0].Tag as QcItem;
            if (it == null && _grid.SelectedRows[0].Tag is QcPassed)
            {
                // 2026-10-26：通过项/结论可能很长 → 完整显示在右侧
                var p = (QcPassed)_grid.SelectedRows[0].Tag;
                var sb0 = new System.Text.StringBuilder();
                sb0.AppendLine("等级：通过");
                sb0.AppendLine();
                sb0.AppendLine("内容：" + (p.Text ?? ""));
                sb0.AppendLine();
                sb0.AppendLine("审核结果：该项检查通过，无需修改。");
                _detail.Text = sb0.ToString();
                try { _detail.Refresh(); } catch { }
                return;
            }
            if (it == null) { _detail.Text = "（这是分组标题行，无详细说明）"; return; }
            var sb = new System.Text.StringBuilder();
            // AI 逐项结果：整齐输出「该项」的完整信息（尺寸/几何公差 或 技术要求）
            if (it.RuleId == "AI-GEOM-ITEM" || it.RuleId == "AI-TECH-ITEM")
            {
                string ttl = it.Title ?? "";
                if (it.RuleId == "AI-GEOM-ITEM")
                    ttl = ttl.Replace("尺寸/几何公差 ", "").Replace("几何公差 ", "");
                string head = ttl, brief = "";
                int p = ttl.IndexOf("：");
                if (p > 0) { head = ttl.Substring(0, p); brief = ttl.Substring(p + 1); }
                string itg = "";
                if (it.RuleId == "AI-GEOM-ITEM")
                {
                    try
                    {
                        var mm = System.Text.RegularExpressions.Regex.Match(head, @"[（(]\s*(≈?IT\d+)\s*[)）]");
                        if (mm.Success) itg = mm.Groups[1].Value;
                        else
                        {
                            var mm2 = System.Text.RegularExpressions.Regex.Match(brief, @"(≈?IT\d+)");
                            if (mm2.Success) itg = mm2.Groups[1].Value;
                        }
                    }
                    catch { }
                }
                sb.AppendLine("等级：" + (it.SeverityText ?? "—"));
                sb.AppendLine();
                if (it.RuleId == "AI-GEOM-ITEM")
                    sb.AppendLine("尺寸/几何公差：" + head);
                else
                {
                    string original = (!string.IsNullOrEmpty(it.Evidence) &&
                        (it.Location ?? "").StartsWith("TECH-", StringComparison.OrdinalIgnoreCase))
                        ? it.Evidence
                        : (brief.Length > 0 ? ("第" + head.Replace("技术要求第", "").Replace("条", "条 ") + brief)
                                            : head.Replace("技术要求第", "第"));
                    sb.AppendLine("技术要求：" + original);
                }
                sb.AppendLine();
                if (!string.IsNullOrEmpty(itg)) sb.AppendLine("IT等级：" + itg);
                string detailText = CleanText(it.Detail);
                int evAt = detailText.IndexOf("依据：", StringComparison.Ordinal);
                if (evAt >= 0) detailText = detailText.Substring(0, evAt).Trim('　', ' ', '；');
                sb.AppendLine("审核结果：" + detailText);
                string evidence = it.Evidence ?? "";
                if (!string.IsNullOrEmpty(it.Location) && it.Location != "OVERALL")
                    evidence = ("关联位置 " + it.Location + (string.IsNullOrEmpty(evidence) ? "" : "；" + evidence));
                if (!string.IsNullOrEmpty(it.Standard) && it.Standard != "AI语义审核")
                    evidence += (string.IsNullOrEmpty(evidence) ? "" : "；") + it.Standard;
                if (!string.IsNullOrEmpty(evidence))
                {
                    sb.AppendLine();
                    sb.AppendLine("依据：" + evidence);
                }
                if (!string.IsNullOrEmpty(it.Suggestion))
                {
                    sb.AppendLine();
                    sb.AppendLine("建议：" + CleanText(it.Suggestion));
                }
                _detail.Text = sb.ToString();
                try { _detail.Refresh(); } catch { }
                return;
            }
            // 普通问题行（图框/尺寸/表面处理等）：同样按 等级/内容/审核结果 显示
            sb.AppendLine("等级：" + (it.SeverityText ?? "—"));
            sb.AppendLine();
            sb.AppendLine("内容：" + (it.Title ?? ""));
            sb.AppendLine();
            sb.AppendLine("审核结果：" + CleanText(it.Detail));
            if (!string.IsNullOrEmpty(it.Suggestion))
            {
                sb.AppendLine();
                sb.AppendLine("建议：" + CleanText(it.Suggestion));
            }
            if (!string.IsNullOrEmpty(it.Standard))
            {
                sb.AppendLine();
                sb.AppendLine("依据：" + it.Standard);
            }
            _detail.Text = sb.ToString();
            try { _detail.Refresh(); } catch { }
            return;
        }

        // ---------------------------------------------------------------- 状态/演示
        /// <summary>自测用：选中包含指定文字的行</summary>
        public string DemoSelectedText = "";
        public bool DemoSelectRow(string contains)
        {
            try
            {
                if (_grid == null) return false;
                foreach (DataGridViewRow row in _grid.Rows)
                {
                    string txt = (row.Cells.Count > 1) ? (row.Cells[1].Value ?? "").ToString() : "";
                    if (txt.Contains(contains))
                    {
                        row.Selected = true;
                        _grid.CurrentCell = row.Cells[1];
                        DemoSelectedText = txt;
                        ShowDetail();
                        return true;
                    }
                }
            }
            catch { }
            return false;
        }


        public void SetBusy(string text)
        {
            _busy = true;
            _runCount++;
            _checkStart = DateTime.Now;
            try
            {
                if (_btnCheck != null)
                {
                    _btnCheck.Enabled = false;
                    _btnCheck.Text = "检查中…";
                    _btnCheck.BackColor = Color.FromArgb(120, 136, 158);
                }
                if (_statusTime != null)
                    _statusTime.Text = "第 " + _runCount + " 次检查中… " + _checkStart.ToString("HH:mm:ss");
                if (_statusTip != null)
                {
                    _statusTip.ForeColor = Color.FromArgb(181, 71, 8);
                    _statusTip.Text = "⏳ 正在检查（第 " + _runCount + " 次）";
                }
                if (_aiLabel != null)
                {
                    _aiLabel.Text = "检查中…（第 " + _runCount + " 次）" + (string.IsNullOrEmpty(text) ? "" : "　" + text);
                    _aiLabel.ForeColor = Color.FromArgb(181, 71, 8);
                }
                try { Cursor = Cursors.AppStarting; } catch { }
                Application.DoEvents();
            }
            catch { }
        }

        /// <summary>检查未完成时恢复可操作状态，避免错误路径把按钮永久锁死。</summary>
        public void SetIdle(string text)
        {
            _busy = false;
            try
            {
                if (_btnCheck != null)
                {
                    _btnCheck.Enabled = true;
                    _btnCheck.Text = "检 查";
                    _btnCheck.BackColor = Brand;
                }
                Cursor = Cursors.Default;
                string msg = string.IsNullOrEmpty(text) ? "检查未完成" : text;
                if (_statusTime != null) _statusTime.Text = msg;
                if (_statusTip != null)
                {
                    _statusTip.ForeColor = Color.FromArgb(181, 71, 8);
                    _statusTip.Text = "⚠ " + msg;
                }
                if (_aiLabel != null)
                {
                    _aiLabel.Text = msg;
                    _aiLabel.ForeColor = Color.FromArgb(181, 71, 8);
                }
            }
            catch { }
        }

        public void SetTab(int index)
        {
            try
            {
                if (_tabs != null && index >= 0 && index < _tabs.TabPages.Count)
                    _tabs.SelectedIndex = index;
                Application.DoEvents();
            }
            catch { }
        }

        public void DemoCheck(int n)
        {
            if (_tolGrid == null) return;
            int k = 0;
            foreach (DataGridViewRow row in _tolGrid.Rows)
            {
                var r = row.Tag as QcDimRow;
                if (!CanCalc(r)) continue;
                if (k++ >= n) break;
                row.Cells[0].Value = true;
            }
            RefreshCalc();
            Application.DoEvents();
        }

        public string DemoGenerate(int idx)
        {
            if (_calc == null) return "（无计算面板）";
            _busy = false;
            return _calc.ClickGenerate(idx);
        }

        public bool DemoFocusBox(int idx, int k)
        {
            if (_calc == null) return false;
            return _calc.FocusBox(idx, k);
        }

        public string DemoEditTol(int rowIndex, string tolText)
        {
            if (_tolGrid == null || rowIndex < 0 || rowIndex >= _tolGrid.Rows.Count) return "(行无效)";
            DataGridViewRow gr = _tolGrid.Rows[rowIndex];
            var r = gr.Tag as QcDimRow;
            if (r == null) return "(该行无数据)";
            gr.Cells[3].Value = tolText;
            r.TolDisplay = tolText;
            ApplyTolText(r, tolText);
            if (r.HasTol && r.Band > 1e-9)
            {
                gr.Cells[0].ReadOnly = false;
                gr.Cells[0].Style.BackColor = Color.White;
            }
            gr.Cells[0].Value = true;
            RefreshCalc();
            return "改后: " + r.Content + " 公差=" + r.TolDisplay
                 + " 上=" + r.TolUp + " 下=" + r.TolLow + " 带=" + r.Band;
        }

        // ---------------------------------------------------------------- 任务栏
        const int GWL_EXSTYLE = -20;
        const int WS_EX_TOOLWINDOW = 0x00000080;
        const int WS_EX_APPWINDOW = 0x00040000;

        [DllImport("user32.dll", SetLastError = true)]
        static extern int GetWindowLong(IntPtr hWnd, int nIndex);

        [DllImport("user32.dll", SetLastError = true)]
        static extern int SetWindowLong(IntPtr hWnd, int nIndex, int dwNewLong);

        void EnsureTaskbarEntry()
        {
            try
            {
                ShowInTaskbar = true;
                if (Handle != IntPtr.Zero)
                {
                    int ex = GetWindowLong(Handle, GWL_EXSTYLE);
                    SetWindowLong(Handle, GWL_EXSTYLE, (ex & ~WS_EX_TOOLWINDOW) | WS_EX_APPWINDOW);
                }
            }
            catch { }
        }

        protected override void OnHandleCreated(EventArgs e)
        {
            base.OnHandleCreated(e);
            EnsureTaskbarEntry();
        }
    }
}

