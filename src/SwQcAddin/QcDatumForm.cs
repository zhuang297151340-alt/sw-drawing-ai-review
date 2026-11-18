using System;
using System.Collections.Generic;
using System.Drawing;
using System.Windows.Forms;

namespace SwQc
{
    /// <summary>基准表（美化版）：基准 / 所在视图 / 指向要素 / 尺寸（可选可输入）/ 来源</summary>
    public class DatumRow
    {
        public string Letter = "", View = "", Auto = "", Near = "", Coord = "", Manual = "";
    }

    public class QcDatumForm : Form
    {

        readonly string _dwg;
        readonly List<DatumRow> _rows;
        readonly List<string> _dims;
        readonly Panel _body = new Panel();
        readonly Label _sum = new Label();
        readonly Label _chipA = new Label();
        readonly Label _chipH = new Label();
        readonly Label _chipU = new Label();
        readonly List<ComboBox> _kindCombos = new List<ComboBox>();
        readonly List<ComboBox> _sizeCombos = new List<ComboBox>();
        static readonly string[] Kinds = { "未指定", "平面", "轴线", "圆柱面" };
        static readonly Font FO = new Font("Microsoft YaHei", 9F);
        static readonly Font FOB = new Font("Microsoft YaHei", 9F, FontStyle.Bold);
        static readonly Color INK = Color.FromArgb(38, 48, 60);
        static readonly Color MUT = Color.FromArgb(110, 122, 138);
        static readonly Color BRAND = Color.FromArgb(31, 78, 121);

        public QcDatumForm(string dwgNo, List<DatumRow> rows, List<string> dimContents)
        {
            _dwg = dwgNo;
            _rows = rows ?? new List<DatumRow>();
            _dims = dimContents ?? new List<string>();
            Text = "基准表";
            StartPosition = FormStartPosition.CenterParent;
            FormBorderStyle = FormBorderStyle.FixedDialog;
            MaximizeBox = false; MinimizeBox = false;
            ClientSize = new Size(1000, 560);
            BackColor = Color.FromArgb(245, 247, 251);
            Font = FO;

            var title = new Label();
            title.Text = "基准表";
            title.Font = new Font("Microsoft YaHei", 15F, FontStyle.Bold);
            title.ForeColor = BRAND;
            title.AutoSize = true;
            title.Location = new Point(24, 18);
            Controls.Add(title);

            var sub = new Label();
            sub.Text = "指向要素：平面 / 轴线 / 圆柱面；尺寸可下拉选择，也可直接输入；来源由程序自动判断";
            sub.ForeColor = MUT;
            sub.AutoSize = true;
            sub.Location = new Point(26, 50);
            Controls.Add(sub);

            _sum.AutoSize = true; _sum.Visible = false;
            _sum.Location = new Point(26, 78);
            _sum.ForeColor = MUT;
            Controls.Add(_sum);

            _chipA.AutoSize = false; _chipA.Size = new Size(130, 24);
            _chipA.Location = new Point(26, 78); _chipA.TextAlign = ContentAlignment.MiddleCenter;
            _chipA.BackColor = Color.FromArgb(232, 240, 252); _chipA.ForeColor = Color.FromArgb(31, 78, 121);
            _chipA.Font = new Font("Microsoft YaHei", 9F, FontStyle.Bold); Controls.Add(_chipA);
            _chipH.AutoSize = false; _chipH.Size = new Size(100, 24);
            _chipH.Location = new Point(164, 78); _chipH.TextAlign = ContentAlignment.MiddleCenter;
            _chipH.BackColor = Color.FromArgb(230, 246, 236); _chipH.ForeColor = Color.FromArgb(30, 132, 73);
            _chipH.Font = new Font("Microsoft YaHei", 9F, FontStyle.Bold); Controls.Add(_chipH);
            _chipU.AutoSize = false; _chipU.Size = new Size(100, 24);
            _chipU.Location = new Point(272, 78); _chipU.TextAlign = ContentAlignment.MiddleCenter;
            _chipU.BackColor = Color.FromArgb(255, 247, 224); _chipU.ForeColor = Color.FromArgb(190, 103, 0);
            _chipU.Font = new Font("Microsoft YaHei", 9F, FontStyle.Bold); Controls.Add(_chipU);
            _body.Location = new Point(20, 112);
            _body.Size = new Size(ClientSize.Width - 40, ClientSize.Height - 190);
            _body.AutoScroll = true;
            _body.BackColor = Color.White;
            _body.BorderStyle = BorderStyle.FixedSingle;
            Controls.Add(_body);

            BuildBody();

            var cancel = new Button();
            cancel.Text = "取消"; cancel.Size = new Size(104, 34);
            cancel.FlatStyle = FlatStyle.Flat; cancel.FlatAppearance.BorderColor = Color.FromArgb(200, 208, 218);
            cancel.BackColor = Color.White;
            cancel.Location = new Point(ClientSize.Width - 140, ClientSize.Height - 58);
            cancel.Click += delegate { DialogResult = DialogResult.Cancel; Close(); };
            Controls.Add(cancel);

            var ok = new Button();
            ok.Text = "确认"; ok.Size = new Size(104, 34);
            ok.FlatStyle = FlatStyle.Flat; ok.FlatAppearance.BorderSize = 0;
            ok.BackColor = BRAND; ok.ForeColor = Color.White;
            ok.Location = new Point(ClientSize.Width - 258, ClientSize.Height - 58);
            ok.Click += delegate { SaveAll(); };
            Controls.Add(ok);

            var tip = new Label();
            tip.Text = "修改后「确认」保存；基准指向变化立即同步到尺寸表备注";
            tip.ForeColor = MUT; tip.AutoSize = true;
            tip.Location = new Point(24, ClientSize.Height - 48);
            Controls.Add(tip);
        }

        void BuildBody()
        {
            int y = 0;
            string[] htxt = { "基准", "所在视图", "指向要素", "尺寸（下拉/可填）", "来源" };
            int[] hx = { 18, 100, 280, 460, 760 };
            var hdr = new Panel();
            hdr.Location = new Point(0, 0); hdr.Size = new Size(_body.Width - 20, 32);
            hdr.BackColor = Color.FromArgb(214, 228, 240);
            for (int i = 0; i < htxt.Length; i++)
            {
                var l = new Label();
                l.Text = htxt[i]; l.Font = FOB; l.ForeColor = Color.FromArgb(26, 64, 110);
                l.AutoSize = false; l.Size = new Size(160, 24); l.Location = new Point(hx[i], 6);
                hdr.Controls.Add(l);
            }
            _body.Controls.Add(hdr);
            y = 32;

            foreach (DatumRow r in _rows)
            {
                var row = new Panel();
                row.Location = new Point(0, y); row.Size = new Size(_body.Width - 20, 44);
                row.BackColor = (_rows.IndexOf(r) % 2 == 0) ? Color.White : Color.FromArgb(239, 245, 251);
                row.BorderStyle = BorderStyle.None;
                _body.Controls.Add(row);

                AddText(row, 18, 12, r.Letter, FOB);
                AddText(row, 100, 12, r.View ?? "", FO);

                var kind = new ComboBox();
                kind.DropDownStyle = ComboBoxStyle.DropDownList;
                kind.Items.AddRange(Kinds);
                kind.Size = new Size(150, 26); kind.Location = new Point(280, 9);
                string ks = string.IsNullOrEmpty(r.Manual) ? "未指定" : r.Manual;
                int ki = kind.Items.IndexOf(ks); kind.SelectedIndex = ki >= 0 ? ki : 0;
                row.Controls.Add(kind);
                _kindCombos.Add(kind);

                var sizeCombo = new ComboBox();
                sizeCombo.DropDownStyle = ComboBoxStyle.DropDown;   // 可选可输入
                sizeCombo.Items.Add("（自动）");
                foreach (string dn in _dims) if (!string.IsNullOrEmpty(dn) && !sizeCombo.Items.Contains(dn)) sizeCombo.Items.Add(dn);
                string near = r.Near ?? "";
                if (!string.IsNullOrEmpty(near))
                {
                    int cut = near.IndexOf("（距离");
                    if (cut > 0) near = near.Substring(0, cut).Trim();
                }
                sizeCombo.Text = string.IsNullOrEmpty(near) ? "（自动）" : near;
                sizeCombo.Size = new Size(280, 26); sizeCombo.Location = new Point(460, 9);
                row.Controls.Add(sizeCombo);
                _sizeCombos.Add(sizeCombo);

                bool manual = !string.IsNullOrEmpty(r.Manual) || (r.Near ?? "").Contains("人工指定");
                AddText(row, 760, 12, manual ? "人工指定" : "图纸", FO, manual ? Color.FromArgb(46, 125, 50) : INK);

                y += 44;
            }
            if (_rows.Count == 0)
            {
                var l = new Label();
                l.Text = "本图未读到基准符号（A/B…）"; l.ForeColor = MUT; l.AutoSize = true; l.Location = new Point(18, y + 14);
                _body.Controls.Add(l);
            }
            UpdateSum();
            foreach (ComboBox cb in _kindCombos) cb.SelectedIndexChanged += delegate { UpdateSum(); };
            foreach (ComboBox cb in _sizeCombos) cb.TextChanged += delegate { UpdateSum(); };
        }

        static void AddText(Panel row, int x, int y, string text, Font font) { AddText(row, x, y, text, font, INK); }
        static void AddText(Panel row, int x, int y, string text, Font font, Color color)
        {
            var l = new Label();
            l.Text = text ?? ""; l.Font = font; l.ForeColor = color;
            l.AutoSize = false; l.Size = new Size(170, 24); l.Location = new Point(x, y);
            row.Controls.Add(l);
        }

        void UpdateSum()
        {
            int spec = 0;
            for (int i = 0; i < _kindCombos.Count; i++)
            {
                string kind = Convert.ToString(_kindCombos[i].SelectedItem ?? "");
                string size = (_sizeCombos[i].Text ?? "").Trim();
                bool ok = (kind != "" && kind != "未指定") || (size.Length > 0 && size != "（自动）");
                if (ok) spec++;
            }
            _sum.Text = "全部 " + _kindCombos.Count + " 个基准    已指定 " + spec + "    待指定 " + (_kindCombos.Count - spec);
            _chipA.Text = "全部 " + _kindCombos.Count + " 个基准";
            _chipH.Text = "已指定 " + spec;
            _chipU.Text = "待指定 " + (_kindCombos.Count - spec);
        }

        void SaveAll()
        {
            try
            {
                for (int i = 0; i < _kindCombos.Count && i < _rows.Count; i++)
                {
                    DatumRow r = _rows[i];
                    string kind = Convert.ToString(_kindCombos[i].SelectedItem ?? "");
                    if (kind == "未指定") kind = "";
                    string size = (_sizeCombos[i].Text ?? "").Trim();
                    size = size.Replace("（人工指定）", "").Replace("（自动）", "").Replace("人工指定", "").Trim();
                    // 只修改当前窗口会话；关闭窗口后不恢复，重读图纸时也全部清空。
                    r.Manual = kind;
                    r.Near = size;
                }
                DialogResult = DialogResult.OK;
                Close();
            }
            catch (Exception ex) { MessageBox.Show(this, "保存失败：" + ex.Message); }
        }
    }
}
