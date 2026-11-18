using System;
using System.IO;
namespace SwQc {
    static class TestNative {
        static int Main(string[] a) {
            string outDir = a.Length > 1 ? a[1] : Path.Combine(Path.GetTempPath(), "SwQcAddin", "out");
            var rep = QcRunner.RunNative(a[0], outDir);
            if (rep.RawError != null && rep.RawError.Length > 0) {
                Console.WriteLine("失败: " + rep.RawError);
                return 1;
            }
            Console.WriteLine("OK 文件=" + rep.FileName + " 严重=" + rep.Critical + " 一般=" + rep.Major + " 轻微=" + rep.Minor + " 耗时=" + rep.ElapsedSec.ToString("0.0") + "s");
            if (rep.Items.Count > 0) Console.WriteLine("首条: [" + rep.Items[0].SeverityText + "] " + rep.Items[0].RuleId + " " + rep.Items[0].Title);
            return 0;
        }
    }
}