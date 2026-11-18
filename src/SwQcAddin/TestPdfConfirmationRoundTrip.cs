using System;
using System.Collections.Generic;
using System.IO;

namespace SwQc
{
    internal static class TestPdfConfirmationRoundTrip
    {
        private static int Main()
        {
            string dir = Path.Combine(Path.GetTempPath(), "SwQcAddin", "pdf_audit",
                "confirmation-roundtrip-" + Guid.NewGuid().ToString("N"));
            Directory.CreateDirectory(dir);
            try
            {
                var row = new PdfConfirmRow {
                    row_id = "CONF-DIM-1", object_type = "dimension", entity_id = "DIM-1",
                    page = 1, display = "10±0.1", target_feature = "总长", confirmed = false
                };
                var draft = new PdfConfirmationDraft {
                    source = new PdfAuditSource { sha256 = "TEST-SHA" },
                    rows = new List<PdfConfirmRow> { row }
                };
                bool rejected = false;
                try { PdfAuditRunner.SaveConfirmed(draft, new List<PdfConfirmRow> { row }, dir); }
                catch (InvalidDataException) { rejected = true; }
                if (!rejected) throw new Exception("未确认行没有被阻断。");

                row.confirmed = true;
                string path = PdfAuditRunner.SaveConfirmed(draft, new List<PdfConfirmRow> { row }, dir);
                if (!File.Exists(path)) throw new Exception("确认文件未写入。");
                string json = File.ReadAllText(path);
                if (json.IndexOf("TEST-SHA", StringComparison.Ordinal) < 0 ||
                    json.IndexOf("10±0.1", StringComparison.Ordinal) < 0)
                    throw new Exception("保存后回读内容不完整。");
                row.display = "10±0.05";
                path = PdfAuditRunner.SaveConfirmed(draft, new List<PdfConfirmRow> { row }, dir);
                json = File.ReadAllText(path);
                if (json.IndexOf("10±0.05", StringComparison.Ordinal) < 0)
                    throw new Exception("原子替换后的确认内容不正确。");
                var manual = new PdfConfirmRow {
                    row_id = "CONF-MANUAL-DIM-2", object_type = "dimension", entity_id = "MANUAL-DIM-2",
                    page = 1, display = "12±0.1", target_feature = "总宽", confirmed = true,
                    manual_added = true, status = "人工新增并确认"
                };
                row.excluded = true;
                row.status = "人工排除";
                path = PdfAuditRunner.SaveConfirmed(draft, new List<PdfConfirmRow> { row, manual }, dir);
                json = File.ReadAllText(path);
                if (json.IndexOf("MANUAL-DIM-2", StringComparison.Ordinal) < 0 ||
                    json.IndexOf("\"excluded\":true", StringComparison.Ordinal) < 0)
                    throw new Exception("人工新增或人工排除没有保存。");
                Console.WriteLine("PASS C# confirmation save-readback-failclosed");
                return 0;
            }
            finally
            {
                try { Directory.Delete(dir, true); } catch { }
            }
        }
    }
}
