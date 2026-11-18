using System;
using System.Drawing;
using System.Windows.Forms;

namespace SwQc
{
    /// <summary>手工新增/编辑尺寸行：类别、内容、公差、IT、几何公差、备注。</summary>
    public class QcManualRowForm : Form
    {
        public string Kind = "尺寸特征";
        public string Content = "";
        public string TolUp = "";
        public string TolLow = "";
        public string ITText = "";
        public string GtolText = "";
        public string Note = "";

        private ComboBox _kind;
        private TextBox _content, _up, _low, _it, _gtol, _note;

        public QcManualRowForm(string title)
        {
            Text = title;
            StartPosition = FormStartPosition.CenterParent;
            FormBorderStyle = FormBorderStyle.FixedDialog;
            MaximizeBox = false; MinimizeBox = false;
            ClientSize = new Size(620, 420);
            BackColor = Color.White;
            Font = new Font("Microsoft YaHei", 9F);

            var root = new TableLayoutPanel();
            root.Dock = DockStyle.Fill;
            root.ColumnCount = 2;
            root.Padding = new Padding(16, 14, 16, 10);
            root.ColumnStyles.Add(new ColumnStyle(SizeType.Absolute, 110F));
            root.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 100F));

            _kind = new ComboBox();
            _kind.DropDownStyle = ComboBoxStyle.DropDown;
            _kind.Items.AddRange(new object[] { "尺寸特征", "轮廓-X", "轮廓-Y", "轮廓-Z", "板厚", "粗糙度" });
            _kind.Text = "尺寸特征";

            _content = NewBox();
            _up = NewBox();
            _low = NewBox();
            _it = NewBox();
            _gtol = NewBox();
            _note = new TextBox(); _note.Multiline = true; _note.Height = 70; _note.Dock = DockStyle.Fill;

            AddRow(root, "类别", _kind);
            AddRow(root, "尺寸特征（内容）", _content);
            AddRow(root, "公差上偏差", _up);
            AddRow(root, "公差下偏差", _low);
            AddRow(root, "IT 等级（可空）", _it);
            AddRow(root, "几何公差（可空）", _gtol);
            AddRow(root, "备注", _note);

            Controls.Add(root);

            var bar = new FlowLayoutPanel();
            bar.Dock = DockStyle.Bottom;
            bar.FlowDirection = FlowDirection.RightToLeft;
            bar.Height = 52;
            bar.Padding = new Padding(10, 8, 10, 6);
            var ok = new Button();
            ok.Text = "确定";
            ok.Size = new Size(96, 32);
            ok.BackColor = Color.FromArgb(31, 78, 121);
            ok.ForeColor = Color.White;
            ok.FlatStyle = FlatStyle.Flat;
            ok.FlatAppearance.BorderSize = 0;
            ok.Click += delegate
            {
                Kind = (_kind.Text ?? "").Trim();
                Content = (_content.Text ?? "").Trim();
                TolUp = (_up.Text ?? "").Trim();
                TolLow = (_low.Text ?? "").Trim();
                ITText = (_it.Text ?? "").Trim();
                GtolText = (_gtol.Text ?? "").Trim();
                Note = (_note.Text ?? "").Trim();
                if (string.IsNullOrEmpty(Kind)) Kind = "尺寸特征";
                if (string.IsNullOrEmpty(Content))
                {
                    MessageBox.Show(this, "「尺寸特征（内容）」不能为空，例如 ⌀5 H8 / M6 / 槽 3×5。", "工程图QC");
                    return;
                }
                DialogResult = DialogResult.OK;
                Close();
            };
            var cancel = new Button();
            cancel.Text = "取消";
            cancel.Size = new Size(96, 32);
            cancel.FlatStyle = FlatStyle.Flat;
            cancel.Click += delegate { DialogResult = DialogResult.Cancel; Close(); };
            bar.Controls.Add(ok);
            bar.Controls.Add(cancel);
            Controls.Add(bar);
        }

        static TextBox NewBox()
        {
            var t = new TextBox();
            t.Dock = DockStyle.Fill;
            return t;
        }

        static void AddRow(TableLayoutPanel root, string label, Control ctl)
        {
            var lb = new Label();
            lb.Text = label;
            lb.TextAlign = ContentAlignment.MiddleLeft;
            lb.AutoSize = false;
            lb.Dock = DockStyle.Fill;
            root.RowStyles.Add(new RowStyle(SizeType.Absolute, 40F));
            root.Controls.Add(lb);
            root.Controls.Add(ctl);
        }

        public void LoadFrom(string kind, string content, string tol, string it, string gtol, string note)
        {
            try
            {
                _kind.Text = string.IsNullOrEmpty(kind) ? "尺寸特征" : kind;
                _content.Text = content ?? "";
                if (!string.IsNullOrEmpty(tol))
                {
                    string t = tol;
                    int p = t.IndexOf("下");
                    if (p > 0)
                    {
                        _up.Text = t.Substring(0, p).Replace("上", "").Trim();
                        _low.Text = t.Substring(p + 1).Trim();
                    }
                    else _up.Text = t;
                }
                _it.Text = it ?? "";
                _gtol.Text = gtol ?? "";
                _note.Text = note ?? "";
            }
            catch { }
        }
    }
}
