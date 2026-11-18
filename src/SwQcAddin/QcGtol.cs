using System;
using System.Collections.Generic;
using System.Drawing;
using System.Windows.Forms;

namespace SwQc
{
    // ========================================================================
    // 几何公差符号表（GB/T 1182）
    // ========================================================================
    static class GtolSymbols
    {
        public static readonly string[] Items = new string[]
        { "—", "▱", "○", "⌭", "⌒", "⌓", "∥", "⊥", "∠", "⌖", "◎", "⌯", "↗", "⌰" };

        public static readonly string[] Modifiers = new string[]
        { "Ⓔ", "Ⓜ", "Ⓛ", "Ⓕ", "Ⓣ", "Ⓟ", "CZ", "SZ", "SIM" };

        public static string NameOf(string sym)
        {
            switch (sym)
            {
                case "—": return "直线度";
                case "▱": return "平面度";
                case "○": return "圆度";
                case "⌭": return "圆柱度";
                case "⌒": return "线轮廓度";
                case "⌓": return "面轮廓度";
                case "∥": return "平行度";
                case "⊥": return "垂直度";
                case "∠": return "倾斜度";
                case "⌖": return "位置度";
                case "◎": return "同轴度";
                case "⌯": return "对称度";
                case "↗": return "圆跳动";
                case "⌰": return "全跳动";
                default: return "";
            }
        }
    }

    // ========================================================================
    // 符号盘：浮动小窗，4 列网格（只放符号，不带中文）
    // ========================================================================
    public class GtolPalette : Form
    {
        public string Selected = "";

        public GtolPalette()
        {
            Text = "公差";
            FormBorderStyle = FormBorderStyle.FixedToolWindow;
            StartPosition = FormStartPosition.Manual;
            ShowInTaskbar = false;
            TopMost = true;
            BackColor = Color.White;
            ClientSize = new Size(268, 168);

            var grid = new TableLayoutPanel();
            grid.Location = new Point(0, 0);
            grid.Size = new Size(268, 132);
            grid.ColumnCount = 4;
            grid.RowCount = 4;
            grid.Padding = new Padding(3);
            grid.BackColor = Color.White;
            for (int c = 0; c < 4; c++)
                grid.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 25f));
            for (int r = 0; r < 4; r++)
                grid.RowStyles.Add(new RowStyle(SizeType.Absolute, 31f));
            Controls.Add(grid);

            for (int i = 0; i < GtolSymbols.Items.Length && i < 16; i++)
            {
                string sym = GtolSymbols.Items[i];
                var b = new Button();
                b.Text = sym;
                b.Font = new Font("Segoe UI Symbol", 11f);
                b.FlatStyle = FlatStyle.Flat;
                b.FlatAppearance.BorderColor = Color.FromArgb(196, 203, 212);
                b.Margin = new Padding(1);
                b.Dock = DockStyle.Fill;
                b.Tag = sym;
                b.Click += delegate(object o, EventArgs e)
                {
                    Selected = Convert.ToString(((Button)o).Tag);
                    DialogResult = DialogResult.OK;
                    Close();
                };
                grid.Controls.Add(b, i % 4, i / 4);
            }

            var foot = new Label();
            foot.Text = "独立原则 = 不加修正符号";
            foot.Font = new Font("微软雅黑", 7f);
            foot.ForeColor = Color.FromArgb(102, 112, 133);
            foot.Location = new Point(0, 140);
            foot.Size = new Size(268, 18);
            foot.TextAlign = ContentAlignment.MiddleCenter;
            Controls.Add(foot);
        }
    }

    // ========================================================================
    // 几何公差框格：[符号][值][修正][基准1][基准2]，每段自带 × 分段删除
    // 修正段为空时显示一个小「＋」，点它选修正符号（用户 2026-10-24 要求）
    // ========================================================================
    public class GtolFrame : Panel
    {
        public TextBox TxtValue, TxtDat1, TxtDat2;
        private Button _sym, _modBtn;
        private string _mod = "";
        private readonly Font _f = new Font("微软雅黑", 8.5f);
        private readonly Font _fx = new Font("Microsoft Sans Serif", 7.5f);

        public GtolFrame()
        {
            Height = 22;
            BackColor = Color.White;
            BorderStyle = BorderStyle.FixedSingle;
            Size = new Size(214, 22);

            _sym = new Button();
            _sym.Text = "＋";
            _sym.Font = _f;
            _sym.ForeColor = Color.FromArgb(11, 74, 138);
            _sym.FlatStyle = FlatStyle.Flat;
            _sym.FlatAppearance.BorderSize = 0;
            _sym.Size = new Size(24, 20);
            _sym.Location = new Point(0, 0);
            _sym.BackColor = Color.White;
            _sym.Click += delegate { PickSymbol(); };
            Controls.Add(_sym);

            TxtValue = AddSeg(26, 52, "值", "公差值（可填 φ0.02 / 0.02）");

            AddSep(82);
            _modBtn = new Button();
            _modBtn.Text = "＋";
            _modBtn.Font = _f;
            _modBtn.ForeColor = Color.FromArgb(11, 74, 138);
            _modBtn.FlatStyle = FlatStyle.Flat;
            _modBtn.FlatAppearance.BorderSize = 0;
            _modBtn.Size = new Size(30, 20);
            _modBtn.Location = new Point(84, 0);
            _modBtn.BackColor = Color.White;
            _modBtn.Click += delegate { PickModifier(); };
            Controls.Add(_modBtn);

            TxtDat1 = AddSeg(118, 34, "基准", "基准 1");
            TxtDat2 = AddSeg(156, 34, "基准", "基准 2");
        }

        void AddSep(int x)
        {
            var sep = new Panel();
            sep.BackColor = Color.FromArgb(208, 213, 221);
            sep.Width = 1;
            sep.Height = 20;
            sep.Location = new Point(x, 0);
            Controls.Add(sep);
        }

        TextBox AddSeg(int x, int w, string ph, string tip)
        {
            AddSep(x - 2);
            var tb = new TextBox();
            tb.Font = _f;
            tb.BorderStyle = BorderStyle.None;
            tb.Size = new Size(w, 16);
            tb.Location = new Point(x, 3);
            tb.BackColor = Color.White;
            tb.TextAlign = HorizontalAlignment.Center;
            tb.Text = ph;
            tb.ForeColor = Color.FromArgb(152, 162, 179);
            // 2026-10-24：与计算面板一致 —— 聚焦高亮 + 关输入法，保证有可见反馈
            tb.ImeMode = ImeMode.Off;
            tb.Cursor = Cursors.IBeam;
            tb.BackColor = Color.FromArgb(255, 252, 240);
            tb.Enter += delegate
            {
                tb.BackColor = Color.FromArgb(255, 255, 214);
                if (tb.Text == ph) { tb.Text = ""; tb.ForeColor = Color.FromArgb(29, 78, 216); }
            };
            tb.Leave += delegate
            {
                tb.BackColor = Color.FromArgb(255, 252, 240);
                if (tb.Text.Length == 0) { tb.Text = ph; tb.ForeColor = Color.FromArgb(152, 162, 179); }
            };
            tb.Click += delegate
            {
                tb.Focus();
                tb.SelectionStart = tb.Text.Length;
            };
            var tipTxt = new ToolTip();
            tipTxt.SetToolTip(tb, tip);
            Controls.Add(tb);

            var x2 = new Label();
            x2.Text = "×";
            x2.Font = _fx;
            x2.ForeColor = Color.FromArgb(196, 203, 212);
            x2.AutoSize = true;
            x2.Location = new Point(x + w - 6, 3);
            x2.Cursor = Cursors.Hand;
            x2.Click += delegate
            {
                tb.Text = ph;
                tb.ForeColor = Color.FromArgb(152, 162, 179);
            };
            Controls.Add(x2);
            return tb;
        }

        public string Symbol
        {
            get { return _sym.Text == "＋" ? "" : _sym.Text; }
            set
            {
                _sym.Text = string.IsNullOrEmpty(value) ? "＋" : value;
                _sym.Font = string.IsNullOrEmpty(value) ? _f : new Font("Segoe UI Symbol", 10f);
            }
        }

        /// <summary>修正符号；空串 = 未加修正符号（独立原则）</summary>
        public string Modifier
        {
            get { return _mod; }
            set
            {
                _mod = value ?? "";
                _modBtn.Text = string.IsNullOrEmpty(_mod) ? "＋" : _mod;
                _modBtn.Font = string.IsNullOrEmpty(_mod) ? _f : new Font("Microsoft Sans Serif", 9f);
                _modBtn.ForeColor = string.IsNullOrEmpty(_mod)
                    ? Color.FromArgb(11, 74, 138) : Color.FromArgb(192, 16, 72);
            }
        }

        void PickSymbol()
        {
            using (var p = new GtolPalette())
            {
                p.Location = PointToScreen(new Point(0, Height));
                if (p.ShowDialog(this) != DialogResult.OK || p.Selected.Length == 0) return;
                if (p.Selected.StartsWith("MOD:")) Modifier = p.Selected.Substring(4);
                else Symbol = p.Selected;
            }
        }

        /// <summary>点「＋」选修正符号（菜单式，8 个 + 清除）</summary>
        void PickModifier()
        {
            var menu = new ContextMenuStrip();
            menu.Font = _f;
            menu.Items.Add(new ToolStripMenuItem("Ⓔ 包容要求"));
            menu.Items.Add(new ToolStripMenuItem("Ⓜ 最大实体"));
            menu.Items.Add(new ToolStripMenuItem("Ⓛ 最小实体"));
            menu.Items.Add(new ToolStripMenuItem("Ⓕ 自由状态"));
            menu.Items.Add(new ToolStripMenuItem("Ⓣ 相切平面"));
            menu.Items.Add(new ToolStripMenuItem("Ⓟ 延伸公差带"));
            menu.Items.Add(new ToolStripMenuItem("CZ 组合公差带"));
            menu.Items.Add(new ToolStripMenuItem("SZ 独立公差带"));
            menu.Items.Add(new ToolStripMenuItem("SIM 同时要求"));
            menu.Items.Add(new ToolStripSeparator());
            menu.Items.Add(new ToolStripMenuItem("清除（独立原则）"));
            menu.ItemClicked += delegate(object o, ToolStripItemClickedEventArgs e)
            {
                string t = e.ClickedItem.Text;
                if (t.StartsWith("清除")) { Modifier = ""; return; }
                int sp = t.IndexOf(' ');
                Modifier = sp > 0 ? t.Substring(0, sp) : t;
            };
            menu.Show(_modBtn, new Point(0, _modBtn.Height));
        }

        static string Val(TextBox tb, string ph)
        {
            return tb.Text == ph ? "" : tb.Text.Trim();
        }
        public string ValueText { get { return Val(TxtValue, "值"); } }
        public string Datum1 { get { return Val(TxtDat1, "基准"); } }
        public string Datum2 { get { return Val(TxtDat2, "基准"); } }

        /// <summary>取公差数值（mm），兼容 φ Φ Ø ⌀ Ф SΦ ± 与全角数字</summary>
        public double ToleranceMm()
        {
            string t = CalcPanel.Normalize(ValueText);
            if (t.Length == 0) return 0;
            double v;
            return double.TryParse(t, System.Globalization.NumberStyles.Float,
                    System.Globalization.CultureInfo.InvariantCulture, out v) ? v : 0;
        }
    }

    /// <summary>
    /// 几何公差"计入/涵盖/取严"规则引擎（2026-10-25）。
    /// 与 Python 端 GTOL_RADIAL_RULE / GTOL_CONTAINS 保持一致：
    ///   · 计入径向作用边界：直线度 —、圆度 ○、圆柱度 ⌭、同轴度 ◎、位置度 ⌖、圆跳动 ↗、全跳动 ⌰
    ///   · 端面/方向/轮廓类不计
    ///   · 父级涵盖子级 → 子级不叠加（圆柱度⊃圆度/直线度、全跳动⊃圆跳动/圆柱度/同轴度/圆度、
    ///     圆跳动⊃圆度、位置度⊃同轴度/对称度、平面度⊃直线度）
    ///   · 尺寸带 Ⓔ → 形状类不叠加
    ///   · 同一项目多值 → 取严（小值）
    /// </summary>
    public static class GtolRules
    {
        static readonly System.Collections.Generic.Dictionary<string, string> RADIAL =
            new System.Collections.Generic.Dictionary<string, string>
        {
            {"—","形状：影响径向"}, {"○","形状：影响径向"}, {"⌭","形状：影响径向"},
            {"◎","位置：影响径向"}, {"⌖","位置：影响径向"},
            {"↗","位置：影响径向"}, {"⌰","位置：影响径向"},
        };
        static readonly string[] SHAPE = new string[] { "—", "○", "⌭" };
        static readonly System.Collections.Generic.Dictionary<string, string[]> CONTAINS =
            new System.Collections.Generic.Dictionary<string, string[]>
        {
            {"⌭", new string[] {"○","—"}},
            {"▱", new string[] {"—"}},
            {"⌰", new string[] {"↗","⌭","◎","○"}},
            {"↗", new string[] {"○"}},
            {"⌖", new string[] {"◎","⌯"}},
        };
        static readonly System.Collections.Generic.Dictionary<string, string> PARENT_NAME =
            new System.Collections.Generic.Dictionary<string, string>
        {
            {"⌭","圆柱度"}, {"▱","平面度"}, {"⌰","全跳动"}, {"↗","圆跳动"}, {"⌖","位置度"},
        };

        /// <summary>重算：原地更新每条的 Include/Reason，返回 Σt(mm)</summary>
        public static double Recompute(System.Collections.Generic.List<QcGtolLine> lines, bool isEnv)
        {
            if (lines == null) return 0;
            var scopes = new System.Collections.Generic.Dictionary<QcGtolLine, string>();
            var grouped = new System.Collections.Generic.Dictionary<string, System.Collections.Generic.List<QcGtolLine>>();
            for (int i = 0; i < lines.Count; i++)
            {
                QcGtolLine g = lines[i];
                if (g == null) continue;
                string scope = !string.IsNullOrEmpty(g.GroupId) ? g.GroupId
                    : (!string.IsNullOrEmpty(g.GtolId) ? g.GtolId
                    : (!string.IsNullOrEmpty(g.RawId) ? g.RawId : "ITEM-" + i.ToString()));
                scopes[g] = scope;
                if (!grouped.ContainsKey(scope)) grouped[scope] = new System.Collections.Generic.List<QcGtolLine>();
                grouped[scope].Add(g);
            }
            // 复合框下层无独立符号格：仅当同组恰好一个符号且位于第一层时继承。
            // 不写回 Sym，避免下次把复合框误判成“同符号组合框”。
            var effectiveSymbols = new System.Collections.Generic.Dictionary<QcGtolLine, string>();
            foreach (var bucket in grouped.Values)
            {
                int symbolCount = 0; string inherited = ""; bool firstHasSymbol = false;
                foreach (QcGtolLine g in bucket)
                {
                    if (g == null || string.IsNullOrEmpty(g.Sym)) continue;
                    symbolCount++;
                    if (g.FrameOrder == 1) { inherited = g.Sym; firstHasSymbol = true; }
                }
                foreach (QcGtolLine g in bucket)
                    if (g != null) effectiveSymbols[g] = (!string.IsNullOrEmpty(g.Sym) ? g.Sym :
                        (symbolCount == 1 && firstHasSymbol && g.FrameOrder > 1 ? inherited : ""));
            }
            foreach (QcGtolLine g in lines)
            {
                if (g == null) continue;
                string reason;
                string effective = effectiveSymbols.ContainsKey(g) ? effectiveSymbols[g] : (g.Sym ?? "");
                g.Include = RADIAL.TryGetValue(effective, out reason);
                g.Reason = g.Include ? reason : "端面/方向/轮廓类：不参与径向计算";
            }
            // Ⓔ：形状类不叠加
            if (isEnv)
                foreach (QcGtolLine g in lines)
                    if (g != null && g.Include && System.Array.IndexOf(SHAPE,
                        effectiveSymbols.ContainsKey(g) ? effectiveSymbols[g] : g.Sym) >= 0)
                    { g.Include = false; g.Reason = "尺寸带 Ⓔ：形状误差已被尺寸带约束，不叠加"; }

            // 涵盖关系
            foreach (QcGtolLine g in lines)
            {
                if (g == null) continue;
                if (!g.Include) continue;
                foreach (var kv in CONTAINS)
                {
                    string gEffective = effectiveSymbols.ContainsKey(g) ? effectiveSymbols[g] : (g.Sym ?? "");
                    if (System.Array.IndexOf(kv.Value, gEffective) < 0) continue;
                    foreach (QcGtolLine p in lines)
                    {
                        string pEffective = p != null && effectiveSymbols.ContainsKey(p) ? effectiveSymbols[p] : (p != null ? p.Sym : "");
                        if (p == null || p == g || !p.Include || pEffective != kv.Key) continue;
                        if (scopes[p] != scopes[g]) continue;
                        if (!string.IsNullOrEmpty(p.Dat1) && !string.IsNullOrEmpty(g.Dat1)
                            && p.Dat1 != g.Dat1) continue;
                        if (!string.IsNullOrEmpty(p.Dat2) && !string.IsNullOrEmpty(g.Dat2)
                            && p.Dat2 != g.Dat2) continue;
                        if (!string.IsNullOrEmpty(p.Dat3) && !string.IsNullOrEmpty(g.Dat3)
                            && p.Dat3 != g.Dat3) continue;
                        g.Include = false;
                        string pn;
                        g.Reason = "被 " + kv.Key + "（" + (PARENT_NAME.TryGetValue(kv.Key, out pn) ? pn : "父级")
                                  + "）涵盖，不叠加";
                        break;
                    }
                    if (!g.Include) break;
                }
            }

            // 同一项目取严
            var seen = new System.Collections.Generic.Dictionary<string, QcGtolLine>();
            foreach (QcGtolLine g in lines)
            {
                if (g == null) continue;
                if (!g.Include || g.ValueMm <= 0) continue;
                string key = scopes[g] + "|" + (effectiveSymbols.ContainsKey(g) ? effectiveSymbols[g] : (g.Sym ?? "")) + "|" +
                             (g.Dat1 ?? "") + "|" + (g.Dat2 ?? "") + "|" + (g.Dat3 ?? "");
                QcGtolLine keep;
                if (!seen.TryGetValue(key, out keep)) { seen[key] = g; continue; }
                if (g.ValueMm < keep.ValueMm)
                {
                    keep.Include = false;
                    keep.Reason = "同一项目取严：被更小值 " + Mm(g.ValueMm) + " 取代";
                    g.Reason = "同一项目取严（保留较小值）";
                    seen[key] = g;
                }
                else
                {
                    g.Include = false;
                    g.Reason = "同一项目取严：未超过已取严值 " + Mm(keep.ValueMm);
                }
            }

            double sigma = 0;
            foreach (QcGtolLine g in lines)
                if (g != null && g.Include && g.ValueMm > 0) sigma += g.ValueMm;
            return System.Math.Round(sigma, 4);
        }

        static string Mm(double v) { return v.ToString("0.###", System.Globalization.CultureInfo.InvariantCulture); }
    }

}
