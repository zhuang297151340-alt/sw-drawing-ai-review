using System;
using System.Collections.Generic;
using System.Drawing;
using System.IO;
using System.Text;
using System.Windows.Forms;

namespace SwQc
{
    /// <summary>
    /// 通用 AI 语义审核接口设置。
    ///
    /// 支持 DeepSeek / ChatGPT / OpenAI 兼容 / 通义 / 智谱 / Moonshot 以及自定义 base。
    /// 保存写入 qc_addin_config.ini 的 ai_provider / ai_key / ai_base / ai_model。
    /// </summary>
    public class QcAiSettingsForm : Form
    {
        private static readonly Color Brand = Color.FromArgb(31, 78, 121);
        private static readonly Color Ink = Color.FromArgb(55, 65, 81);
        private static readonly Color GrayText = Color.FromArgb(107, 122, 141);
        private static readonly Font F = new Font("微软雅黑", 9f);
        private static readonly Font FB = new Font("微软雅黑", 9f, FontStyle.Bold);

        private ComboBox _provider;
        // 2026-10-25：按厂商缓存 Key，切换厂商时不丢失
        private readonly Dictionary<string, string> _keys =
            new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
        private string _lastProv = "";
        private bool _loading = false;
        private TextBox _base, _key, _proxy;
        private ComboBox _model;
        private Label _hint;
        private readonly bool _embedded;

        // 各厂商常用模型（可手输，最终以服务商控制台/接口为准）
        private static readonly Dictionary<string, string[]> ProviderModels =
            new Dictionary<string, string[]>
        {
            { "deepseek（深度求索）", new[] { "deepseek-chat", "deepseek-reasoner" } },
            { "chatgpt / openai", new[] { "gpt-4o-mini", "gpt-4o", "gpt-4.1-mini", "gpt-4.1", "o4-mini", "o3" } },
            { "qwen（通义千问）", new[] { "qwen-plus", "qwen-turbo", "qwen-max" } },
            { "zhipu（智谱 GLM）", new[] { "glm-4-plus", "glm-4-air", "glm-4-flash" } },
            { "moonshot（月之暗面）", new[] { "moonshot-v1-8k", "moonshot-v1-32k", "moonshot-v1-128k" } },
            { "自定义（OpenAI 兼容）", new[] { "" } },
        };

        // 各厂商默认（base, model）
        private static readonly Dictionary<string, string[]> Defaults = new Dictionary<string, string[]>
        {
            { "deepseek（深度求索）", new[] { "https://api.deepseek.com/v1", "deepseek-chat" } },
            { "chatgpt / openai", new[] { "https://api.openai.com/v1", "gpt-4o-mini" } },
            { "qwen（通义千问）", new[] { "https://dashscope.aliyuncs.com/compatible-mode/v1", "qwen-plus" } },
            { "zhipu（智谱 GLM）", new[] { "https://open.bigmodel.cn/api/paas/v4", "glm-4-plus" } },
            { "moonshot（月之暗面）", new[] { "https://api.moonshot.cn/v1", "moonshot-v1-8k" } },
            { "自定义（OpenAI 兼容）", new[] { "", "" } },
        };

        public QcAiSettingsForm() : this(false)
        {
        }

        /// <param name="embedded">true 时作为审图主窗口页签内容，不显示独立窗口按钮。</param>
        public QcAiSettingsForm(bool embedded)
        {
            _embedded = embedded;
            BuildUi();
            LoadCurrent();
        }

        void BuildUi()
        {
            Text = _embedded ? "通用审核通道 · AI接口设置" : "AI 接口设置";
            StartPosition = FormStartPosition.CenterScreen;
            ClientSize = new Size(_embedded ? 900 : 560, 390);
            FormBorderStyle = _embedded ? FormBorderStyle.None : FormBorderStyle.FixedDialog;
            MaximizeBox = false;
            MinimizeBox = false;
            TopMost = !_embedded;
            ShowInTaskbar = !_embedded;
            AutoScroll = true;
            Font = F;
            BackColor = Color.White;

            var head = new Panel();
            head.Dock = DockStyle.Top;
            head.Height = 56;
            head.BackColor = Brand;
            Controls.Add(head);
            var title = new Label();
            title.Text = _embedded ? "通用审核通道 · AI接口设置" : "AI 接口设置";
            title.Font = new Font("微软雅黑", 13f, FontStyle.Bold);
            title.ForeColor = Color.White;
            title.AutoSize = true;
            title.Location = new Point(16, 8);
            head.Controls.Add(title);
            var sub = new Label();
            sub.Text = "仅用于通用 AI 语义审核：发送技术要求 + 标题栏 + 尺寸/公差列表，不读取 PDF 或图纸图像";
            sub.Font = new Font("微软雅黑", 8f);
            sub.ForeColor = Color.FromArgb(214, 228, 240);
            sub.AutoSize = true;
            sub.Location = new Point(18, 34);
            head.Controls.Add(sub);

            int y = 72;
            AddLabel("接口类型", 18, y);
            _provider = new ComboBox();
            _provider.DropDownStyle = ComboBoxStyle.DropDownList;
            _provider.Location = new Point(120, y - 3);
            _provider.Width = _embedded ? 520 : 300;
            foreach (string k in Defaults.Keys) _provider.Items.Add(k);
            _provider.SelectedIndex = 0;
            _provider.SelectedIndexChanged += delegate
            {
                string prov = Convert.ToString(_provider.SelectedItem);
                if (_loading) { ApplyDefault(); _lastProv = prov; return; }
                // 先把旧厂商的 Key 存回它自己的槽位，再载入新厂商的 Key
                if (!string.IsNullOrEmpty(_lastProv) && _lastProv != prov)
                    _keys[SlotOf(_lastProv)] = _key == null ? "" : _key.Text.Trim();
                _lastProv = prov;
                string saved;
                if (_key != null)
                    _key.Text = _keys.TryGetValue(SlotOf(prov), out saved) ? (saved ?? "") : "";
                ApplyDefault();
            };
            Controls.Add(_provider);

            y += 42;
            AddLabel("API 地址", 18, y);
            _base = new TextBox();
            NiceInput(_base);
            _base.Location = new Point(120, y - 3);
            _base.Width = _embedded ? 520 : 300;
            Controls.Add(_base);

            y += 42;
            AddLabel("API Key", 18, y);
            _key = new TextBox();
            NiceInput(_key);
            _key.Location = new Point(120, y - 3);
            _key.Width = _embedded ? 520 : 300;
            _key.UseSystemPasswordChar = true;
            Controls.Add(_key);
            var eye = new Button();
            eye.Text = "显示";
            eye.Font = F;
            eye.Size = new Size(56, 24);
            eye.Location = new Point(_embedded ? 648 : 428, y - 2);
            eye.FlatStyle = FlatStyle.System;
            eye.Click += delegate { _key.UseSystemPasswordChar = !_key.UseSystemPasswordChar; };
            Controls.Add(eye);

            y += 42;
            AddLabel("模型名称", 18, y);
            _model = new ComboBox();
            _model.DropDownStyle = ComboBoxStyle.DropDown;   // 可下拉也可手输
            _model.Location = new Point(120, y - 3);
            _model.Width = _embedded ? 520 : 300;
            Controls.Add(_model);

            var btnModels = new Button();
            btnModels.Text = "拉取模型";
            btnModels.Font = F;
            btnModels.Size = new Size(72, 24);
            btnModels.Location = new Point(_embedded ? 648 : 428, y - 2);
            btnModels.FlatStyle = FlatStyle.System;
            btnModels.Click += delegate
            {
                try
                {
                    QcRunner.SaveConfig(BuildConfig());
                    string err;
                    System.Collections.Generic.List<string> models = QcRunner.ListModels(out err);
                    if (models == null || models.Count == 0)
                    {
                        MessageBox.Show(err, "拉取模型", MessageBoxButtons.OK, MessageBoxIcon.Warning);
                        return;
                    }
                    string cur = _model.Text;
                    _model.Items.Clear();
                    foreach (string m in models) _model.Items.Add(m);
                    _model.Text = cur;
                    MessageBox.Show("该 Key 可用模型 " + models.Count + " 个，已填入下拉：\n" +
                                    string.Join(", ", models.ToArray()),
                        "拉取模型", MessageBoxButtons.OK, MessageBoxIcon.Information);
                }
                catch (Exception ex)
                {
                    MessageBox.Show("拉取失败：" + ex.Message, "拉取模型");
                }
            };
            Controls.Add(btnModels);

            y += 42;
            AddLabel("代理(可选)", 18, y);
            _proxy = new TextBox();
            NiceInput(_proxy);
            _proxy.Location = new Point(120, y - 3);
            _proxy.Width = _embedded ? 520 : 300;
            Controls.Add(_proxy);

            var tip = new Label();
            tip.Text = "例：DeepSeek 填 Key 后点保存即可；模型留空会用该厂商默认。\n" +
                "★ 用 ChatGPT/OpenAI 必须填「代理」：国内直连 api.openai.com 会超时" +
                "（实测 DNS 被污染、443 不通），代理填 http://127.0.0.1:7890 这类本机端口。\n" +
                       "Key 只保存在本机 qc_addin_config.ini 里，不会上传到别处。";
            tip.Font = new Font("微软雅黑", 8f);
            tip.ForeColor = GrayText;
            tip.AutoSize = false;
            tip.Size = new Size(_embedded ? 850 : 540, 62);
            tip.Location = new Point(18, y + 36);
            Controls.Add(tip);

            var btnTest = new Button();
            btnTest.Text = "测试连接";
            btnTest.Font = F;
            btnTest.Size = new Size(104, 28);
            btnTest.Location = new Point(18, 372);
            btnTest.FlatStyle = FlatStyle.System;
            btnTest.Click += delegate
            {
                btnTest.Enabled = false;
                try
                {
                    QcRunner.SaveConfig(BuildConfig());
                    string r = QcRunner.TestAi();
                    MessageBox.Show(r, "AI 测试", MessageBoxButtons.OK,
                        r.StartsWith("AI_TEST_OK") ? MessageBoxIcon.Information : MessageBoxIcon.Warning);
                }
                finally { btnTest.Enabled = true; }
            };
            Controls.Add(btnTest);
            ClientSize = new Size(ClientSize.Width, ClientSize.Height + 40);
            btnTest.Location = new Point(18, ClientSize.Height - 58);
            btnTest.Anchor = AnchorStyles.Left | AnchorStyles.Bottom;

            _hint = new Label();
            _hint.Font = F;
            _hint.ForeColor = GrayText;
            _hint.AutoSize = false;
            _hint.Size = new Size(520, 22);
            _hint.Location = new Point(18, y + 82);
            Controls.Add(_hint);

            var save = new Button();
            save.Text = "保存";
            save.Font = FB;
            save.Size = new Size(104, 32);
            save.Location = new Point(ClientSize.Width - (_embedded ? 122 : 232), ClientSize.Height - 46);
            save.Anchor = AnchorStyles.Right | AnchorStyles.Bottom;
            save.BackColor = Brand;
            save.ForeColor = Color.White;
            save.FlatStyle = FlatStyle.Flat;
            save.FlatAppearance.BorderSize = 0;
            save.Click += delegate { SaveConfig(); };
            Controls.Add(save);

            if (!_embedded)
            {
                var cancel = new Button();
                cancel.Text = "取消";
                cancel.Font = F;
                cancel.Size = new Size(104, 32);
                cancel.Location = new Point(ClientSize.Width - 118, ClientSize.Height - 46);
                cancel.Anchor = AnchorStyles.Right | AnchorStyles.Bottom;
                cancel.FlatStyle = FlatStyle.System;
                cancel.Click += delegate { Close(); };
                Controls.Add(cancel);
            }
        }

        void AddLabel(string text, int x, int y)
        {
            var l = new Label();
            l.Text = text;
            l.Font = F;
            l.ForeColor = Ink;
            l.AutoSize = true;
            l.Location = new Point(x, y);
            Controls.Add(l);
        }

        void ApplyDefault()
        {
            string prov = Convert.ToString(_provider.SelectedItem);
            string[] d;
            if (Defaults.TryGetValue(prov, out d))
            {
                if (!string.IsNullOrEmpty(d[0])) _base.Text = d[0];
            }
            // 该厂商的常用模型填进下拉（不强制覆盖用户已填的）
            _model.Items.Clear();
            string[] list;
            if (ProviderModels.TryGetValue(prov, out list))
            {
                foreach (string m in list) if (!string.IsNullOrEmpty(m)) _model.Items.Add(m);
                if (string.IsNullOrEmpty(_model.Text) && list.Length > 0) _model.Text = list[0];
            }
        }

        static string IniPath()
        {
            string dir = Path.GetDirectoryName(typeof(QcAiSettingsForm).Assembly.Location);
            return Path.Combine(dir, "qc_addin_config.ini");
        }

        void LoadCurrent()
        {
            _loading = true;
            var cfg = QcRunner.LoadConfig();
            // 2026-10-25：载入各厂商已保存的 Key
            if (cfg.AiKeys != null)
                foreach (KeyValuePair<string, string> kv in cfg.AiKeys)
                    if (!string.IsNullOrEmpty(kv.Value)) _keys[kv.Key] = kv.Value;
            string prov = "deepseek（深度求索）";
            if (!string.IsNullOrEmpty(cfg.AiProvider))
                foreach (string k in Defaults.Keys)
                    if (k.StartsWith(cfg.AiProvider)) { prov = k; break; }
            _provider.SelectedItem = prov;
            // 配置为空时保留该厂商默认值（之前会被清空，看起来像坏了）
            if (!string.IsNullOrEmpty(cfg.AiBase)) _base.Text = cfg.AiBase;
            if (!string.IsNullOrEmpty(cfg.AiModel)) _model.Text = cfg.AiModel;
            _proxy.Text = cfg.AiProxy ?? "";
            _key.Text = cfg.AiKey ?? "";
            _hint.Text = string.IsNullOrEmpty(cfg.AiKey)
                ? "当前状态：未配置（AI 检查不会执行）"
                : "当前状态：已配置（保存后生效）";
            // 当前厂商的 Key 记入缓存，供切换时回填
            string curSlot = SlotOf(Convert.ToString(_provider.SelectedItem));
            if (!string.IsNullOrEmpty(cfg.AiKey)) _keys[curSlot] = cfg.AiKey;
            _lastProv = Convert.ToString(_provider.SelectedItem);
            _loading = false;
        }

        void SaveConfig()
        {
            var cfg = BuildConfig();
            try
            {
                QcRunner.SaveConfig(cfg);
                MessageBox.Show("已保存。\n下一次点「检查」时会使用该 AI 接口。",
                    "AI 接口设置", MessageBoxButtons.OK, MessageBoxIcon.Information);
                _hint.Text = "当前状态：已配置（保存后生效）";
                if (!_embedded) Close();
            }
            catch (Exception ex)
            {
                MessageBox.Show("保存失败：" + ex.Message, "AI 接口设置",
                    MessageBoxButtons.OK, MessageBoxIcon.Error);
            }
        }

        QcRunner.Config BuildConfig()
        {
            var cfg = QcRunner.LoadConfig();
            cfg.AiProvider = ProviderKey(Convert.ToString(_provider.SelectedItem));
            cfg.AiBase = _base.Text.Trim();
            cfg.AiKey = _key.Text.Trim();
            // 2026-10-25：当前厂商的 Key 写回槽位，一起持久化
            string _slot = SlotOf(Convert.ToString(_provider.SelectedItem));
            if (!string.IsNullOrEmpty(cfg.AiKey)) _keys[_slot] = cfg.AiKey;
            cfg.AiKeys = new Dictionary<string, string>(_keys, StringComparer.OrdinalIgnoreCase);
            cfg.AiModel = _model.Text.Trim();
            cfg.AiProxy = _proxy == null ? "" : _proxy.Text.Trim();
            return cfg;
        }

        /// <summary>厂商显示名 → Key 槽位名（持久化为 ai_key_&lt;槽位&gt;）</summary>
        static string SlotOf(string display)
        {
            string p = ProviderKey(display);
            return string.IsNullOrEmpty(p) ? "custom" : p;
        }

        static string ProviderKey(string display)
        {
            if (string.IsNullOrEmpty(display)) return "";
            if (display.StartsWith("deepseek")) return "deepseek";
            if (display.StartsWith("chatgpt")) return "chatgpt";
            if (display.StartsWith("qwen")) return "qwen";
            if (display.StartsWith("zhipu")) return "zhipu";
            if (display.StartsWith("moonshot")) return "moonshot";
            return "";
        }

        /// <summary>
        /// 输入框统一体验（2026-10-24）：聚焦背景高亮 + 关输入法 + I 形光标 + 点击强制聚焦。
        /// 用户反馈过「点进框里看不到光标」，高亮是最稳的可见反馈（不依赖系统光标绘制）。
        /// </summary>
        static void NiceInput(TextBox tb)
        {
            if (tb == null) return;
            var idle = Color.FromArgb(255, 252, 240);
            var active = Color.FromArgb(255, 255, 214);
            tb.BackColor = idle;
            tb.ImeMode = ImeMode.Off;
            tb.Cursor = Cursors.IBeam;
            tb.TabStop = true;
            tb.Enter += delegate { tb.BackColor = active; };
            tb.Leave += delegate { tb.BackColor = idle; };
            tb.Click += delegate
            {
                tb.Focus();
                tb.SelectionStart = tb.Text.Length;
            };
        }
    }
}
