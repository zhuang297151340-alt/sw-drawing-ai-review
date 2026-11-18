using System;
using System.Collections.Generic;
using System.Drawing;
using System.IO;
using System.Text;
using System.Windows.Forms;

namespace SwQc
{
    /// <summary>PDF视觉通道专用模型设置，不读写原生通道 ai_*。</summary>
    public class PdfAuditSettingsForm : Form
    {
        private readonly ComboBox _provider;
        private readonly TextBox _base;
        private readonly TextBox _model;
        private readonly TextBox _key;
        private readonly TextBox _proxy;
        private readonly Button _test;
        private readonly Label _testStatus;

        public PdfAuditSettingsForm()
        {
            Text = "PDF视觉模型设置";
            StartPosition = FormStartPosition.CenterParent;
            FormBorderStyle = FormBorderStyle.FixedDialog;
            MaximizeBox = false; MinimizeBox = false;
            Width = 620; Height = 455;
            Font = new Font("Microsoft YaHei UI", 9F);
            BackColor = Color.White;

            var title = new Label { Left = 28, Top = 22, Width = 530, Height = 30,
                Font = new Font("Microsoft YaHei UI", 14F, FontStyle.Bold),
                ForeColor = Color.FromArgb(31, 81, 125), Text = "PDF视觉整图审核 · 专用模型" };
            var note = new Label { Left = 30, Top = 60, Width = 540, Height = 42,
                ForeColor = Color.FromArgb(80, 85, 92),
                Text = "该配置只供PDF通道使用，不会修改现有通用审核的AI设置。\r\n必须选择支持图片输入的模型；DeepSeek纯文本模型不能使用。" };
            Controls.Add(title); Controls.Add(note);

            _provider = new ComboBox { Left = 150, Top = 118, Width = 380, DropDownStyle = ComboBoxStyle.DropDownList };
            _provider.Items.AddRange(new object[] { "qwen", "kimi", "openai", "custom" });
            _base = new TextBox { Left = 150, Top = 158, Width = 380 };
            _model = new TextBox { Left = 150, Top = 198, Width = 380 };
            _key = new TextBox { Left = 150, Top = 238, Width = 380, UseSystemPasswordChar = true };
            _proxy = new TextBox { Left = 150, Top = 278, Width = 380 };
            AddLabel("提供商", 118); AddLabel("API 地址", 158); AddLabel("视觉模型", 198);
            AddLabel("API Key", 238); AddLabel("代理（可空）", 278);
            Controls.Add(_provider); Controls.Add(_base); Controls.Add(_model); Controls.Add(_key); Controls.Add(_proxy);

            _provider.SelectedIndexChanged += delegate { ApplyDefaults(false); };
            _testStatus = new Label { Left = 30, Top = 318, Width = 500, Height = 34,
                ForeColor = Color.FromArgb(80, 85, 92), Text = "测试会发送一张小图片，验证模型是否真正支持视觉输入。" };
            _test = new Button { Text = "测试连接", Left = 230, Top = 360, Width = 105, Height = 34 };
            var save = new Button { Text = "保存", Left = 350, Top = 360, Width = 90, Height = 34,
                BackColor = Color.FromArgb(31, 81, 125), ForeColor = Color.White, FlatStyle = FlatStyle.Flat };
            var cancel = new Button { Text = "取消", Left = 450, Top = 360, Width = 80, Height = 34,
                DialogResult = DialogResult.Cancel };
            _test.Click += delegate { TestConnection(); };
            save.Click += delegate { SaveAndClose(); };
            Controls.Add(_testStatus); Controls.Add(_test); Controls.Add(save); Controls.Add(cancel);
            AcceptButton = save; CancelButton = cancel;
            LoadValues();
        }

        private void AddLabel(string text, int top)
        {
            Controls.Add(new Label { Left = 30, Top = top + 3, Width = 110, Text = text });
        }

        private string IniPath()
        {
            return Path.Combine(Path.GetDirectoryName(typeof(PdfAuditSettingsForm).Assembly.Location), "qc_addin_config.ini");
        }

        private Dictionary<string, string> ReadValues()
        {
            var d = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
            string path = IniPath();
            if (!File.Exists(path)) return d;
            foreach (string raw in File.ReadAllLines(path, Encoding.UTF8))
            {
                string line = raw.Trim();
                if (line.Length == 0 || line.StartsWith("#") || line.StartsWith(";")) continue;
                int eq = line.IndexOf('=');
                if (eq > 0) d[line.Substring(0, eq).Trim()] = line.Substring(eq + 1).Trim();
            }
            return d;
        }

        private void LoadValues()
        {
            var d = ReadValues();
            string value;
            string provider = d.TryGetValue("pdf_ai_provider", out value) ? value : "qwen";
            int index = _provider.Items.IndexOf(provider.ToLower());
            _provider.SelectedIndex = index >= 0 ? index : 3;
            if (d.TryGetValue("pdf_ai_base", out value)) _base.Text = value;
            if (d.TryGetValue("pdf_ai_model", out value)) _model.Text = value;
            if (d.TryGetValue("pdf_ai_key", out value)) _key.Text = value;
            if (d.TryGetValue("pdf_ai_proxy", out value)) _proxy.Text = value;
            ApplyDefaults(true);
        }

        private void ApplyDefaults(bool onlyIfEmpty)
        {
            string p = Convert.ToString(_provider.SelectedItem) ?? "qwen";
            string b = "", m = "";
            if (p == "qwen") { b = "https://dashscope.aliyuncs.com/compatible-mode/v1"; m = "qwen3-vl-plus"; }
            else if (p == "kimi") { b = "https://api.moonshot.cn/v1"; m = "kimi-k3.1-pro"; }
            else if (p == "openai") { b = "https://api.openai.com/v1"; m = "gpt-4.1"; }
            if (!onlyIfEmpty || string.IsNullOrWhiteSpace(_base.Text)) _base.Text = b;
            if (!onlyIfEmpty || string.IsNullOrWhiteSpace(_model.Text)) _model.Text = m;
        }

        private void SaveAndClose()
        {
            string p = Convert.ToString(_provider.SelectedItem) ?? "";
            if (!ValidateFields(p))
            {
                return;
            }
            QcRunner.SaveSetting("pdf_ai_provider", p);
            QcRunner.SaveSetting("pdf_ai_base", _base.Text.Trim());
            QcRunner.SaveSetting("pdf_ai_model", _model.Text.Trim());
            QcRunner.SaveSetting("pdf_ai_key", _key.Text.Trim());
            QcRunner.SaveSetting("pdf_ai_proxy", _proxy.Text.Trim());
            DialogResult = DialogResult.OK;
            Close();
        }

        private bool ValidateFields(string provider)
        {
            if (!string.IsNullOrWhiteSpace(provider) && !string.IsNullOrWhiteSpace(_base.Text) &&
                !string.IsNullOrWhiteSpace(_model.Text) && !string.IsNullOrWhiteSpace(_key.Text))
                return true;
            MessageBox.Show("请完整填写提供商、API地址、视觉模型和API Key。", "PDF视觉模型设置",
                MessageBoxButtons.OK, MessageBoxIcon.Warning);
            return false;
        }

        private void TestConnection()
        {
            string provider = Convert.ToString(_provider.SelectedItem) ?? "";
            if (!ValidateFields(provider)) return;
            string baseUrl = _base.Text.Trim();
            string model = _model.Text.Trim();
            string key = _key.Text.Trim();
            string proxy = _proxy.Text.Trim();
            _test.Enabled = false;
            _testStatus.ForeColor = Color.FromArgb(31, 81, 125);
            _testStatus.Text = "正在连接模型并验证图片输入，请稍候…";
            System.Threading.ThreadPool.QueueUserWorkItem(delegate
            {
                string message;
                bool ok = PdfAuditRunner.TestConnection(provider, baseUrl, model, key, proxy, out message);
                try
                {
                    if (IsDisposed) return;
                    BeginInvoke((MethodInvoker)delegate
                    {
                        if (IsDisposed) return;
                        _test.Enabled = true;
                        _testStatus.ForeColor = ok ? Color.FromArgb(30, 125, 70) : Color.FromArgb(185, 50, 40);
                        _testStatus.Text = message;
                        MessageBox.Show(this, message, ok ? "连接成功" : "连接失败",
                            MessageBoxButtons.OK, ok ? MessageBoxIcon.Information : MessageBoxIcon.Error);
                    });
                }
                catch { }
            });
        }
    }
}
