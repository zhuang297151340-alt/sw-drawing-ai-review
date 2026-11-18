using System;
using System.IO;

namespace SwQc
{
    /// <summary>开发期端到端自测工具，不参与插件 DLL 编译。</summary>
    public static class TestPdfAuditRunner
    {
        public static int Main(string[] args)
        {
            if (args.Length < 2)
            {
                Console.WriteLine("用法：TestPdfAuditRunner.exe <PDF> <输出目录>");
                return 2;
            }
            var report = PdfAuditRunner.Run(args[0], args[1], 0,
                delegate(string text) { Console.WriteLine("PROGRESS " + text); },
                delegate { return false; });
            Console.WriteLine("STATUS=" + (report.status ?? ""));
            Console.WriteLine("PAGES=" + (report.source == null ? 0 : report.source.page_count));
            Console.WriteLine("ERROR=" + (report.error ?? ""));
            Console.WriteLine("REPORT=" + (report.ReportPath ?? ""));
            // 当前开发机尚未配置视觉模型时，明确失败仍表示 C#→Python→JSON 链路成功。
            return File.Exists(report.ReportPath ?? "") ? 0 : 1;
        }
    }
}
