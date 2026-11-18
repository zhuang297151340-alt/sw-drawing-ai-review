using System;
using System.Drawing;
using System.Windows.Forms;

namespace SwQc
{
    /// <summary>开发期 PDF 独立窗口截图测试，不参与插件 DLL 编译。</summary>
    public static class TestPdfAuditUi
    {
        [STAThread]
        public static int Main(string[] args)
        {
            string output = args.Length > 0 ? args[0] : "pdf_audit_ui_test.png";
            string evidence = args.Length > 1 ? args[1] : "";
            Application.EnableVisualStyles();
            var report = new PdfAuditReport
            {
                status = "incomplete",
                summary = "已完成整图审核；发现1项一般风险，另有1项需要人工确认。",
                counts = new PdfAuditCounts { major = 1, manual_review = 1 },
                source = new PdfAuditSource { file_name = "测试工程图.pdf", page_count = 1 },
                coverage = new PdfAuditCoverage { total_pages = 1, extracted_pages = 1, complete = false }
            };
            report.issues.Add(new PdfAuditIssue
            {
                issue_id = "ISSUE-001", severity = "major", category = "几何公差",
                reviewed_object = "位置度复合公差框", drawing_evidence = "位置度 ⌀0.05(M) | A",
                audit_result = "公差框引用基准A，但未识别到清晰的基准A定义。",
                risk = "检验基准体系不完整，可能导致制造与测量口径不一致。",
                basis = "图面事实 + 几何公差可检验性原则",
                suggestion = "补充基准A符号并明确指向基准要素。",
                verification = "确认基准符号、指向要素和公差框引用一致。", confidence = 0.93,
                location = new PdfAuditLocation { page = 1, crop_id = "P001-T11" }, evidence_image = evidence
            });
            report.manual_review.Add(new PdfAuditIssue
            {
                issue_id = "ISSUE-002", severity = "manual_review", category = "尺寸",
                reviewed_object = "孔尺寸", drawing_evidence = "图像局部清晰度不足",
                audit_result = "无法确定数值是3.5还是3.6。", risk = "关键尺寸可能被误读。",
                basis = "视觉识别冲突", suggestion = "人工核对原始PDF或提高导出质量。",
                verification = "放大原PDF确认小数位。", confidence = 0.51,
                location = new PdfAuditLocation { page = 1, crop_id = "P001-T12" }, evidence_image = evidence
            });
            using (var form = new PdfAuditForm("测试工程图.pdf"))
            {
                form.Show();
                form.SetReport(report);
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
