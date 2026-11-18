using System;
using System.Drawing;
using System.Windows.Forms;

namespace SwQc
{
    public static class TestPdfAuditSettingsUi
    {
        [STAThread]
        public static int Main(string[] args)
        {
            string output = args.Length > 0 ? args[0] : "pdf_audit_settings_ui_test.png";
            Application.EnableVisualStyles();
            using (var form = new PdfAuditSettingsForm())
            {
                form.Show();
                Application.DoEvents();
                using (var bmp = new Bitmap(form.Width, form.Height))
                {
                    form.DrawToBitmap(bmp, new Rectangle(0, 0, bmp.Width, bmp.Height));
                    bmp.Save(output);
                }
                form.Close();
            }
            return 0;
        }
    }
}
