using System;
using System.Collections.Generic;
using System.Drawing;
using System.Windows.Forms;

namespace SwQc
{
    /// <summary>开发期人工确认窗口截图测试，不参与插件 DLL 编译。</summary>
    public static class TestPdfConfirmUi
    {
        [STAThread]
        public static int Main(string[] args)
        {
            string output = args.Length > 0 ? args[0] : "pdf_confirm_ui_test.png";
            var draft = new PdfConfirmationDraft();
            draft.source = new PdfAuditSource { file_name = "测试图纸.pdf", sha256 = "TEST" };
            draft.dimension_choices.Add(new PdfDimensionChoice { entity_id = "DIM-P001-001", label = "DIM-P001-001  ⌀5 +0.04/+0.02" });
            draft.rows.Add(new PdfConfirmRow { row_id = "C1", object_type = "dimension", entity_id = "DIM-P001-001",
                page = 1, confidence = 0.97, status = "系统判定", kind = "diameter", display = "⌀5 +0.04/+0.02",
                nominal = "5", upper = "+0.04", lower = "+0.02", target_feature = "孔", crop_id = "P001-T11" });
            draft.rows.Add(new PdfConfirmRow { row_id = "C2", object_type = "dimension", entity_id = "DIM-P001-002",
                page = 1, confidence = 0.62, status = "需确认", kind = "linear", display = "12",
                nominal = "12", target_feature = "宽度", crop_id = "P001-T12" });
            draft.rows.Add(new PdfConfirmRow { row_id = "C3", object_type = "gtol", entity_id = "GTOL-P001-001",
                page = 1, confidence = 0.88, status = "系统判定", group_id = "GROUP-1",
                frame_lines_text = "位置度 | ⌀0.05 | M | A", target_type = "dimension",
                target_entity_id = "DIM-P001-001", target_feature = "孔轴线", crop_id = "P001-T11" });
            draft.rows.Add(new PdfConfirmRow { row_id = "C4", object_type = "datum", entity_id = "DATUM-P001-001",
                page = 1, confidence = 0.70, status = "需确认", letter = "A", target_feature = "底面",
                target_entity_id = "DIM-P001-002", crop_id = "P001-T21" });

            Application.EnableVisualStyles();
            using (var form = new PdfAuditConfirmForm(draft, ""))
            {
                form.Show(); Application.DoEvents();
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
