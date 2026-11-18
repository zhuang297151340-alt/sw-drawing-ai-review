using System;
using System.IO;

namespace SwQc
{
    /// <summary>开发自检：直接调 Python 引擎，验证插件里的调用链（不依赖 SW）。</summary>
    static class TestRunner
    {
        static int Main(string[] args)
        {
            if (args.Length < 1)
            {
                Console.WriteLine("用法: TestRunner.exe <图纸.dxf> [输出目录]");
                return 2;
            }
            string outDir = args.Length > 1 ? args[1] : Path.Combine(Path.GetTempPath(), "SwQcTest");
            var rep = QcRunner.Run(args[0], outDir);
            if (!string.IsNullOrEmpty(rep.RawError))
            {
                Console.WriteLine("失败: " + rep.RawError);
                return 1;
            }
            Console.WriteLine("OK  文件=" + rep.FileName + "  预设=" + rep.Preset);
            Console.WriteLine("严重=" + rep.Critical + " 一般=" + rep.Major +
                              " 轻微=" + rep.Minor + " 合计=" + rep.Items.Count +
                              " 耗时=" + rep.ElapsedSec.ToString("0.0") + "s");
            Console.WriteLine("预览=" + (rep.PreviewPath.Length > 0 ? "有" : "无") +
                              "  报告=" + rep.JsonPath);
            if (rep.Items.Count > 0)
            {
                QcItem it = rep.Items[0];
                Console.WriteLine("首条: [" + it.SeverityText + "] " + it.RuleId + " " + it.Title);
            }
            return 0;
        }
    }
}
