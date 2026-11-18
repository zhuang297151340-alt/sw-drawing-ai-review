using System;
using System.Collections.Generic;
using System.Drawing;
using System.Text;
using System.Windows.Forms;

namespace SwQc
{
    /// <summary>逐条审核详情：把原文、风险、证据和建议分区显示。</summary>
    public sealed class QcDetailView : UserControl
    {
        readonly FlowLayoutPanel _flow;
        readonly Label _kicker;
        readonly Label _title;
        readonly Label _badge;
        readonly Label _original;
        readonly Label _result;
        readonly Label _evidence;
        readonly Label _suggestion;
        readonly List<Control> _blocks = new List<Control>();
        string _text = "";

        public QcDetailView()
        {
            SetStyle(ControlStyles.OptimizedDoubleBuffer | ControlStyles.AllPaintingInWmPaint, true);
            Font = new Font("Microsoft YaHei UI", 10f, FontStyle.Regular);
            BackColor = Color.White;
            _flow = new FlowLayoutPanel();
            _flow.Dock = DockStyle.Fill;
            _flow.FlowDirection = FlowDirection.TopDown;
            _flow.WrapContents = false;
            _flow.AutoScroll = true;
            _flow.Padding = new Padding(16, 14, 16, 16);
            _flow.BackColor = Color.White;
            Controls.Add(_flow);

            var header = new Panel();
            header.Height = 96;
            header.Margin = new Padding(0, 0, 0, 12);
            header.BackColor = Color.White;
            _kicker = new Label();
            _kicker.AutoSize = true;
            _kicker.Font = new Font("Microsoft YaHei UI", 8.5f, FontStyle.Regular);
            _kicker.ForeColor = Color.FromArgb(107, 122, 141);
            _kicker.Text = "审核详情";
            _kicker.Location = new Point(0, 0);
            header.Controls.Add(_kicker);
            _title = new Label();
            _title.AutoEllipsis = true;
            _title.Font = new Font("Microsoft YaHei UI", 10.75f, FontStyle.Bold);
            _title.ForeColor = Color.FromArgb(31, 78, 121);
            _title.Location = new Point(0, 22);
            _title.Height = 38;
            _title.TextAlign = ContentAlignment.TopLeft;
            header.Controls.Add(_title);
            _badge = new Label();
            _badge.AutoSize = true;
            _badge.Font = new Font("Microsoft YaHei UI", 8.5f, FontStyle.Bold);
            _badge.Padding = new Padding(10, 5, 10, 5);
            _badge.Location = new Point(0, 64);
            header.Controls.Add(_badge);
            _flow.Controls.Add(header);
            _blocks.Add(header);

            _original = AddSection("图纸原文 / 审核对象", Color.FromArgb(247, 249, 252));
            _result = AddSection("审核结果与风险", Color.FromArgb(255, 249, 240));
            _evidence = AddSection("关联对象与判定依据", Color.FromArgb(246, 249, 253));
            _suggestion = AddSection("修改建议", Color.FromArgb(242, 250, 246));
            Resize += delegate { ResizeBlocks(); };
            RenderText("");
        }

        Label AddSection(string caption, Color back)
        {
            var p = new Panel();
            p.AutoSize = false;
            p.Padding = new Padding(15, 12, 15, 14);
            p.Margin = new Padding(0, 0, 0, 12);
            p.BackColor = back;
            p.BorderStyle = BorderStyle.FixedSingle;
            p.Paint += delegate(object sender, PaintEventArgs e)
            {
                using (var brush = new SolidBrush(Color.FromArgb(31, 78, 121)))
                    e.Graphics.FillRectangle(brush, 0, 0, 3, p.ClientSize.Height);
            };
            var h = new Label();
            h.AutoSize = true;
            h.Font = new Font("Microsoft YaHei UI", 9.25f, FontStyle.Bold);
            h.ForeColor = Color.FromArgb(31, 78, 121);
            h.Text = caption;
            h.Margin = new Padding(0, 0, 0, 9);
            var body = new Label();
            body.AutoSize = true;
            body.Font = new Font("Microsoft YaHei UI", 9.25f, FontStyle.Regular);
            body.ForeColor = Color.FromArgb(38, 52, 69);
            body.Padding = new Padding(0, 9, 0, 4);
            body.TextAlign = ContentAlignment.TopLeft;
            p.Controls.Add(h);
            p.Controls.Add(body);
            h.Location = new Point(15, 12);
            body.Location = new Point(15, 40);
            _flow.Controls.Add(p);
            _blocks.Add(p);
            return body;
        }

        public override string Text
        {
            get { return _text; }
            set { _text = value ?? ""; RenderText(_text); }
        }

        void RenderText(string value)
        {
            string level = "—", original = "", result = "", evidence = "", suggestion = "";
            string kind = "审核详情";
            var other = new StringBuilder();
            string[] lines = (value ?? "").Replace("\r", "").Split('\n');
            foreach (string raw in lines)
            {
                string line = raw.Trim();
                if (line.Length == 0) continue;
                string v;
                if (Take(line, new string[] { "等级：", "风险等级：" }, out v)) level = v;
                else if (Take(line, new string[] { "技术要求：" }, out v))
                { kind = "技术要求审核"; original = Append(original, v); }
                else if (Take(line, new string[] { "尺寸/几何公差：" }, out v))
                { kind = "尺寸 / 几何公差审核"; original = Append(original, v); }
                else if (Take(line, new string[] { "内容：" }, out v))
                { kind = "审核详情"; original = Append(original, v); }
                else if (Take(line, new string[] { "审核结果：", "审核结论：", "风险：" }, out v))
                    result = Append(result, v);
                else if (Take(line, new string[] { "依据：", "标准：", "证据：", "关联对象：", "IT等级：" }, out v))
                    evidence = Append(evidence, v);
                else if (Take(line, new string[] { "建议：", "修改建议：" }, out v))
                    suggestion = Append(suggestion, v);
                else other.AppendLine(line);
            }
            if (original.Length == 0 && other.Length > 0) original = other.ToString().Trim();
            else if (other.Length > 0) result = Append(result, other.ToString().Trim());
            _kicker.Text = kind;
            _title.Text = original.Length > 0 ? ShortTitle(original) : "审核详情";
            SetBadge(level);
            _original.Text = Empty(original);
            _result.Text = Empty(result);
            _evidence.Text = Empty(evidence);
            _suggestion.Text = Empty(suggestion);
            ResizeBlocks();
        }

        static bool Take(string line, string[] prefixes, out string value)
        {
            foreach (string p in prefixes)
                if (line.StartsWith(p, StringComparison.Ordinal))
                { value = line.Substring(p.Length).Trim(); return true; }
            value = ""; return false;
        }

        static string Append(string a, string b)
        {
            if (string.IsNullOrEmpty(b)) return a ?? "";
            return string.IsNullOrEmpty(a) ? b : a + "\r\n" + b;
        }

        static string Empty(string s) { return string.IsNullOrEmpty(s) ? "—" : s; }
        static string ShortTitle(string s)
        {
            string x = (s ?? "").Replace("\r", " ").Replace("\n", " ").Trim();
            return x.Length > 28 ? x.Substring(0, 28) + "…" : x;
        }

        void SetBadge(string level)
        {
            _badge.Text = "风险等级　" + Empty(level);
            Color fg = Color.FromArgb(107, 122, 141), bg = Color.FromArgb(238, 242, 246);
            if (level.Contains("严重")) { fg = Color.FromArgb(170, 45, 35); bg = Color.FromArgb(254, 235, 232); }
            else if (level.Contains("一般")) { fg = Color.FromArgb(176, 100, 10); bg = Color.FromArgb(255, 242, 218); }
            else if (level.Contains("轻微")) { fg = Color.FromArgb(90, 112, 45); bg = Color.FromArgb(239, 246, 224); }
            else if (level.Contains("通过")) { fg = Color.FromArgb(35, 125, 77); bg = Color.FromArgb(226, 246, 235); }
            _badge.ForeColor = fg; _badge.BackColor = bg;
        }

        void ResizeBlocks()
        {
            int w = Math.Max(220, _flow.ClientSize.Width - 42);
            foreach (Control c in _blocks) c.Width = w;
            _title.Width = w;
            foreach (Label l in new Label[] { _original, _result, _evidence, _suggestion })
            {
                l.MaximumSize = new Size(Math.Max(180, w - 32), 0);
                l.Width = Math.Max(180, w - 32);
                if (l.Parent != null)
                {
                    l.Parent.Width = w;
                    l.Parent.Height = l.Top + l.PreferredHeight + 17;
                }
            }
        }
    }
}
