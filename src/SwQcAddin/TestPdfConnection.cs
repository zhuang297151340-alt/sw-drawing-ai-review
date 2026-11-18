using System;
using System.Collections.Generic;
using System.IO;
using System.Text;

namespace SwQc
{
    public static class TestPdfConnection
    {
        public static int Main()
        {
            string ini = Path.Combine(Environment.GetFolderPath(
                Environment.SpecialFolder.LocalApplicationData), "SwQcAddin", "qc_addin_config.ini");
            var values = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
            foreach (string raw in File.ReadAllLines(ini, Encoding.UTF8))
            {
                string line = raw.Trim();
                int eq = line.IndexOf('=');
                if (eq > 0) values[line.Substring(0, eq).Trim()] = line.Substring(eq + 1).Trim();
            }
            Func<string, string> get = delegate(string key)
            {
                string value;
                return values.TryGetValue(key, out value) ? value : "";
            };
            string message;
            bool ok = PdfAuditRunner.TestConnection(get("pdf_ai_provider"), get("pdf_ai_base"),
                get("pdf_ai_model"), get("pdf_ai_key"), get("pdf_ai_proxy"), out message);
            Console.WriteLine((ok ? "PASS " : "FAIL ") + message);
            return ok ? 0 : 1;
        }
    }
}
