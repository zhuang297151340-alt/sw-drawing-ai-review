using System;
using System.Collections.Generic;
using System.Drawing;
using System.Globalization;
using System.Windows.Forms;

namespace SwQc
{
    /// <summary>
    /// 公差计算面板（2026-10-24 按用户实测反馈修正）。
    ///
    /// 交互：
    ///   · 尺寸行：「偏差上」「偏差下」两个**可编辑框**（自动读入图纸公差，可手动改）；
    ///             点 **「计算」**：轴算最大作用边界 MMS+有效Σt；孔算最小作用边界 MMS−有效Σt。
    ///   · 配合行：「配合尺寸」「偏差上」「偏差下」手动填；点 **「生成」** →
    ///             输出「配合类别 + 最大间隙 + 最小间隙」。
    ///             尺寸行选「轴」→ 配合件默认是**孔**；选「孔」→ 配合件是**轴**。
    ///             判定：最小间隙 ≥ 0 → 间隙配合；最大间隙 ≤ 0 → 过盈；其余过渡。
    /// </summary>
    public class CalcPanel : UserControl
    {
        static readonly Font F = new Font("微软雅黑", 8.5f);
        static readonly Font FB = new Font("微软雅黑", 8.5f, FontStyle.Bold);
        static readonly Font Fs = new Font("微软雅黑", 7.8f);
        static readonly Color Ink = Color.FromArgb(55, 65, 81);
        static readonly Color Muted = Color.FromArgb(102, 112, 133);
        static readonly Color Brand = Color.FromArgb(11, 74, 138);

        private readonly Panel _body;
        private readonly Label _head;
        private readonly Label _empty;

        // 列 x（尺寸行与配合行共用，保证上下对齐）
        const int X_Mark = 2;        // 轴/孔 单选 or 「配合（孔）」
        const int X_No = 74;         // 尺寸N / 配合尺寸框
        const int X_Size = 124;      // φ35
        const int X_UpLbl = 168;     // 「偏差上」
        const int X_UpBox = 212;     // 偏差上 输入框
        const int X_LowLbl = 276;    // 「偏差下」
        const int X_LowBox = 320;    // 偏差下 输入框
        const int X_Glab = 386;      // 「几何公差」
        const int X_Gtol = 446;      // 框格
        const int X_Btn = 668;       // 计算 / 生成
        const int X_Res = 726;       // 结果
        const int BOWW = 60;         // 偏差框宽
        // 2026-10-25：结果改为整行显示（原来挤在右侧被截断），组高 54 → 94
        const int GroupH = 56;       // 每组高（结果回到按钮后面，不再占整行）
        const int ResY1 = 50;        // 尺寸行结果
        const int ResY2 = 70;        // 配合行结果

        public CalcPanel()
        {
            BackColor = Color.White;

            _head = new Label();
            _head.Dock = DockStyle.Top;
            _head.Height = 34;
            _head.BackColor = Color.FromArgb(232, 241, 249);
            _head.Font = Fs;
            _head.ForeColor = Ink;
            _head.Padding = new Padding(6, 3, 6, 0);
            Controls.Add(_head);

            _body = new Panel();
            _body.Dock = DockStyle.Fill;
            _body.AutoScroll = true;
            _body.BackColor = Color.White;
            Controls.Add(_body);
            _body.BringToFront();

            _empty = new Label();
            _empty.Text = "在「尺寸公差」列表中勾选要计算的尺寸（左侧方框），这里会生成对应计算。";
            _empty.Font = F;
            _empty.ForeColor = Muted;
            _empty.AutoSize = true;
            _empty.Location = new Point(10, 10);
            _body.Controls.Add(_empty);

            UpdateHeader(0);
        }

        void UpdateHeader(int n)
        {
            _head.Text = "公差计算　" + (n > 0 ? "已选 " + n + " 个" : "") + "\r\n" +
                "尺寸行点「生成」→ 轴给最大作用边界、孔给最小作用边界" +
                "（复合公差先按涵盖/取严筛选有效Σt；轴=MMS+Σt，孔=MMS−Σt）；" +
                "配合行点「生成」→ 配合种类 + 最大/最小间隙（过盈时给过盈量）";
        }

        /// <summary>
        /// 自检/测试用：程序化点击第 idx 组的「生成」，返回结果区文字。
        /// 用来验证修正符号（Ⓔ/Ⓜ）是否真的参与计算。
        /// </summary>
        /// <summary>
        /// 自检/测试用：程序化点击第 idx 组的「生成」（尺寸行那个），返回结果区文字。
        /// </summary>
        public string ClickGenerate(int idx)
        {
            var groups = new List<Control>();
            foreach (Control c in _body.Controls)
                if (c is Panel && c.Controls.Count > 5) groups.Add(c);
            if (idx < 0 || idx >= groups.Count) return "(无分组)";
            Control g = groups[idx];

            var btns = new List<Button>();
            foreach (Control c in g.Controls)
            {
                var b = c as Button;
                if (b != null && b.Text == "生成") btns.Add(b);
            }
            if (btns.Count == 0) return "(无生成按钮)";
            // 尺寸行的按钮在上排（Top 更小）
            btns.Sort(delegate(Button a, Button b) { return a.Top.CompareTo(b.Top); });
            btns[0].PerformClick();

            // 收集结果标签（X 靠右那两块），按 Top 排序，取尺寸行那块
            var labs = new List<Label>();
            foreach (Control c in g.Controls)
            {
                var l = c as Label;
                if (l == null) continue;
                if (Convert.ToString(l.Tag) == "res") labs.Add(l);      // 新布局：按 Tag
                else if (l.Left >= X_Res - 2 && labs.Count == 0) labs.Add(l);   // 兼容旧布局
            }
            labs.Sort(delegate(Label a, Label b) { return a.Top.CompareTo(b.Top); });
            if (labs.Count == 0) return "(无结果标签)";
            return "[尺寸行] " + labs[0].Text + "  |  [配合行] "
                 + (labs.Count > 1 ? labs[1].Text : "");
        }

        /// <summary>自检：聚焦第 idx 组里第 k 个输入框，返回是否真的拿到焦点</summary>
        public bool FocusBox(int idx, int k)
        {
            var groups = new List<Control>();
            foreach (Control c in _body.Controls)
                if (c is Panel && c.Controls.Count > 5) groups.Add(c);
            if (idx < 0 || idx >= groups.Count) return false;
            var boxes = new List<TextBox>();
            foreach (Control c in groups[idx].Controls)
            {
                var tb = c as TextBox;
                if (tb != null) boxes.Add(tb);
            }
            if (boxes.Count == 0) return false;
            var target = boxes[Math.Max(0, Math.Min(k, boxes.Count - 1))];
            target.Focus();
            Application.DoEvents();
            return target.Focused;
        }

        public void SetRows(List<QcDimRow> picked)
        {
            _body.SuspendLayout();
            _body.Controls.Clear();

            if (picked == null || picked.Count == 0)
            {
                _body.Controls.Add(_empty);
                UpdateHeader(0);
                _body.ResumeLayout();
                return;
            }

            int y = 2;
            int no = 1;
            foreach (QcDimRow r in picked)
            {
                Panel g = BuildGroup(r, no++, y);
                _body.Controls.Add(g);
                y += g.Height + 4;   // 组高动态（多条几何公差时更高）
            }
            UpdateHeader(picked.Count);
            _body.ResumeLayout();
        }

        Panel BuildGroup(QcDimRow row, int no, int y)
        {
            var g = new Panel();
            g.Location = new Point(0, y);
            // 2026-10-25：多条几何公差时，多留一行显示清单
            int _nGtol = (row.Gtols == null) ? 0 : row.Gtols.Count;
            int _extra = (_nGtol > 1) ? (_nGtol - 1) * 24 : 0;   // 每多一条框格 +24
            g.Size = new Size(1000, GroupH + _extra);
            g.BackColor = Color.White;

            // =============== 尺寸行 ===============
            var rbShaft = new RadioButton();
            rbShaft.Text = "轴";
            rbShaft.Font = F;
            rbShaft.AutoSize = true;
            rbShaft.Checked = true;
            rbShaft.Location = new Point(X_Mark, 2);
            g.Controls.Add(rbShaft);

            var rbHole = new RadioButton();
            rbHole.Text = "孔";
            rbHole.Font = F;
            rbHole.AutoSize = true;
            rbHole.Location = new Point(X_Mark + 34, 2);
            g.Controls.Add(rbHole);

            AddLabel(g, "尺寸" + no, X_No, 2, 46, FB, Brand);
            AddLabel(g, row.Content, X_Size, 2, 46, FB, Ink);

            AddLabel(g, "偏差上", X_UpLbl, 2, 44, Fs, Muted);
            TextBox tbUp = MakeInput(g, X_UpBox, 1, BOWW, row.HasTol ? N(row.TolUp) : "");
            AddLabel(g, "偏差下", X_LowLbl, 2, 44, Fs, Muted);
            TextBox tbLow = MakeInput(g, X_LowBox, 1, BOWW, row.HasTol ? N(row.TolLow) : "");

            AddLabel(g, "几何公差", X_Glab, 2, 60, Fs, Muted);
            // 2026-10-25 用户要求：分段式方框，几条显示几个；右侧"计入"标签不与「生成」按钮重叠。
            // 用紧凑分段框（160×22）替代原来的 GtolFrame（214 宽会顶到按钮）。
            int _nG = (row.Gtols == null) ? 0 : row.Gtols.Count;
            if (_nG == 0)
            {
                // 没有关联几何公差 → 留一个空的分段框（可手填）
                g.Controls.Add(MakeGtolChip("", "", "", "", "", "", X_Gtol, 2));
            }
            else
            {
                for (int gi = 0; gi < _nG; gi++)
                {
                    QcGtolLine gl = row.Gtols[gi];
                    string mod = string.IsNullOrEmpty(gl.Mod)
                        ? (gi == 0 ? (row.Modifier ?? "") : "")
                        : gl.Mod;
                    g.Controls.Add(MakeGtolChip(gl.Sym, gl.Val, mod, gl.Dat1, gl.Dat2, gl.Dat3,
                                                X_Gtol, 2 + gi * 24));

                    string tag;
                    if (gl.Include) tag = "计入";
                    else if ((gl.Reason ?? "").Contains("涵盖")) tag = "被涵盖";
                    else if ((gl.Reason ?? "").Contains("取严")) tag = "取严";
                    else if ((gl.Reason ?? "").Contains("Ⓔ")) tag = "Ⓔ不叠加";
                    else tag = "不计";
                    var tg = new Label();
                    tg.AutoSize = false;
                    tg.Font = Fs;
                    tg.Text = tag;
                    tg.TextAlign = ContentAlignment.MiddleLeft;
                    tg.Location = new Point(638, 2 + gi * 24 + 3);
                    tg.Size = new Size(44, 16);
                    tg.ForeColor = gl.Include ? Color.FromArgb(22, 122, 60) : Color.FromArgb(150, 120, 60);
                    g.Controls.Add(tg);
                }
            }

            var btnCalc = new Button();
            btnCalc.Text = "生成";
            btnCalc.Font = F;
            btnCalc.Size = new Size(52, 21);
            btnCalc.Location = new Point(X_Btn, 1);
            btnCalc.FlatStyle = FlatStyle.System;
            g.Controls.Add(btnCalc);

            // 2026-10-25 用户要求：结果显示在「生成」按钮后面（只显示数值，不显示计算过程）
            var eff = AddLabel(g, "", X_Btn + 58, 2, 268, F, Muted);
            eff.Tag = "res";

            // =============== 配合行（列与尺寸行对齐）==============
            var lbFit = AddLabel(g, "配合（孔）", X_Mark, 28 + _extra, 72, F, Muted);
            TextBox tbFit = MakeInput(g, X_No, 27 + _extra, 44, "");

            AddLabel(g, "偏差上", X_UpLbl + 4, 28 + _extra, 44, Fs, Muted);
            TextBox tbMUp = MakeInput(g, X_UpBox, 27 + _extra, BOWW, "");
            AddLabel(g, "偏差下", X_LowLbl + 4, 28 + _extra, 44, Fs, Muted);
            TextBox tbMLow = MakeInput(g, X_LowBox, 27 + _extra, BOWW, "");

            AddLabel(g, "几何公差", X_Glab, 28 + _extra, 60, Fs, Muted);
            var frame2 = new GtolFrame();
            frame2.Location = new Point(X_Gtol, 26 + _extra);
            g.Controls.Add(frame2);

            var btnGen = new Button();
            btnGen.Text = "生成";
            btnGen.Font = F;
            btnGen.Size = new Size(52, 21);
            btnGen.Location = new Point(X_Btn, 27 + _extra);
            btnGen.FlatStyle = FlatStyle.System;
            g.Controls.Add(btnGen);

            var res = AddLabel(g, "填完点「生成」", X_Btn + 58, 27 + _extra, 268, F, Muted);
            res.Tag = "res";


            // 选轴 → 配合件是孔；选孔 → 配合件是轴
            rbShaft.CheckedChanged += delegate
            {
                if (rbShaft.Checked) lbFit.Text = "配合（孔）";
            };
            rbHole.CheckedChanged += delegate
            {
                if (rbHole.Checked) lbFit.Text = "配合（轴）";
            };

            // ---- 分隔线 ----
            var line = new Panel();
            line.BackColor = Color.FromArgb(218, 223, 230);
            line.Location = new Point(0, GroupH + _extra - 2);
            line.Size = new Size(1000, 1);
            g.Controls.Add(line);

            // ---- 尺寸行「计算」：一律算 MMVS ----
            btnCalc.Click += delegate
            {
                double up, low;
                if (!TryNum(tbUp, out up) || !TryNum(tbLow, out low))
                {
                    eff.Text = "偏差填完整再生成";
                    eff.ForeColor = Muted;
                    return;
                }
                // 用户 2026-10-24：上偏差必须是最大值、下偏差是最小值；填反了要更正
                bool _swapped;
                FixToleranceOrder(ref up, ref low, out _swapped);
                if (_swapped)
                {
                    tbUp.Text = N(up).TrimStart('+');
                    tbLow.Text = N(low).TrimStart('+');
                }
                bool shaft = rbShaft.Checked;
                row.SigmaT = GtolRules.Recompute(row.Gtols, row.Modifier == "Ⓔ");
                string mod = row.Modifier;      // Ⓔ / Ⓜ / Ⓛ 等尺寸修正符号
                if (string.IsNullOrEmpty(mod) && row.Gtols != null)
                    foreach (QcGtolLine gl in row.Gtols)
                        if (gl != null && gl.Include && (gl.Mod == "Ⓜ" || gl.Mod == "Ⓛ"))
                        { mod = gl.Mod; break; }
                double t = (mod == "Ⓔ") ? 0.0 : row.SigmaT;
                string txt2;
                double val = ActionBoundary(row.Nominal, up, low, shaft, t, mod,
                                            PrincipleDefault, out txt2);
                // 2026-10-25 用户要求：只显示数值结果（含"独立原则/装配预估"字样），
                // 不再显示 Σt / 轴 / 孔 之类的计算过程。
                string _num = N(val);
                string resultName = shaft ? "最大作用边界 " : "最小作用边界 ";
                eff.Text = resultName + _num
                    + (string.IsNullOrEmpty(mod) ? "（复合公差有效加严值已计入）" : "")
                    + (_swapped ? "　⚠ 上下偏差填反，已更正" : "");
                eff.ForeColor = _swapped
                    ? Color.FromArgb(181, 71, 8)
                    : ((mod == "Ⓜ" || mod == "Ⓛ" || mod == "Ⓔ")
                        ? Color.FromArgb(6, 118, 71)
                        : Color.FromArgb(181, 71, 8));
                eff.Font = FB;
            };

            // ---- 配合行「生成」----
            btnGen.Click += delegate
            {
                double fitSize, fUp, fLow, up2, low2;
                if (!TryNum(tbFit, out fitSize) || !TryNum(tbMUp, out fUp) || !TryNum(tbMLow, out fLow))
                {
                    res.Text = "配合尺寸/偏差填完整";
                    res.ForeColor = Muted;
                    return;
                }
                if (!TryNum(tbUp, out up2) || !TryNum(tbLow, out low2))
                {
                    res.Text = "尺寸的偏差要先填好";
                    res.ForeColor = Muted;
                    return;
                }
                // 配合行两组偏差也要保证「上 = 最大、下 = 最小」
                bool swA, swB;
                FixToleranceOrder(ref up2, ref low2, out swA);
                FixToleranceOrder(ref fUp, ref fLow, out swB);
                double rowHi = row.Nominal + Math.Max(up2, low2);
                double rowLo = row.Nominal + Math.Min(up2, low2);
                double fHi = fitSize + Math.Max(fUp, fLow);
                double fLo = fitSize + Math.Min(fUp, fLow);

                double holeHi, holeLo, shaftHi, shaftLo;
                if (rbShaft.Checked)
                {
                    shaftHi = rowHi; shaftLo = rowLo; holeHi = fHi; holeLo = fLo;
                }
                else
                {
                    holeHi = rowHi; holeLo = rowLo; shaftHi = fHi; shaftLo = fLo;
                }
                double maxGap, minGap;
                string kind = FitKind(holeHi, holeLo, shaftHi, shaftLo, out maxGap, out minGap);
                Color col = kind == "间隙配合" ? Color.FromArgb(6, 118, 71)
                          : (kind == "过盈配合" ? Color.FromArgb(192, 16, 72)
                                                : Color.FromArgb(181, 71, 8));

                res.Text = FitText(kind, maxGap, minGap);
                res.ForeColor = col;
                res.Font = FB;
            };

            return g;
        }


        /// <summary>
        /// 紧凑几何公差分段框：符号|值|修正|基准1|基准2|基准3，总宽 188、高 22。
        /// 原 GtolFrame 宽 214 会顶到「生成」按钮，导致"计入"标签重叠。
        /// </summary>
        static Panel MakeGtolChip(string sym, string val, string mod,
                                  string dat1, string dat2, string dat3, int x, int y)
        {
            var p = new Panel();
            p.BorderStyle = BorderStyle.FixedSingle;
            p.BackColor = Color.White;
            p.Size = new Size(188, 22);
            p.Location = new Point(x, y);

            int[] ws = new int[] { 22, 48, 22, 32, 32, 32 };
            string[] texts = new string[] { sym, val, mod, dat1, dat2, dat3 };
            int cx = 0;
            for (int k = 0; k < ws.Length; k++)
            {
                var lb = new Label();
                lb.AutoSize = false;
                lb.TextAlign = ContentAlignment.MiddleCenter;
                lb.Font = new Font("Segoe UI Symbol", 8.5f);
                lb.Text = texts[k] ?? "";
                lb.Location = new Point(cx + 1, 2);
                lb.Size = new Size(ws[k] - 2, 16);
                p.Controls.Add(lb);
                cx += ws[k];
                if (k < ws.Length - 1)
                {
                    var lineSeg = new Panel();
                    lineSeg.BackColor = Color.FromArgb(160, 170, 185);
                    lineSeg.Location = new Point(cx, 1);
                    lineSeg.Size = new Size(1, 19);
                    p.Controls.Add(lineSeg);
                }
            }
            return p;
        }

        static Label AddLabel(Control p, string text, int x, int y, int w, Font f, Color c)
        {
            var l = new Label();
            l.Text = text;
            l.Font = f;
            l.ForeColor = c;
            l.AutoSize = false;
            l.Location = new Point(x, y);
            l.Size = new Size(w, 18);
            l.TextAlign = ContentAlignment.MiddleLeft;
            p.Controls.Add(l);
            return l;
        }

        static readonly Color BoxIdle = Color.FromArgb(255, 252, 240);
        static readonly Color BoxActive = Color.FromArgb(255, 255, 214);

        static TextBox MakeInput(Control p, int x, int y, int w, string init)
        {
            var tb = new TextBox();
            tb.Font = F;
            tb.Size = new Size(w, 19);
            tb.Location = new Point(x, y);
            tb.Text = init ?? "";
            tb.BackColor = BoxIdle;
            // 2026-10-24：用户反馈「点进去看不到光标」——
            // ① 聚焦高亮：有没有焦点一眼可见（不依赖系统光标）
            // ② 强制聚焦 + 光标放末尾
            // ③ 关掉输入法：只输数字，避免 IME 抢焦点/盖住光标
            tb.ImeMode = ImeMode.Off;
            tb.Cursor = Cursors.IBeam;
            tb.TabStop = true;
            tb.Enter += delegate { tb.BackColor = BoxActive; };
            tb.Leave += delegate { tb.BackColor = BoxIdle; };
            tb.Click += delegate
            {
                tb.Focus();
                tb.SelectionStart = tb.Text.Length;
            };
            p.Controls.Add(tb);
            tb.BringToFront();
            return tb;
        }

        static bool TryNum(TextBox tb, out double v)
        {
            v = 0;
            string t = Normalize(tb.Text);
            if (t.Length == 0) return false;
            return double.TryParse(t, NumberStyles.Float, CultureInfo.InvariantCulture, out v);
        }

        // ================================================================
        // 计算核心（抽成静态函数，便于 DemoShot --selftest 断言）
        // ================================================================

        // ================================================================
        // 公差原则（用户 2026-10-24 要求可配置）
        //   GB/T 1800.1、ISO 默认 **独立原则**；ASME Y14.5 默认 **包容原则**
        //   独立原则：尺寸与几何互不约束 → 无实效边界，只有 MMS（最大实体尺寸）
        //   包容要求 Ⓔ：边界就是 MMS（尺寸与几何互相补偿，不能超出 MMS）
        //   最大实体要求 Ⓜ：MMVS = MMC + t（轴）/ MMC − t（孔），有补偿
        // ================================================================
        public static string PrincipleDefault = "独立原则";

        /// <summary>
        /// 校验上下偏差顺序（用户 2026-10-24）：
        /// 上偏差 = 最大值、下偏差 = 最小值。填反了自动交换，并告知调用方。
        /// </summary>
        public static void FixToleranceOrder(ref double up, ref double low, out bool swapped)
        {
            swapped = false;
            if (up < low)
            {
                double t = up; up = low; low = t;
                swapped = true;
            }
        }

        // ================================================================
        // 作用边界（用户 2026-10-24 定稿口径）
        //   轴 → 最大作用边界：Ⓔ=MMS；其余=MMS+有效Σt
        //   孔 → 最小作用边界：Ⓔ=MMS；其余=MMS−有效Σt
        // MMS：轴取上极限（最大径），孔取下极限（最小径）。
        // 返回数值，out 参数给界面显示文字（带公式与原则说明，避免误解）。
        // ================================================================
        public static double ActionBoundary(double nominal, double up, double low,
                                            bool shaft, double t, string modifier,
                                            string principle, out string text)
        {
            double hi = nominal + Math.Max(up, low);
            double lo = nominal + Math.Min(up, low);
            double mms = shaft ? hi : lo;          // 轴取上极限、孔取下极限
            string mod = modifier ?? "";
            string name = shaft ? "最大作用边界" : "最小作用边界";

            if (mod == "Ⓔ")
            {
                // 包容原则：作用边界就是最大实体边界（MMB = MMS），不叠加几何公差
                text = name + " = " + N(mms) + "（Ⓔ包容原则：MMB = MMS）";
                return mms;
            }
            if (mod == "Ⓜ" || mod == "Ⓛ")
            {
                double v = shaft ? mms + t : mms - t;
                text = name + " = " + N(v) + "（Ⓜ最大实体：MMS " + N(mms)
                     + (shaft ? " + " : " − ") + N(Math.Abs(t)).TrimStart('+') + "）";
                return v;
            }
            // 本工具的装配加严口径：复合公差先按涵盖/取严得到有效 t。
            double estimate = shaft ? mms + t : mms - t;
            text = name + " = " + N(estimate) + "（复合公差有效加严值已计入）";
            return estimate;
        }

        /// <summary>
        /// 配合结果文案（用户 2026-10-24）：
        /// 过盈时**不写负间隙**，而是把「过盈量」用正数表达，工程师一眼就懂。
        ///   间隙配合 → 最大间隙 +0.034　最小间隙 0
        ///   过渡配合 → 最大间隙 +0.006　最大过盈 +0.014
        ///   过盈配合 → 最大过盈 +0.028　最小过盈 +0.008
        /// </summary>
        public static string FitText(string kind, double maxGap, double minGap)
        {
            if (kind == "过盈配合")
            {
                // 过盈量 = −间隙：最大过盈对应最小间隙，最小过盈对应最大间隙
                return kind + "　最大过盈 " + N(-minGap) + "　最小过盈 " + N(-maxGap);
            }
            if (kind == "过渡配合")
            {
                return kind + "　最大间隙 " + N(maxGap) + "　最大过盈 " + N(-minGap);
            }
            return kind + "　最大间隙 " + N(maxGap) + "　最小间隙 " + N(minGap);
        }

        /// <summary>
        /// 按「修正符号 + 默认公差原则」算边界尺寸。
        /// tag 返回给人看的名称（MMVS / MMS），便于界面自解释。
        /// </summary>
        public static double MmvsByPrinciple(double nominal, double up, double low,
                                            bool shaft, double t, string modifier,
                                            string principle, out string tag)
        {
            double hi = nominal + Math.Max(up, low);
            double lo = nominal + Math.Min(up, low);
            double mms = shaft ? hi : lo;      // 最大实体尺寸：轴取上极限、孔取下极限
            string mod = modifier ?? "";

            if (mod == "Ⓜ" || mod == "Ⓛ")
            {
                // 有实效边界：轴 + t / 孔 − t
                tag = "MMVS";
                return shaft ? mms + t : mms - t;
            }
            if (mod == "Ⓔ")
            {
                // 包容要求：边界就是最大实体尺寸
                tag = "MMS（Ⓔ包容要求）";
                return mms;
            }
            // 无修正符号：按默认原则；独立原则/包容原则都没有实效补偿边界
            tag = "MMS（" + (principle ?? "独立原则") + "）";
            return mms;
        }

        /// <summary>
        /// MMVS 最大实体实效尺寸。
        /// 轴：上极限 + 几何公差；孔：下极限 − 几何公差。
        /// </summary>
        public static double Mmvs(double nominal, double tolUp, double tolLow, bool shaft, double t)
        {
            double hi = nominal + Math.Max(tolUp, tolLow);
            double lo = nominal + Math.Min(tolUp, tolLow);
            return shaft ? hi + t : lo - t;
        }

        /// <summary>
        /// 配合判定：最小间隙 ≥ 0 → 间隙配合；最大间隙 ≤ 0 → 过盈配合；其余过渡。
        /// 返回类别，并通过 out 给出最大/最小间隙。
        /// </summary>
        public static string FitKind(double holeHi, double holeLo,
                                    double shaftHi, double shaftLo,
                                    out double maxGap, out double minGap)
        {
            maxGap = holeHi - shaftLo;
            minGap = holeLo - shaftHi;
            if (minGap >= -1e-9) return "间隙配合";
            if (maxGap <= 1e-9) return "过盈配合";
            return "过渡配合";
        }

        /// <summary>归一化：全角→半角、丢弃直径符号等非数字字符</summary>
        public static string Normalize(string raw)
        {
            if (string.IsNullOrEmpty(raw)) return "";
            var sb = new System.Text.StringBuilder();
            foreach (char ch in raw)
            {
                if (ch >= '0' && ch <= '9') { sb.Append(ch); continue; }
                if (ch == '.' || ch == '-') { sb.Append(ch); continue; }
                if (ch >= '０' && ch <= '９') { sb.Append((char)('0' + (ch - '０'))); continue; }
                if (ch == '．' || ch == '。') { sb.Append('.'); continue; }
                if (ch == '－') { sb.Append('-'); continue; }
            }
            return sb.ToString().Trim();
        }

        public static string TolText(QcDimRow r)
        {
            if (!r.HasTol) return "未注";
            if (Math.Abs(r.TolUp + r.TolLow) < 1e-9)
                return "±" + N(Math.Abs(r.TolUp)).TrimStart('+');
            return "上 " + N(r.TolUp) + " 下 " + N(r.TolLow);
        }

        /// <summary>数字显示：最多 3 位小数、去尾 0、带符号</summary>
        public static string N(double v)
        {
            if (Math.Abs(v) < 0.0000005) return "0";
            string s = v.ToString("+0.000;-0.000;0", CultureInfo.InvariantCulture);
            if (!s.Contains(".")) return s;
            s = s.TrimEnd('0');
            if (s.EndsWith(".")) s = s.TrimEnd('.');
            return s;
        }
    }
}
