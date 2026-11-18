using System;
using System.Collections.Generic;
using System.Drawing;
using System.Windows.Forms;

namespace SwQc
{
    /// <summary>几何公差归属表（v201 新界面）：一条公差一行；被测要素下拉；指定尺寸下拉可填；底部确认/取消</summary>
    public class QcGtolLinkForm : Form
    {
        public List<QcUnlinkedGtol> Items = new List<QcUnlinkedGtol>();
        public List<int> Assignment = new List<int>();
        public List<string> Custom = new List<string>();

        readonly QcReport _rep;
        readonly List<QcUnlinkedGtol> _all;
        readonly Panel _rows = new Panel();
        readonly Label _sum = new Label();
        readonly Label _chipA = new Label();
        readonly Label _chipH = new Label();
        readonly Label _chipU = new Label();
        readonly List<ComboBox> _targetCombos = new List<ComboBox>();
        readonly List<Label> _statusLabels = new List<Label>();
        readonly List<ComboBox> _featCombos = new List<ComboBox>();
        readonly List<Label> _geoms = new List<Label>();

        static readonly string[] Feats = { "轴心", "平面", "圆柱面", "表面", "未判定" };

        static string NameOf(string sym)
        {
            switch (sym ?? "")
            {
                case "⌖": case "◎": return "位置度";
                case "⌓": case "⌒": return "面轮廓度";
                case "▱": return "平面度";
                case "—": return "直线度";
                case "○": return "圆度";
                case "⌭": return "圆柱度";
                case "⊥": return "垂直度";
                case "∥": return "平行度";
                case "∠": return "倾斜度";
                case "⌯": return "同轴度";
                default: return "几何公差";
            }
        }

        public QcGtolLinkForm(QcReport rep) : this(rep, BuildAll(rep)) { }

        static List<QcUnlinkedGtol> BuildAll(QcReport rep)
        {
            var list = new List<QcUnlinkedGtol>();
            try
            {
                foreach (QcDimRow r in (rep.DimRows ?? new List<QcDimRow>()))
                {
                    if (r == null || r.Gtols == null) continue;
                    foreach (QcGtolLine g in r.Gtols)
                    {
                        if (g == null) continue;
                        list.Add(new QcUnlinkedGtol
                        {
                            Sym = g.Sym, Val = g.Val, Dat1 = g.Dat1, Dat2 = g.Dat2, Dat3 = g.Dat3, Mod = g.Mod,
                            Target = r.Content, MeasuredFeature = g.MeasuredFeature,
                            GtolId = g.GtolId, RawId = g.RawId, GroupId = g.GroupId, FrameOrder = g.FrameOrder,
                            FrameCount = g.FrameCount, FrameType = g.FrameType,
                            FrameReason = g.FrameReason, ZoneType = g.ZoneType
                        });
                    }
                }
                foreach (QcUnlinkedGtol u in (rep.UnlinkedGtols ?? new List<QcUnlinkedGtol>()))
                    if (u != null) list.Add(u);
            }
            catch { }
            return list;
        }

        public QcGtolLinkForm(QcReport rep, List<QcUnlinkedGtol> allGtols)
        {
            _rep = rep;
            _all = allGtols ?? new List<QcUnlinkedGtol>();
            Text = "几何公差归属表";
            StartPosition = FormStartPosition.CenterParent;
            FormBorderStyle = FormBorderStyle.FixedDialog;
            MaximizeBox = false; MinimizeBox = false;
            ClientSize = new Size(1000, 640);
            BackColor = Color.FromArgb(245, 247, 251);
            Font = new Font("Microsoft YaHei", 9F);

            var head = new Label();
            head.Text = "几何公差归属表：有指定尺寸 = 已挂；没有 = 未挂；复合框只占 1 行";
            head.Font = new Font("Microsoft YaHei", 15F, FontStyle.Bold); head.ForeColor = Color.FromArgb(31, 78, 121);
            head.AutoSize = true;
            head.Location = new Point(16, 12);
            Controls.Add(head);

            _sum.AutoSize = true;
            _sum.Location = new Point(16, 40);
            _sum.ForeColor = Color.FromArgb(90, 100, 115);
            Controls.Add(_sum);

            _chipA.AutoSize = false; _chipA.Size = new Size(120, 24);
            _chipA.Location = new Point(16, 40); _chipA.TextAlign = ContentAlignment.MiddleCenter;
            _chipA.BackColor = Color.FromArgb(232, 240, 252); _chipA.ForeColor = Color.FromArgb(31, 78, 121);
            _chipA.Font = new Font("Microsoft YaHei", 9F, FontStyle.Bold); Controls.Add(_chipA);
            _chipH.AutoSize = false; _chipH.Size = new Size(100, 24);
            _chipH.Location = new Point(144, 40); _chipH.TextAlign = ContentAlignment.MiddleCenter;
            _chipH.BackColor = Color.FromArgb(230, 246, 236); _chipH.ForeColor = Color.FromArgb(30, 132, 73);
            _chipH.Font = new Font("Microsoft YaHei", 9F, FontStyle.Bold); Controls.Add(_chipH);
            _chipU.AutoSize = false; _chipU.Size = new Size(100, 24);
            _chipU.Location = new Point(252, 40); _chipU.TextAlign = ContentAlignment.MiddleCenter;
            _chipU.BackColor = Color.FromArgb(255, 247, 224); _chipU.ForeColor = Color.FromArgb(190, 103, 0);
            _chipU.Font = new Font("Microsoft YaHei", 9F, FontStyle.Bold); Controls.Add(_chipU);
            _rows.Location = new Point(12, 70);
            _rows.Size = new Size(976, 500);
            _rows.AutoScroll = true;
            _rows.BorderStyle = BorderStyle.FixedSingle;
            Controls.Add(_rows);

            BuildRows();

            var cancel = new Button();
            cancel.Text = "取消"; cancel.Size = new Size(104, 34);
            cancel.FlatStyle = FlatStyle.Flat;
            cancel.Location = new Point(ClientSize.Width - 120, ClientSize.Height - 50);
            cancel.Click += delegate { DialogResult = DialogResult.Cancel; Close(); };
            Controls.Add(cancel);

            var ok = new Button();
            ok.Text = "确认"; ok.Size = new Size(104, 34);
            ok.BackColor = Color.FromArgb(31, 78, 121); ok.ForeColor = Color.White;
            ok.FlatStyle = FlatStyle.Flat; ok.FlatAppearance.BorderSize = 0;
            ok.Location = new Point(ClientSize.Width - 236, ClientSize.Height - 50);
            ok.Click += delegate { Collect(); DialogResult = DialogResult.OK; Close(); };
            Controls.Add(ok);

            var note = new Label();
            note.AutoSize = true;
            note.ForeColor = Color.FromArgb(110, 122, 138);
            note.Text = "修改后点「确认」保存，并立即同步到尺寸公差表（不自动检查）";
            note.Location = new Point(16, ClientSize.Height - 42);
            Controls.Add(note);
        }

        void BuildRows()
        {
            _rows.Controls.Clear();
            _targetCombos.Clear(); _statusLabels.Clear(); _geoms.Clear(); _featCombos.Clear();
            var dimChoices = new List<string>();
            try
            {
                foreach (QcDimRow r in (_rep.DimRows ?? new List<QcDimRow>()))
                    if (r != null && !string.IsNullOrEmpty(r.Content) && !dimChoices.Contains(r.Content))
                        dimChoices.Add(r.Content);
            }
            catch { }

            // QcDocument returns one item per compound frame with Members.
            // Expand for rendering, then regroup by GroupId so it remains one row.
            var displayItems = new List<QcUnlinkedGtol>();
            foreach (QcUnlinkedGtol source in _all)
            {
                if (source == null) continue;
                if (source.Members != null && source.Members.Count > 0)
                {
                    foreach (QcGtolLine m in source.Members)
                    {
                        if (m == null) continue;
                        displayItems.Add(new QcUnlinkedGtol
                        {
                            GtolId = m.GtolId, RawId = m.RawId,
                            GroupId = string.IsNullOrEmpty(m.GroupId) ? source.GroupId : m.GroupId,
                            FrameOrder = m.FrameOrder, Sym = m.Sym, Val = m.Val,
                            FrameCount = m.FrameCount, FrameType = m.FrameType,
                            FrameReason = m.FrameReason, ZoneType = m.ZoneType,
                            Dat1 = m.Dat1, Dat2 = m.Dat2, Dat3 = m.Dat3, Mod = m.Mod,
                            MeasuredFeature = m.MeasuredFeature, Target = source.Target,
                            Label = source.Label, View = source.View
                        });
                    }
                }
                else displayItems.Add(source);
            }
            displayItems.Sort(delegate(QcUnlinkedGtol a, QcUnlinkedGtol b)
            {
                int c = string.Compare(a.GroupId ?? "", b.GroupId ?? "", StringComparison.OrdinalIgnoreCase);
                return c != 0 ? c : a.FrameOrder.CompareTo(b.FrameOrder);
            });
            var groups = new List<List<QcUnlinkedGtol>>();
            var idx = new Dictionary<string, int>();
            foreach (QcUnlinkedGtol g in displayItems)
            {
                if (g == null) continue;
                string key = string.IsNullOrEmpty(g.GroupId) ? ("S" + groups.Count + "_" + g.GetHashCode()) : ("G" + g.GroupId);
                int gi;
                if (!idx.TryGetValue(key, out gi)) { gi = groups.Count; groups.Add(new List<QcUnlinkedGtol>()); idx[key] = gi; }
                groups[gi].Add(g);
            }

            // 表头
            var hdr = new Panel();
            hdr.Location = new Point(0, 0);
            hdr.Size = new Size(950, 30);
            hdr.BackColor = Color.FromArgb(214, 228, 240);
            _rows.Controls.Add(hdr);
            string[] htxt = { "序号", "几何公差", "被测要素", "指定尺寸（下拉/可填）", "状态" };
            int[] hx = { 8, 50, 360, 470, 730 };
            for (int hi = 0; hi < htxt.Length; hi++)
            {
                var hl = new Label();
                hl.Text = htxt[hi];
                hl.AutoSize = false; hl.Size = new Size((hi == 1 ? 300 : (hi == 3 ? 210 : 100)), 24);
                hl.Location = new Point(hx[hi], 4);
                hl.Font = new Font("Microsoft YaHei", 9F, FontStyle.Bold);
                hl.ForeColor = Color.FromArgb(20, 60, 110);
                hdr.Controls.Add(hl);
            }

            int y = 30, no = 0;
            foreach (List<QcUnlinkedGtol> grp in groups)
            {
                if (grp.Count == 0) continue;
                no++;
                QcUnlinkedGtol g0 = grp[0];
                var row = new Panel();
                int h = 52 + (grp.Count - 1) * 26;
                row.Location = new Point(0, y);
                row.Size = new Size(950, h);
                row.BackColor = (no % 2 == 0) ? Color.White : Color.FromArgb(239, 245, 251);
                row.BorderStyle = BorderStyle.None;
                _rows.Controls.Add(row);

                var nol = new Label();
                nol.Text = no.ToString(); nol.AutoSize = false; nol.Size = new Size(40, 24);
                nol.Location = new Point(8, 14); nol.TextAlign = ContentAlignment.MiddleCenter;
                row.Controls.Add(nol);

                // 组内每个框：几何公差 + 各自被测要素下拉
                for (int k = 0; k < grp.Count; k++)
                {
                    QcUnlinkedGtol gk = grp[k];
                    var geom = new Label();
                    geom.Text = NameOf(gk.Sym) + " " + gk.Display;
                    geom.AutoSize = false; geom.Size = new Size(300, 24);
                    geom.Location = new Point(50, 14 + k * 26);
                    geom.AutoEllipsis = true; geom.TextAlign = ContentAlignment.MiddleLeft;
                    row.Controls.Add(geom);
                    _geoms.Add(geom);

                    var feat = new ComboBox();
                    feat.DropDownStyle = ComboBoxStyle.DropDownList;
                    feat.Items.AddRange(Feats);
                    feat.Size = new Size(100, 24);
                    feat.Location = new Point(360, 13 + k * 26);
                    string f0 = gk.MeasuredFeature ?? "";
                    if (f0.Length == 0 || f0 == "未判定")
                    {
                        string sy0 = gk.Sym ?? "";
                        bool diaTarget = !string.IsNullOrEmpty(gk.Target)
                            && (gk.Target.Contains("⌀") || gk.Target.Contains("φ") || gk.Target.Contains("Ø"));
                        if ((sy0 == "—" || sy0 == "○" || sy0 == "⌭") && diaTarget) f0 = "圆柱面";
                        else if (sy0 == "▱" || sy0 == "□") f0 = "平面";
                        else if (sy0 == "⌓" || sy0 == "⌒" || sy0 == "—" || sy0 == "○" || sy0 == "⌭") f0 = "表面";
                        else if (!string.IsNullOrEmpty(gk.Target) && (gk.Target.Contains("⌀") || gk.Target.Contains("φ"))) f0 = "轴心";
                        else if (!string.IsNullOrEmpty(gk.Target)) f0 = "平面";
                        else f0 = "未判定";
                    }
                    int fi = feat.Items.IndexOf(f0);
                    feat.SelectedIndex = fi >= 0 ? fi : feat.Items.IndexOf("未判定");
                    feat.Tag = gk;
                    _featCombos.Add(feat);
                    row.Controls.Add(feat);
                }

                // 整组一个指定尺寸（置顶）
                var combo = new ComboBox();
                combo.DropDownStyle = ComboBoxStyle.DropDown;
                combo.Size = new Size(250, 24);
                combo.Location = new Point(470, 13);
                combo.Items.Add("（不指定）");
                foreach (string d in dimChoices) combo.Items.Add(d);
                string cur = g0.Target ?? "";
                if (string.IsNullOrEmpty(cur))
                {
                    try
                    {
                        foreach (QcDimRow r in (_rep.DimRows ?? new List<QcDimRow>()))
                        {
                            if (r == null || r.Gtols == null) continue;
                            foreach (QcGtolLine gl in r.Gtols)
                            {
                                if (gl == null) continue;
                                if (QcRunner.GtolKey(gl.Sym, gl.Val, gl.Dat1, gl.Dat2, gl.Mod, gl.Dat3) ==
                                    QcRunner.GtolKey(g0.Sym, g0.Val, g0.Dat1, g0.Dat2, g0.Mod, g0.Dat3)) { cur = r.Content; break; }
                            }
                            if (!string.IsNullOrEmpty(cur)) break;
                        }
                    }
                    catch { }
                }
                combo.Text = string.IsNullOrEmpty(cur) ? "（不指定）" : cur;
                row.Controls.Add(combo);
                _targetCombos.Add(combo);

                var st = new Label();
                st.AutoSize = false; st.Size = new Size(80, 24);
                st.Location = new Point(730, 13); st.TextAlign = ContentAlignment.MiddleCenter;
                row.Controls.Add(st);
                _statusLabels.Add(st);
                combo.TextChanged += delegate { UpdateStatus(); };
                combo.SelectedIndexChanged += delegate { UpdateStatus(); };

                var rep = new QcUnlinkedGtol
                {
                    Sym = g0.Sym, Val = g0.Val, Dat1 = g0.Dat1, Dat2 = g0.Dat2, Dat3 = g0.Dat3, Mod = g0.Mod,
                    Target = g0.Target, MeasuredFeature = g0.MeasuredFeature,
                    GtolId = g0.GtolId, RawId = g0.RawId, GroupId = g0.GroupId, FrameOrder = g0.FrameOrder,
                    FrameCount = g0.FrameCount, FrameType = g0.FrameType,
                    FrameReason = g0.FrameReason, ZoneType = g0.ZoneType
                };
                foreach (QcUnlinkedGtol gg in grp)
                {
                    var line = new QcGtolLine();
                    line.Sym = gg.Sym; line.Val = gg.Val; line.Dat1 = gg.Dat1; line.Dat2 = gg.Dat2; line.Dat3 = gg.Dat3; line.Mod = gg.Mod;
                    line.GtolId = gg.GtolId; line.RawId = gg.RawId; line.GroupId = gg.GroupId; line.MeasuredFeature = gg.MeasuredFeature;
                    line.FrameOrder = gg.FrameOrder; line.FrameCount = gg.FrameCount;
                    line.FrameType = gg.FrameType; line.FrameReason = gg.FrameReason; line.ZoneType = gg.ZoneType;
                    double vv; if (double.TryParse(gg.Val, out vv)) line.ValueMm = vv;
                    rep.Members.Add(line);
                }
                Items.Add(rep);
                y += h;
            }
            UpdateStatus();
        }

        void UpdateStatus()
        {
            int hung = 0;
            for (int i = 0; i < _statusLabels.Count; i++)
            {
                string t = (_targetCombos[i].Text ?? "").Trim();
                bool has = t.Length > 0 && t != "（不指定）";
                if (has) hung++;
                _statusLabels[i].Text = has ? "已挂" : "未挂";
                _statusLabels[i].ForeColor = has ? Color.FromArgb(30, 132, 73) : Color.FromArgb(190, 103, 0);
                _statusLabels[i].BackColor = has ? Color.FromArgb(230, 246, 236) : Color.FromArgb(255, 247, 224);
            }
            int all = _statusLabels.Count;
            _sum.Text = "全部 " + all + " 条   已挂 " + hung + "   未挂 " + (all - hung);
            _chipA.Text = "全部 " + all + " 条";
            _chipH.Text = "已挂 " + hung;
            _chipU.Text = "未挂 " + (all - hung);
        }

        void Collect()
        {
            foreach (ComboBox cb in _featCombos)
            {
                QcUnlinkedGtol gk = cb.Tag as QcUnlinkedGtol;
                if (gk == null) continue;
                gk.MeasuredFeature = Convert.ToString(cb.SelectedItem ?? "");
            }
            for (int ii = 0; ii < Items.Count; ii++)
            {
                QcUnlinkedGtol it = Items[ii];
                if (it == null) continue;
                // 组内每个成员：按 RawId 映射；页面只有一个下拉时取组内第一个框
                if (it.Members != null && it.Members.Count > 0)
                {
                    foreach (QcGtolLine mm in it.Members)
                    {
                        foreach (ComboBox cb in _featCombos)
                        {
                            QcUnlinkedGtol gk = cb.Tag as QcUnlinkedGtol;
                            if (gk == null || mm == null) continue;
                            if ((!string.IsNullOrEmpty(gk.GtolId) && gk.GtolId == mm.GtolId)
                                || (!string.IsNullOrEmpty(gk.RawId) && gk.RawId == mm.RawId))
                                mm.MeasuredFeature = Convert.ToString(cb.SelectedItem ?? "");
                        }
                    }
                    it.MeasuredFeature = it.Members[0].MeasuredFeature;
                }
                else if (ii < _featCombos.Count)
                {
                    it.MeasuredFeature = Convert.ToString(_featCombos[ii].SelectedItem ?? "");
                }
            }
            foreach (QcUnlinkedGtol it in Items)
            {
                if (it.Members == null) continue;
                foreach (QcGtolLine mm in it.Members)
                    foreach (ComboBox cb in _featCombos)
                    {
                        QcUnlinkedGtol gk = cb.Tag as QcUnlinkedGtol;
                        if (gk == null || mm == null) continue;
                        if ((!string.IsNullOrEmpty(gk.GtolId) && gk.GtolId == mm.GtolId)
                            || ((gk.RawId ?? "") != "" && gk.RawId == mm.RawId))
                            mm.MeasuredFeature = Convert.ToString(cb.SelectedItem ?? "");
                        else if (string.IsNullOrEmpty(gk.GtolId) && gk.RawId == mm.RawId && gk.Sym == mm.Sym && gk.Val == mm.Val)
                            mm.MeasuredFeature = Convert.ToString(cb.SelectedItem ?? "");
                    }
            }
            Assignment.Clear();
            Custom.Clear();
            for (int i = 0; i < Items.Count; i++)
            {
                string t = (_targetCombos[i].Text ?? "").Trim();
                if (t == "（不指定）") t = "";
                int di = -1;
                if (t.Length > 0)
                {
                    try
                    {
                        for (int ri = 0; ri < (_rep.DimRows ?? new List<QcDimRow>()).Count; ri++)
                            if ((_rep.DimRows[ri].Content ?? "") == t) { di = ri; break; }
                    }
                    catch { }
                }
                Assignment.Add(di);
                Custom.Add(di < 0 ? t : "");
            }
        }
    }
}
