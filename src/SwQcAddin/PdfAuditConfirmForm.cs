using System;
using System.Collections.Generic;
using System.ComponentModel;
using System.Drawing;
using System.IO;
using System.Linq;
using System.Windows.Forms;

namespace SwQc
{
    /// <summary>
    /// PDF 自动识别后的人工确认窗口。只编辑本次 PDF job 的临时数据，
    /// 不连接 QcDocument，也不写回 SolidWorks 文档。
    /// </summary>
    public class PdfAuditConfirmForm : Form
    {
        private readonly PdfConfirmationDraft _draft;
        private readonly string _jobDir;
        private readonly TabControl _tabs;
        private readonly PictureBox _preview;
        private readonly Label _hint;
        private readonly List<DataGridView> _grids = new List<DataGridView>();
        private List<PdfConfirmRow> _allRows = new List<PdfConfirmRow>();
        private BindingList<PdfConfirmRow> _dimensions;
        private BindingList<PdfConfirmRow> _gtols;
        private BindingList<PdfConfirmRow> _datums;
        private DataGridView _gtolGrid;
        private DataGridView _datumGrid;
        private bool _suppressRowState;
        private int _manualSequence;

        public List<PdfConfirmRow> ConfirmedRows { get; private set; }

        public PdfAuditConfirmForm(PdfConfirmationDraft draft, string jobDir)
        {
            _draft = draft ?? new PdfConfirmationDraft();
            _jobDir = jobDir ?? "";
            ConfirmedRows = new List<PdfConfirmRow>();
            Text = "PDF识别结果人工确认";
            StartPosition = FormStartPosition.CenterParent;
            Width = 1260; Height = 820; MinimumSize = new Size(980, 650);
            Font = new Font("Microsoft YaHei UI", 9F);
            BackColor = Color.White;

            var header = new Panel { Dock = DockStyle.Top, Height = 92, BackColor = Color.FromArgb(31, 81, 125) };
            header.Controls.Add(new Label { Left = 22, Top = 14, AutoSize = true, ForeColor = Color.White,
                Font = new Font("Microsoft YaHei UI", 15F, FontStyle.Bold), Text = "确认尺寸、公差、基准和归属" });
            header.Controls.Add(new Label { Left = 24, Top = 51, Width = 1160, Height = 34,
                ForeColor = Color.FromArgb(225, 235, 244),
                Text = "这里只显示 AI 无法确定或关系不完整的项目；确定项已自动接受且仍会提交审核。漏识别可新增，误识别可排除。" });

            var split = new SplitContainer { Dock = DockStyle.Fill, SplitterDistance = 920 };
            _tabs = new TabControl { Dock = DockStyle.Fill };
            split.Panel1.Controls.Add(_tabs);
            var previewPanel = new Panel { Dock = DockStyle.Fill, Padding = new Padding(8), BackColor = Color.FromArgb(242, 245, 248) };
            _hint = new Label { Dock = DockStyle.Top, Height = 42, ForeColor = Color.FromArgb(65, 75, 88),
                Text = "选择一行查看对应PDF分区" };
            _preview = new PictureBox { Dock = DockStyle.Fill, BackColor = Color.White, SizeMode = PictureBoxSizeMode.Zoom };
            previewPanel.Controls.Add(_preview); previewPanel.Controls.Add(_hint);
            split.Panel2.Controls.Add(previewPanel);

            var footer = new Panel { Dock = DockStyle.Bottom, Height = 58, BackColor = Color.FromArgb(247, 249, 252) };
            var confirm = new Button { Text = "确认并继续审核", Width = 150, Height = 34, Top = 12,
                BackColor = Color.FromArgb(31, 81, 125), ForeColor = Color.White, FlatStyle = FlatStyle.Flat,
                Anchor = AnchorStyles.Top | AnchorStyles.Right };
            var cancel = new Button { Text = "取消", Width = 90, Height = 34, Top = 12,
                DialogResult = DialogResult.Cancel, Anchor = AnchorStyles.Top | AnchorStyles.Right };
            footer.Resize += delegate
            {
                cancel.Left = footer.ClientSize.Width - cancel.Width - 14;
                confirm.Left = cancel.Left - confirm.Width - 10;
            };
            confirm.Click += delegate { ConfirmAndClose(); };
            footer.Controls.Add(confirm); footer.Controls.Add(cancel);

            Controls.Add(split); Controls.Add(footer); Controls.Add(header);
            AcceptButton = confirm; CancelButton = cancel;
            BuildTables();
            Shown += delegate
            {
                try { split.SplitterDistance = Math.Max(650, (int)(split.ClientSize.Width * 0.74)); }
                catch { }
                ColorRows();
            };
        }

        private DataGridView NewGrid()
        {
            var grid = new DataGridView { Dock = DockStyle.Fill, BackgroundColor = Color.White,
                BorderStyle = BorderStyle.None, AutoGenerateColumns = false, AllowUserToAddRows = false,
                AllowUserToDeleteRows = false, RowHeadersVisible = false,
                SelectionMode = DataGridViewSelectionMode.FullRowSelect, MultiSelect = false,
                AutoSizeRowsMode = DataGridViewAutoSizeRowsMode.AllCells };
            grid.ColumnHeadersDefaultCellStyle.BackColor = Color.FromArgb(222, 234, 245);
            grid.ColumnHeadersDefaultCellStyle.Font = new Font(Font, FontStyle.Bold);
            grid.EnableHeadersVisualStyles = false;
            grid.DefaultCellStyle.WrapMode = DataGridViewTriState.True;
            grid.SelectionChanged += delegate { ShowEvidence(grid); };
            grid.DataError += delegate { };
            grid.CellEndEdit += delegate(object sender, DataGridViewCellEventArgs e)
            {
                if (_suppressRowState || e.RowIndex < 0) return;
                var edited = grid.Rows[e.RowIndex].DataBoundItem as PdfConfirmRow;
                if (edited == null || edited.excluded) return;
                edited.confirmed = false;
                edited.status = "需确认";
                grid.InvalidateRow(e.RowIndex);
                ColorRows();
            };
            _grids.Add(grid);
            return grid;
        }

        private DataGridViewTextBoxColumn TextCol(string header, string field, int width, bool readOnly)
        {
            return new DataGridViewTextBoxColumn { HeaderText = header, DataPropertyName = field,
                Width = width, ReadOnly = readOnly, SortMode = DataGridViewColumnSortMode.NotSortable };
        }

        private void BuildTables()
        {
            List<PdfConfirmRow> rows = _draft.rows ?? new List<PdfConfirmRow>();
            _allRows = rows;
            // 是否需要人工确认以引擎判定为唯一真源，避免界面再套一层固定置信度，
            // 导致大量本可自动接受的尺寸被重复要求确认。
            Func<PdfConfirmRow, bool> needsHuman = x => x != null &&
                (!x.confirmed || x.status == "需确认");
            _dimensions = new BindingList<PdfConfirmRow>(rows.Where(x => x.object_type == "dimension" && needsHuman(x)).ToList());
            _gtols = new BindingList<PdfConfirmRow>(rows.Where(x => x.object_type == "gtol" && needsHuman(x)).ToList());
            _datums = new BindingList<PdfConfirmRow>(rows.Where(x => x.object_type == "datum" && needsHuman(x)).ToList());
            BuildDimensionTab(); BuildGtolTab(); BuildDatumTab();
        }

        private void BuildDimensionTab()
        {
            var grid = NewGrid();
            grid.Columns.Add(TextCol("页", "page", 45, true));
            grid.Columns.Add(TextCol("尺寸ID", "entity_id", 105, true));
            grid.Columns.Add(TextCol("类别", "kind", 90, false));
            grid.Columns.Add(TextCol("图上完整标注", "display", 170, false));
            grid.Columns.Add(TextCol("基本尺寸", "nominal", 80, false));
            grid.Columns.Add(TextCol("上偏差", "upper", 75, false));
            grid.Columns.Add(TextCol("下偏差", "lower", 75, false));
            grid.Columns.Add(TextCol("配合", "fit", 70, false));
            grid.Columns.Add(TextCol("数量", "quantity", 58, false));
            grid.Columns.Add(TextCol("对应要素", "target_feature", 130, false));
            grid.Columns.Add(TextCol("状态", "status", 75, true));
            grid.DataSource = _dimensions;
            AddTab("尺寸 / 尺寸公差（" + _dimensions.Count + "）", grid, "dimension");
        }

        private List<PdfDimensionChoice> DimensionChoicesWithBlank()
        {
            var choices = new List<PdfDimensionChoice>();
            choices.Add(new PdfDimensionChoice { entity_id = "", label = "（不指定尺寸）" });
            foreach (PdfConfirmRow dimension in AllRows().Where(x => x.object_type == "dimension" && !x.excluded))
                choices.Add(new PdfDimensionChoice { entity_id = dimension.entity_id,
                    label = dimension.entity_id + "  " + (dimension.display ?? "") });
            foreach (PdfConfirmRow row in _gtols.Concat(_datums))
            {
                if (!string.IsNullOrEmpty(row.target_entity_id) &&
                    !choices.Any(x => x.entity_id == row.target_entity_id))
                    choices.Add(new PdfDimensionChoice { entity_id = row.target_entity_id,
                        label = row.target_entity_id + "（原识别）" });
            }
            return choices;
        }

        private void BuildGtolTab()
        {
            var grid = NewGrid();
            _gtolGrid = grid;
            grid.Columns.Add(TextCol("页", "page", 45, true));
            grid.Columns.Add(TextCol("公差ID", "entity_id", 100, true));
            grid.Columns.Add(TextCol("复合组", "group_id", 90, false));
            grid.Columns.Add(TextCol("公差框（每行一层；符号|值|修饰|基准）", "frame_lines_text", 270, false));
            var targetType = new DataGridViewComboBoxColumn { HeaderText = "被测要素类型",
                DataPropertyName = "target_type", Width = 110, FlatStyle = FlatStyle.Flat };
            targetType.Items.AddRange("dimension", "surface", "axis", "centerline", "feature", "unknown");
            grid.Columns.Add(targetType);
            var targetDim = new DataGridViewComboBoxColumn { HeaderText = "挂接尺寸",
                DataPropertyName = "target_entity_id", Width = 175, FlatStyle = FlatStyle.Flat,
                DataSource = DimensionChoicesWithBlank(), DisplayMember = "label", ValueMember = "entity_id" };
            grid.Columns.Add(targetDim);
            grid.Columns.Add(TextCol("被测/指向要素", "target_feature", 145, false));
            grid.Columns.Add(TextCol("状态", "status", 75, true));
            grid.DataSource = _gtols;
            AddTab("几何公差 / 归属（" + _gtols.Count + "）", grid, "gtol");
        }

        private void BuildDatumTab()
        {
            var grid = NewGrid();
            _datumGrid = grid;
            grid.Columns.Add(TextCol("页", "page", 45, true));
            grid.Columns.Add(TextCol("基准ID", "entity_id", 110, true));
            grid.Columns.Add(TextCol("基准字母", "letter", 90, false));
            grid.Columns.Add(TextCol("基准指向要素", "target_feature", 260, false));
            var targetDim = new DataGridViewComboBoxColumn { HeaderText = "关联尺寸",
                DataPropertyName = "target_entity_id", Width = 190, FlatStyle = FlatStyle.Flat,
                DataSource = DimensionChoicesWithBlank(), DisplayMember = "label", ValueMember = "entity_id" };
            grid.Columns.Add(targetDim);
            grid.Columns.Add(TextCol("状态", "status", 80, true));
            grid.DataSource = _datums;
            AddTab("基准 / 指向（" + _datums.Count + "）", grid, "datum");
        }

        private Button ToolButton(string text, int width)
        {
            return new Button { Text = text, Width = width, Height = 30, Margin = new Padding(4, 4, 2, 4),
                FlatStyle = FlatStyle.Flat, BackColor = Color.White };
        }

        private void AddTab(string title, DataGridView grid, string objectType)
        {
            var tab = new TabPage(title) { BackColor = Color.White };
            var tools = new FlowLayoutPanel { Dock = DockStyle.Top, Height = 40, BackColor = Color.FromArgb(242, 245, 248),
                FlowDirection = FlowDirection.LeftToRight, WrapContents = false, Padding = new Padding(4, 1, 0, 0) };
            var pageLabel = new Label { Text = "页码", AutoSize = true, Margin = new Padding(4, 9, 0, 0) };
            int pageCount = _draft.source == null ? 1 : Math.Max(1, _draft.source.page_count);
            var page = new NumericUpDown { Minimum = 1, Maximum = pageCount, Value = 1,
                Width = 54, Height = 28, Margin = new Padding(4, 5, 6, 0) };
            var add = ToolButton("新增漏识别", 98);
            var confirmRow = ToolButton("确认当前行", 98);
            var exclude = ToolButton("误识别/恢复", 104);
            var confirmPage = ToolButton("确认当前页全部", 118);
            var fullPage = ToolButton("查看整页图", 98);
            add.Click += delegate { AddManualRow(objectType, grid, (int)page.Value); };
            confirmRow.Click += delegate { ConfirmSelectedRow(grid); };
            exclude.Click += delegate { ToggleExcluded(grid); };
            confirmPage.Click += delegate { ConfirmSelectedPage((int)page.Value); };
            fullPage.Click += delegate { ShowPageOverview((int)page.Value); };
            grid.SelectionChanged += delegate
            {
                PdfConfirmRow selected = SelectedRow(grid);
                if (selected != null && selected.page >= page.Minimum && selected.page <= page.Maximum)
                    page.Value = selected.page;
            };
            tools.Controls.Add(pageLabel); tools.Controls.Add(page); tools.Controls.Add(add);
            tools.Controls.Add(confirmRow); tools.Controls.Add(exclude);
            tools.Controls.Add(confirmPage); tools.Controls.Add(fullPage);
            tab.Controls.Add(grid); tab.Controls.Add(tools); _tabs.TabPages.Add(tab);
        }

        private PdfConfirmRow SelectedRow(DataGridView grid)
        {
            if (grid == null || grid.SelectedRows.Count == 0) return null;
            return grid.SelectedRows[0].DataBoundItem as PdfConfirmRow;
        }

        private IEnumerable<PdfConfirmRow> AllRows()
        {
            return _allRows;
        }

        private string NextManualId(string objectType, int page)
        {
            string prefix = objectType == "dimension" ? "DIM" : (objectType == "gtol" ? "GTOL" : "DATUM");
            string id;
            do
            {
                _manualSequence++;
                id = "MANUAL-" + prefix + "-P" + page.ToString("000") + "-" + _manualSequence.ToString("000");
            }
            while (AllRows().Any(x => string.Equals(x.entity_id, id, StringComparison.Ordinal)));
            return id;
        }

        private void AddManualRow(string objectType, DataGridView grid, int page)
        {
            if (page <= 0) page = 1;
            var row = new PdfConfirmRow { object_type = objectType, page = page, confidence = 1.0,
                status = "人工新增-待确认", manual_added = true, confirmed = false, excluded = false };
            row.entity_id = NextManualId(objectType, page);
            row.row_id = "CONF-" + row.entity_id;
            _allRows.Add(row);
            if (objectType == "dimension")
            {
                row.kind = "linear";
                _dimensions.Add(row);
                RefreshDimensionChoices();
            }
            else if (objectType == "gtol")
            {
                row.group_id = "MANUAL-GROUP-" + _manualSequence.ToString("000");
                row.target_type = "unknown";
                _gtols.Add(row);
            }
            else
            {
                _datums.Add(row);
            }
            grid.ClearSelection();
            if (grid.Rows.Count > 0)
            {
                grid.Rows[grid.Rows.Count - 1].Selected = true;
                grid.FirstDisplayedScrollingRowIndex = grid.Rows.Count - 1;
            }
            ColorRows();
        }

        private void RefreshDimensionChoices()
        {
            foreach (DataGridView grid in new[] { _gtolGrid, _datumGrid })
            {
                if (grid == null) continue;
                foreach (DataGridViewColumn col in grid.Columns)
                {
                    var combo = col as DataGridViewComboBoxColumn;
                    if (combo != null && combo.DataPropertyName == "target_entity_id")
                    {
                        combo.DataSource = null;
                        combo.DataSource = DimensionChoicesWithBlank();
                        combo.DisplayMember = "label";
                        combo.ValueMember = "entity_id";
                    }
                }
            }
        }

        private void ToggleExcluded(DataGridView grid)
        {
            PdfConfirmRow row = SelectedRow(grid);
            if (row == null) return;
            _suppressRowState = true;
            try
            {
                row.excluded = !row.excluded;
                row.confirmed = row.excluded;
                row.confirmation_source = row.excluded ? "human" : "";
                row.status = row.excluded ? "人工排除" : "需确认";
                if (row.object_type == "dimension") RefreshDimensionChoices();
            }
            finally { _suppressRowState = false; }
            grid.Refresh(); ColorRows();
        }

        private bool ValidateRow(PdfConfirmRow row, out string error)
        {
            error = "";
            if (row == null) { error = "没有选择记录。"; return false; }
            if (row.excluded) return true;
            if (row.page <= 0) { error = "页码必须大于0。"; return false; }
            if (string.IsNullOrWhiteSpace(row.entity_id)) { error = "对象ID不能为空。"; return false; }
            if (row.object_type == "dimension")
            {
                if (string.IsNullOrWhiteSpace(row.display)) { error = "尺寸完整标注不能为空。"; return false; }
            }
            else if (row.object_type == "gtol")
            {
                if (string.IsNullOrWhiteSpace(row.frame_lines_text)) { error = "几何公差框不能为空。"; return false; }
                foreach (string line in row.frame_lines_text.Split(new[] { '\r', '\n' }, StringSplitOptions.RemoveEmptyEntries))
                {
                    string[] parts = line.Split('|');
                    if (parts.Length < 2 || string.IsNullOrWhiteSpace(parts[0]) || string.IsNullOrWhiteSpace(parts[1]))
                    { error = "每层公差框必须按“符号 | 公差值 | 修饰 | 基准”填写。"; return false; }
                }
                if (row.target_type == "dimension")
                {
                    if (string.IsNullOrWhiteSpace(row.target_entity_id) ||
                        !AllRows().Any(x => x.object_type == "dimension" && !x.excluded && x.entity_id == row.target_entity_id))
                    { error = "选择了尺寸归属，但挂接尺寸不存在或已排除。"; return false; }
                }
                else if (string.IsNullOrWhiteSpace(row.target_feature))
                { error = "几何公差的被测/指向要素尚未确认。"; return false; }
            }
            else if (row.object_type == "datum")
            {
                if (string.IsNullOrWhiteSpace(row.letter)) { error = "基准字母不能为空。"; return false; }
                if (string.IsNullOrWhiteSpace(row.target_feature) && string.IsNullOrWhiteSpace(row.target_entity_id))
                { error = "基准指向要素尚未确认。"; return false; }
                if (!string.IsNullOrWhiteSpace(row.target_entity_id) &&
                    !AllRows().Any(x => x.object_type == "dimension" && !x.excluded && x.entity_id == row.target_entity_id))
                { error = "基准关联的尺寸不存在或已排除。"; return false; }
            }
            return true;
        }

        private void ConfirmSelectedRow(DataGridView grid)
        {
            PdfConfirmRow row = SelectedRow(grid);
            string error;
            if (!ValidateRow(row, out error))
            {
                MessageBox.Show(error, "人工确认", MessageBoxButtons.OK, MessageBoxIcon.Warning); return;
            }
            row.confirmed = true;
            row.confirmation_source = "human";
            row.status = row.excluded ? "人工排除" : (row.manual_added ? "人工新增并确认" : "人工确认");
            grid.Refresh(); ColorRows();
        }

        private void ConfirmSelectedPage(int page)
        {
            List<PdfConfirmRow> pageRows = AllRows().Where(x => x.page == page).ToList();
            if (pageRows.Count == 0)
            {
                MessageBox.Show("第 " + page + " 页还没有可确认记录。若图上存在标注，请先用“新增漏识别”补录。",
                    "人工确认", MessageBoxButtons.OK, MessageBoxIcon.Information); return;
            }
            foreach (PdfConfirmRow row in pageRows)
            {
                string error;
                if (!ValidateRow(row, out error))
                {
                    MessageBox.Show("第 " + page + " 页的 " + row.entity_id + "：" + error,
                        "人工确认", MessageBoxButtons.OK, MessageBoxIcon.Warning); return;
                }
            }
            foreach (PdfConfirmRow row in pageRows)
            {
                row.confirmed = true;
                row.confirmation_source = "human";
                row.status = row.excluded ? "人工排除" : (row.manual_added ? "人工新增并确认" : "人工确认");
            }
            foreach (DataGridView g in _grids) g.Refresh();
            ColorRows();
        }

        private void ShowPageOverview(int page)
        {
            string path = Path.Combine(_jobDir, "pages", "page_" + page.ToString("000"),
                "p" + page.ToString("000") + "_overview.png");
            if (!File.Exists(path))
                path = Path.Combine(_jobDir, "page_" + page.ToString("000"),
                    "p" + page.ToString("000") + "_overview.png");
            if (!File.Exists(path) && Directory.Exists(_jobDir))
            {
                string[] files = Directory.GetFiles(_jobDir, "p" + page.ToString("000") + "_overview.png",
                    SearchOption.AllDirectories);
                if (files.Length > 0) path = files[0];
            }
            _hint.Text = "第 " + page + " 页整页图：请检查是否存在漏识别标注";
            LoadPreview(path);
        }

        private void LoadPreview(string path)
        {
            Image old = _preview.Image; _preview.Image = null; if (old != null) old.Dispose();
            if (string.IsNullOrEmpty(path) || !File.Exists(path)) return;
            try
            {
                byte[] data = File.ReadAllBytes(path);
                using (var ms = new MemoryStream(data))
                using (var tmp = Image.FromStream(ms)) _preview.Image = new Bitmap(tmp);
            }
            catch { }
        }

        private void ColorRows()
        {
            foreach (DataGridView grid in _grids)
            {
                foreach (DataGridViewRow uiRow in grid.Rows)
                {
                    var row = uiRow.DataBoundItem as PdfConfirmRow;
                    if (row == null) continue;
                    uiRow.DefaultCellStyle.ForeColor = Color.Black;
                    if (row.excluded)
                    {
                        uiRow.DefaultCellStyle.BackColor = Color.FromArgb(232, 235, 239);
                        uiRow.DefaultCellStyle.ForeColor = Color.FromArgb(100, 105, 112);
                    }
                    else if (row.confirmed)
                        uiRow.DefaultCellStyle.BackColor = Color.FromArgb(226, 244, 232);
                    else
                        uiRow.DefaultCellStyle.BackColor = Color.FromArgb(255, 247, 218);
                }
            }
        }

        private void ShowEvidence(DataGridView grid)
        {
            if (grid.SelectedRows.Count == 0) return;
            var row = grid.SelectedRows[0].DataBoundItem as PdfConfirmRow;
            if (row == null) return;
            _hint.Text = "第 " + row.page + " 页　" + (row.entity_id ?? "") +
                         "　置信度 " + Math.Round(row.confidence * 100) + "%";
            string path = "";
            try
            {
                if (!string.IsNullOrEmpty(row.crop_id) && Directory.Exists(_jobDir))
                {
                    string[] files = Directory.GetFiles(_jobDir, row.crop_id + ".png", SearchOption.AllDirectories);
                    if (files.Length > 0) path = files[0];
                }
            }
            catch { }
            if (string.IsNullOrEmpty(path) || !File.Exists(path))
            {
                ShowPageOverview(row.page);
                return;
            }
            LoadPreview(path);
        }

        private void ConfirmAndClose()
        {
            foreach (DataGridView grid in _grids) grid.EndEdit();
            List<PdfConfirmRow> rows = AllRows().ToList();
            var ids = new HashSet<string>(StringComparer.Ordinal);
            foreach (PdfConfirmRow row in rows)
            {
                if (!ids.Add(row.entity_id ?? ""))
                {
                    MessageBox.Show("对象 ID 重复：" + row.entity_id, "人工确认",
                        MessageBoxButtons.OK, MessageBoxIcon.Warning); return;
                }
                if (!row.confirmed)
                {
                    MessageBox.Show(row.entity_id + " 尚未人工确认。请使用“确认当前行”或“确认当前页全部”。", "人工确认",
                        MessageBoxButtons.OK, MessageBoxIcon.Warning); return;
                }
                string error;
                if (!ValidateRow(row, out error))
                {
                    MessageBox.Show(row.entity_id + "：" + error, "人工确认",
                        MessageBoxButtons.OK, MessageBoxIcon.Warning); return;
                }
            }

            var datumLetters = new HashSet<string>(_datums.Where(x => !x.excluded)
                .Select(x => (x.letter ?? "").Trim()), StringComparer.OrdinalIgnoreCase);
            foreach (PdfConfirmRow row in _gtols.Where(x => !x.excluded))
            {
                foreach (string line in row.frame_lines_text.Split(new[] { '\r', '\n' },
                    StringSplitOptions.RemoveEmptyEntries))
                {
                    string[] parts = line.Split('|');
                    for (int i = 3; i < parts.Length && i < 6; i++)
                    {
                        string datum = (parts[i] ?? "").Trim();
                        if (datum.Length > 0 && !datumLetters.Contains(datum))
                        {
                            MessageBox.Show("几何公差 " + row.entity_id + " 引用的基准 " + datum +
                                " 不存在或已排除。", "人工确认", MessageBoxButtons.OK,
                                MessageBoxIcon.Warning); return;
                        }
                    }
                }
            }
            ConfirmedRows = rows;
            DialogResult = DialogResult.OK;
            Close();
        }
    }
}
