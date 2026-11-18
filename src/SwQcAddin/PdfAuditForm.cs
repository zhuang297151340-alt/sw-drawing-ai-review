using System;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Text;
using System.Windows.Forms;

namespace SwQc
{
    /// <summary>PDF 视觉通道独立结果窗口；不展示或修改现有三表。</summary>
    public class PdfAuditForm : Form
    {
        private readonly Label _status;
        private readonly Label _summary;
        private readonly Label _counts;
        private readonly Label _file;
        private readonly DataGridView _grid;
        private readonly RichTextBox _detail;
        private readonly PictureBox _evidence;
        private readonly Button _import;
        private readonly Button _detect;
        private readonly Button _cancel;
        private PdfAuditReport _report;
        private string _jobDirectory = "";
        private string _importedPdfPath = "";
        private bool _auditRunning;
        private volatile bool _cancelRequested;

        public bool CancelRequested { get { return _cancelRequested; } }
        public bool AuditRunning { get { return _auditRunning; } }
        public event Action<string> AuditRequested;

        public PdfAuditForm(string fileName)
        {
            Text = "工程图 QC · PDF视觉整图审核";
            StartPosition = FormStartPosition.CenterScreen;
            Width = 1180;
            Height = 800;
            MinimumSize = new Size(900, 620);
            Font = new Font("Microsoft YaHei UI", 9F);
            BackColor = Color.White;

            var header = new Panel { Dock = DockStyle.Top, Height = 82, BackColor = Color.FromArgb(31, 81, 125) };
            var title = new Label { Left = 22, Top = 14, AutoSize = true, ForeColor = Color.White,
                Font = new Font("Microsoft YaHei UI", 16F, FontStyle.Bold), Text = "PDF 视觉整图审核" };
            _file = new Label { Left = 24, Top = 50, AutoSize = true, ForeColor = Color.FromArgb(225, 235, 244),
                Text = "图纸：" + (fileName ?? "") };
            header.Controls.Add(title); header.Controls.Add(_file);
            Controls.Add(header);

            var overview = new Panel { Dock = DockStyle.Top, Height = 92, BackColor = Color.FromArgb(247, 249, 252), Padding = new Padding(18, 10, 18, 8) };
            _status = new Label { Dock = DockStyle.Top, Height = 26, ForeColor = Color.FromArgb(31, 81, 125),
                Font = new Font("Microsoft YaHei UI", 10F, FontStyle.Bold), Text = "请选择图纸来源，然后点击“检测”" };
            _summary = new Label { Dock = DockStyle.Fill, ForeColor = Color.FromArgb(55, 60, 68),
                Text = "默认检测当前 SolidWorks 图纸页；也可以点击“导入PDF”选择其他图纸。" };
            _counts = new Label { Dock = DockStyle.Right, Width = 330, TextAlign = ContentAlignment.MiddleRight,
                ForeColor = Color.FromArgb(55, 60, 68) };
            overview.Controls.Add(_summary); overview.Controls.Add(_counts); overview.Controls.Add(_status);
            Controls.Add(overview);

            var split = new SplitContainer { Dock = DockStyle.Fill, SplitterDistance = 610, BackColor = Color.FromArgb(222, 228, 235) };
            _grid = new DataGridView { Dock = DockStyle.Fill, BackgroundColor = Color.White, BorderStyle = BorderStyle.None,
                AllowUserToAddRows = false, AllowUserToDeleteRows = false, AllowUserToResizeRows = false,
                ReadOnly = true, RowHeadersVisible = false, SelectionMode = DataGridViewSelectionMode.FullRowSelect,
                MultiSelect = false, AutoGenerateColumns = false };
            _grid.ColumnHeadersDefaultCellStyle.BackColor = Color.FromArgb(222, 234, 245);
            _grid.ColumnHeadersDefaultCellStyle.Font = new Font("Microsoft YaHei UI", 9F, FontStyle.Bold);
            _grid.EnableHeadersVisualStyles = false;
            _grid.RowTemplate.Height = 34;
            _grid.Columns.Add(new DataGridViewTextBoxColumn { Name = "sev", HeaderText = "等级", Width = 72 });
            _grid.Columns.Add(new DataGridViewTextBoxColumn { Name = "cat", HeaderText = "类别", Width = 105 });
            _grid.Columns.Add(new DataGridViewTextBoxColumn { Name = "obj", HeaderText = "审核对象", Width = 170 });
            _grid.Columns.Add(new DataGridViewTextBoxColumn { Name = "result", HeaderText = "审核结果", AutoSizeMode = DataGridViewAutoSizeColumnMode.Fill });
            _grid.SelectionChanged += delegate { ShowSelected(); };
            split.Panel1.Controls.Add(_grid);

            var right = new SplitContainer { Dock = DockStyle.Fill, Orientation = Orientation.Horizontal, SplitterDistance = 365 };
            _detail = new RichTextBox { Dock = DockStyle.Fill, ReadOnly = true, BorderStyle = BorderStyle.None,
                BackColor = Color.White, Font = new Font("Microsoft YaHei UI", 9.5F), DetectUrls = false };
            _evidence = new PictureBox { Dock = DockStyle.Fill, BackColor = Color.FromArgb(242, 244, 247),
                SizeMode = PictureBoxSizeMode.Zoom };
            right.Panel1.Padding = new Padding(15); right.Panel1.Controls.Add(_detail);
            right.Panel2.Padding = new Padding(8); right.Panel2.Controls.Add(_evidence);
            split.Panel2.Controls.Add(right);
            Controls.Add(split);

            var footer = new Panel { Dock = DockStyle.Bottom, Height = 52, BackColor = Color.FromArgb(247, 249, 252) };
            _import = new Button { Text = "导入PDF", Width = 90, Height = 32, Top = 10, Left = 12 };
            _import.Click += delegate { ImportPdf(); };
            _detect = new Button { Text = "检测", Width = 90, Height = 32, Top = 10, Left = 112,
                BackColor = Color.FromArgb(31, 81, 125), ForeColor = Color.White, FlatStyle = FlatStyle.Flat };
            _detect.Click += delegate
            {
                Action<string> handler = AuditRequested;
                if (!_auditRunning && handler != null) handler(_importedPdfPath);
            };
            var open = new Button { Text = "打开报告目录", Width = 110, Height = 32, Top = 10, Left = 212 };
            open.Click += delegate
            {
                try { if (_report != null && Directory.Exists(_report.JobDirectory)) Process.Start("explorer.exe", _report.JobDirectory); }
                catch { }
            };
            _cancel = new Button { Text = "取消审核", Width = 100, Height = 32, Top = 10, Left = 332, Enabled = false };
            _cancel.Click += delegate { _cancelRequested = true; _cancel.Enabled = false; SetStatus("正在取消审核…"); };
            var settings = new Button { Text = "视觉模型设置", Width = 110, Height = 32, Top = 10, Left = 442 };
            settings.Click += delegate { using (var dlg = new PdfAuditSettingsForm()) dlg.ShowDialog(this); };
            var close = new Button { Text = "关闭", Width = 90, Height = 32, Top = 10, Anchor = AnchorStyles.Top | AnchorStyles.Right };
            close.Left = Width - 125;
            close.Click += delegate { Close(); };
            footer.Resize += delegate { close.Left = footer.ClientSize.Width - close.Width - 12; };
            footer.Controls.Add(_import); footer.Controls.Add(_detect); footer.Controls.Add(open);
            footer.Controls.Add(_cancel); footer.Controls.Add(settings); footer.Controls.Add(close);
            // Dock 布局按 Fill → Bottom → 次级 Top → 主 Header 的顺序统一加入，避免 Fill 覆盖顶部信息。
            Controls.Clear();
            Controls.Add(split);
            Controls.Add(footer);
            Controls.Add(overview);
            Controls.Add(header);
            Shown += delegate
            {
                // 在 DPI 缩放完成后按比例设定，避免 150% 缩放时右侧证据区被压得过窄。
                try
                {
                    split.SplitterDistance = Math.Max(420, (int)(split.ClientSize.Width * 0.56));
                    right.SplitterDistance = Math.Max(220, (int)(right.ClientSize.Height * 0.56));
                }
                catch { }
            };
            FormClosing += delegate { if (_auditRunning) _cancelRequested = true; };
            FormClosed += delegate
            {
                string path = _report != null && !string.IsNullOrEmpty(_report.JobDirectory) ?
                    _report.JobDirectory : _jobDirectory;
                if (!string.IsNullOrEmpty(path)) CleanupJobDirectory(path);
            };
        }

        public void SetJobDirectory(string path)
        {
            if (!string.IsNullOrEmpty(_jobDirectory) &&
                !string.Equals(_jobDirectory, path, StringComparison.OrdinalIgnoreCase))
                CleanupJobDirectory(_jobDirectory);
            _jobDirectory = path ?? "";
        }

        private void ImportPdf()
        {
            if (_auditRunning) return;
            using (var dlg = new OpenFileDialog())
            {
                dlg.Title = "导入要审核的 PDF 图纸";
                dlg.Filter = "PDF 文件 (*.pdf)|*.pdf";
                if (dlg.ShowDialog(this) != DialogResult.OK) return;
                _importedPdfPath = dlg.FileName;
                _file.Text = "图纸：" + Path.GetFileName(_importedPdfPath) + "（导入PDF）";
                _status.Text = "PDF 已导入，点击“检测”开始审图";
                _status.ForeColor = Color.FromArgb(31, 81, 125);
                _summary.Text = _importedPdfPath;
                _counts.Text = "";
            }
        }

        public void BeginAudit(string sourceName)
        {
            if (InvokeRequired) { BeginInvoke((MethodInvoker)delegate { BeginAudit(sourceName); }); return; }
            _auditRunning = true;
            _cancelRequested = false;
            _report = null;
            _import.Enabled = false;
            _detect.Enabled = false;
            _cancel.Enabled = true;
            _grid.Rows.Clear();
            _detail.Clear();
            Image old = _evidence.Image; _evidence.Image = null; if (old != null) old.Dispose();
            if (!string.IsNullOrEmpty(sourceName)) _file.Text = "图纸：" + sourceName;
            _status.Text = "正在准备图纸…";
            _status.ForeColor = Color.FromArgb(31, 81, 125);
            _summary.Text = "识别和审核过程在后台运行，当前窗口不会阻塞 SolidWorks。";
            _counts.Text = "";
        }

        private void EndAuditControls()
        {
            _auditRunning = false;
            _cancel.Enabled = false;
            _import.Enabled = true;
            _detect.Enabled = true;
        }

        /// <summary>只允许清理本插件 PDF job 根目录下的单个任务，避免误删用户 PDF。</summary>
        public static void CleanupJobDirectory(string path)
        {
            try
            {
                if (string.IsNullOrEmpty(path) || !Directory.Exists(path)) return;
                string root = Path.GetFullPath(Path.Combine(Path.GetTempPath(), "SwQcAddin", "pdf_audit"))
                    .TrimEnd(Path.DirectorySeparatorChar) + Path.DirectorySeparatorChar;
                string target = Path.GetFullPath(path).TrimEnd(Path.DirectorySeparatorChar) + Path.DirectorySeparatorChar;
                string leaf = new DirectoryInfo(path).Name;
                if (!target.StartsWith(root, StringComparison.OrdinalIgnoreCase) || leaf.Length < 16) return;
                Directory.Delete(path, true);
            }
            catch { }
        }

        public void SetStatus(string text)
        {
            if (InvokeRequired) { BeginInvoke((MethodInvoker)delegate { SetStatus(text); }); return; }
            _status.Text = text ?? "";
        }

        public void SetFailure(string message)
        {
            if (InvokeRequired) { BeginInvoke((MethodInvoker)delegate { SetFailure(message); }); return; }
            EndAuditControls();
            _status.Text = "PDF 整图审核未完成";
            _status.ForeColor = Color.FromArgb(180, 45, 35);
            _summary.Text = message ?? "未知错误";
        }

        public void SetReport(PdfAuditReport report)
        {
            if (InvokeRequired) { BeginInvoke((MethodInvoker)delegate { SetReport(report); }); return; }
            _report = report;
            if (report != null && !string.IsNullOrEmpty(report.JobDirectory))
                _jobDirectory = report.JobDirectory;
            EndAuditControls();
            _grid.Rows.Clear();
            if (report == null) { SetFailure("报告为空。"); return; }
            // error 非空就是引擎失败，不允许降级显示成“审核不完整”。
            // 旧引擎某些提前返回没有写 status，这里也要可靠兜底。
            if (!string.IsNullOrEmpty(report.error) || report.status == "failed")
            {
                SetFailure(string.IsNullOrEmpty(report.error) ? "PDF 审核引擎执行失败。" : report.error);
                return;
            }
            PdfAuditCounts c = report.counts ?? new PdfAuditCounts();
            bool pending = c.manual_review > 0 || report.status != "complete";
            _status.Text = report.status == "complete" ?
                (pending ? "审图完成 · 有关键项待核对" : "审图完成") : "审核已输出 · 覆盖不完整";
            _status.ForeColor = pending ? Color.FromArgb(190, 115, 15) : Color.FromArgb(30, 125, 70);
            PdfAuditCoverage coverage = report.coverage ?? new PdfAuditCoverage();
            string timing = report.performance == null ?
                ("用时 " + report.elapsed_sec.ToString("0.#") + " 秒") :
                ("用时 " + report.elapsed_sec.ToString("0.#") + " 秒；视觉模型 " +
                 report.performance.model_elapsed_sec.ToString("0.#") + " 秒 / " +
                 report.performance.model_calls + " 次调用");
            _summary.Text = (report.summary ?? "") + "\r\n" + timing;
            _counts.Text = "已审 " + (coverage.reviewed_pages == null ? 0 : coverage.reviewed_pages.Count) +
                "/" + coverage.total_pages + " 页   严重 " + c.critical + "  一般 " + c.major +
                "  轻微 " + c.minor + "  待核对 " + c.manual_review;
            foreach (PdfAuditIssue item in report.AllIssues())
            {
                int n = _grid.Rows.Add(item.SeverityText, item.category, item.reviewed_object, item.audit_result);
                _grid.Rows[n].Tag = item;
                Color color = Color.FromArgb(65, 70, 76);
                if (item.severity == "critical") color = Color.FromArgb(190, 45, 35);
                else if (item.severity == "major") color = Color.FromArgb(200, 115, 20);
                else if (item.severity == "manual_review") color = Color.FromArgb(65, 100, 150);
                _grid.Rows[n].Cells[0].Style.ForeColor = color;
                _grid.Rows[n].Cells[0].Style.Font = new Font(Font, FontStyle.Bold);
            }
            if (_grid.Rows.Count > 0)
            {
                _grid.Rows[0].Selected = true;
                ShowSelected();
            }
            else
            {
                _detail.Text = "已审范围内未发现有图面证据支撑的风险。\r\n请查看顶部覆盖状态；必要时对照原图抽查关键尺寸。";
            }
        }

        private void ShowSelected()
        {
            if (_grid.SelectedRows.Count == 0) return;
            var item = _grid.SelectedRows[0].Tag as PdfAuditIssue;
            if (item == null) return;
            var sb = new StringBuilder();
            if (!string.IsNullOrWhiteSpace(item.reviewed_object)) sb.AppendLine("对象：" + item.reviewed_object);
            if (!string.IsNullOrWhiteSpace(item.drawing_evidence)) sb.AppendLine("图面：" + item.drawing_evidence);
            if (!string.IsNullOrWhiteSpace(item.audit_result)) sb.AppendLine("判断：" + item.audit_result);
            if (!string.IsNullOrWhiteSpace(item.risk)) sb.AppendLine("影响：" + item.risk);
            if (!string.IsNullOrWhiteSpace(item.suggestion)) sb.AppendLine("建议：" + item.suggestion);
            if (!string.IsNullOrWhiteSpace(item.verification)) sb.AppendLine("复核：" + item.verification);
            if (!string.IsNullOrWhiteSpace(item.basis)) sb.AppendLine("依据：" + item.basis);
            if (item.location != null)
                sb.AppendLine("\r\n位置：第 " + item.location.page + " 页 / " + (item.location.crop_id ?? "整页"));
            _detail.Text = sb.ToString();
            LoadEvidence(item.evidence_image);
        }

        private void LoadEvidence(string path)
        {
            Image old = _evidence.Image;
            _evidence.Image = null;
            if (old != null) old.Dispose();
            if (string.IsNullOrEmpty(path) || !File.Exists(path)) return;
            try
            {
                byte[] bytes = File.ReadAllBytes(path);
                using (var ms = new MemoryStream(bytes))
                using (var tmp = Image.FromStream(ms))
                    _evidence.Image = new Bitmap(tmp);
            }
            catch { }
        }
    }
}
