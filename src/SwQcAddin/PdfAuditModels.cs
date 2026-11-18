using System;
using System.Collections.Generic;

namespace SwQc
{
    /// <summary>PDF 视觉整图审核的独立报告模型；不得写入 QcDocument/三表。</summary>
    public class PdfAuditLocation
    {
        public int page { get; set; }
        public string view { get; set; }
        public string crop_id { get; set; }
        public List<double> bbox { get; set; }
    }

    public class PdfAuditIssue
    {
        public string issue_id { get; set; }
        public string severity { get; set; }
        public string category { get; set; }
        public string reviewed_object { get; set; }
        public PdfAuditLocation location { get; set; }
        public string drawing_evidence { get; set; }
        public string audit_result { get; set; }
        public string risk { get; set; }
        public string basis { get; set; }
        public string suggestion { get; set; }
        public string verification { get; set; }
        public double confidence { get; set; }
        public List<string> entity_ids { get; set; }
        public string evidence_image { get; set; }

        public string SeverityText
        {
            get
            {
                if (severity == "critical") return "严重";
                if (severity == "major") return "一般";
                if (severity == "minor") return "轻微";
                if (severity == "manual_review") return "待复核";
                return "—";
            }
        }
    }

    public class PdfAuditCoverage
    {
        public int total_pages { get; set; }
        public int extracted_pages { get; set; }
        public List<int> reviewed_pages { get; set; }
        public int total_entities { get; set; }
        public int reviewed_entities { get; set; }
        public List<string> missing_entity_ids { get; set; }
        public bool complete { get; set; }
    }

    public class PdfAuditSource
    {
        public string file_name { get; set; }
        public string sha256 { get; set; }
        public int page_count { get; set; }
        public long file_size { get; set; }
        public bool has_text_layer { get; set; }
    }

    public class PdfAuditCounts
    {
        public int critical { get; set; }
        public int major { get; set; }
        public int minor { get; set; }
        public int manual_review { get; set; }
    }

    public class PdfAuditPerformance
    {
        public double render_sec { get; set; }
        public double model_elapsed_sec { get; set; }
        public int model_calls { get; set; }
        public int http_attempts { get; set; }
    }

    public class PdfAuditReport
    {
        public string engine_version { get; set; }
        public string status { get; set; }
        public PdfAuditSource source { get; set; }
        public string provider { get; set; }
        public string summary { get; set; }
        public PdfAuditCoverage coverage { get; set; }
        public PdfAuditCounts counts { get; set; }
        public PdfAuditPerformance performance { get; set; }
        public List<PdfAuditIssue> issues { get; set; }
        public List<PdfAuditIssue> manual_review { get; set; }
        public List<string> warnings { get; set; }
        public List<string> passed_categories { get; set; }
        public double elapsed_sec { get; set; }
        public string error { get; set; }
        public string confirmation_path { get; set; }
        public bool human_confirmed { get; set; }
        public string ReportPath { get; set; }
        public string JobDirectory { get; set; }

        public PdfAuditReport()
        {
            source = new PdfAuditSource();
            coverage = new PdfAuditCoverage();
            counts = new PdfAuditCounts();
            issues = new List<PdfAuditIssue>();
            manual_review = new List<PdfAuditIssue>();
            warnings = new List<string>();
            passed_categories = new List<string>();
        }

        public List<PdfAuditIssue> AllIssues()
        {
            var all = new List<PdfAuditIssue>();
            if (issues != null) all.AddRange(issues);
            if (manual_review != null) all.AddRange(manual_review);
            return all;
        }
    }

    public class PdfDimensionChoice
    {
        public string entity_id { get; set; }
        public string label { get; set; }
    }

    /// <summary>PDF 自动识别后、专业审核前供人工确认的一行。</summary>
    public class PdfConfirmRow
    {
        public string row_id { get; set; }
        public string object_type { get; set; } // dimension / gtol / datum
        public string entity_id { get; set; }
        public int page { get; set; }
        public double confidence { get; set; }
        public string status { get; set; }
        public string kind { get; set; }
        public string display { get; set; }
        public string nominal { get; set; }
        public string upper { get; set; }
        public string lower { get; set; }
        public string fit { get; set; }
        public string quantity { get; set; }
        public string group_id { get; set; }
        public string frame_lines_text { get; set; }
        public string raw_text { get; set; }
        public string target_type { get; set; }
        public string target_entity_id { get; set; }
        public string target_feature { get; set; }
        public string letter { get; set; }
        public string crop_id { get; set; }
        public bool confirmed { get; set; }
        /// <summary>system=AI 高置信度自动接受；human=用户已确认或人工处理。</summary>
        public string confirmation_source { get; set; }
        /// <summary>人工补录的漏识别对象；允许在原识别集合之外新增。</summary>
        public bool manual_added { get; set; }
        /// <summary>人工判定为误识别。保留原行作为审计痕迹，但不送入专业审核。</summary>
        public bool excluded { get; set; }
    }

    public class PdfConfirmationDraft
    {
        public string schema_version { get; set; }
        public PdfAuditSource source { get; set; }
        public List<PdfConfirmRow> rows { get; set; }
        public List<PdfDimensionChoice> dimension_choices { get; set; }

        public PdfConfirmationDraft()
        {
            source = new PdfAuditSource();
            rows = new List<PdfConfirmRow>();
            dimension_choices = new List<PdfDimensionChoice>();
        }
    }

    public class PdfConfirmedPayload
    {
        public string schema_version { get; set; }
        public string source_sha256 { get; set; }
        public string confirmed_at { get; set; }
        public List<PdfConfirmRow> rows { get; set; }
    }

    public class PdfExportResult
    {
        public bool Success;
        public string PdfPath = "";
        public int ExpectedPages;
        public string Error = "";
        public string Warning = "";
    }
}

