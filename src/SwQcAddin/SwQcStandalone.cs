using System;
using System.IO;
using System.Runtime.InteropServices;
using System.Windows.Forms;
using SolidWorks.Interop.sldworks;
using SolidWorks.Interop.swconst;

namespace SwQc
{
    /// <summary>
    /// 独立启动器（不需要 SW 插件注册）：连到**已经在运行**的 SolidWorks，
    /// 取当前工程图（或零件对应的同名工程图），直读原生数据并弹审图窗口。
    ///
    /// 为什么保留这条路：SW 的 Add-in 自动加载在某些机器/策略下不生效，
    /// 而这条只用 COM 连接，最直接。
    ///
    /// 注意：必须**非管理员**启动（和 SW 同权限），否则 COM 连不上正在运行的 SW。
    /// </summary>
    static class SwQcStandalone
    {
        [STAThread]
        static void Main(string[] args)
        {
            Application.EnableVisualStyles();
            Application.SetCompatibleTextRenderingDefault(false);

            ISldWorks sw = null;
            try
            {
                sw = (ISldWorks)Marshal.GetActiveObject("SldWorks.Application");
            }
            catch
            {
                try
                {
                    sw = (ISldWorks)Activator.CreateInstance(
                        Type.GetTypeFromProgID("SldWorks.Application"));
                }
                catch { }
            }
            if (sw == null)
            {
                MessageBox.Show(
                    "没连上 SolidWorks。\n\n" +
                    "请确认：\n" +
                    "  1. SolidWorks 已经打开；\n" +
                    "  2. 本工具**不要用管理员身份运行**（和 SW 同权限才能连上）。",
                    "工程图QC", MessageBoxButtons.OK, MessageBoxIcon.Warning);
                return;
            }

            IModelDoc2 doc = null;
            try { doc = sw.ActiveDoc as IModelDoc2; } catch { }
            if (doc == null)
            {
                MessageBox.Show("SolidWorks 里没有打开的文档。\n请先打开工程图，或打开零件。",
                    "工程图QC", MessageBoxButtons.OK, MessageBoxIcon.Information);
                return;
            }

            IModelDoc2 drawing = null;
            int t = 0;
            try { t = doc.GetType(); } catch { }
            if (t == (int)swDocumentTypes_e.swDocDRAWING)
            {
                drawing = doc;
            }
            else
            {
                string cand = FindSiblingDrawing(doc);
                if (string.IsNullOrEmpty(cand))
                {
                    string path = "";
                    try { path = doc.GetPathName(); } catch { }
                    string hint = string.IsNullOrEmpty(path)
                        ? "当前零件还没保存过，无法按文件名找工程图。"
                        : ("按「同目录、同文件名」查找：" +
                           Path.Combine(Path.GetDirectoryName(path),
                                        Path.GetFileNameWithoutExtension(path) + ".SLDDRW"));
                    MessageBox.Show("无对应工程图。\n\n" + hint,
                        "工程图QC", MessageBoxButtons.OK, MessageBoxIcon.Warning);
                    return;
                }
                int err = 0, warn = 0;
                try
                {
                    drawing = (IModelDoc2)sw.OpenDoc6(cand,
                        (int)swDocumentTypes_e.swDocDRAWING,
                        (int)swOpenDocOptions_e.swOpenDocOptions_Silent,
                        "", ref err, ref warn);
                }
                catch { }
                if (drawing == null)
                {
                    MessageBox.Show("找到工程图但打开失败：\n" + cand + "\n（错误码 " + err + "）",
                        "工程图QC", MessageBoxButtons.OK, MessageBoxIcon.Error);
                    return;
                }
            }

            string json = "", summary = "";
            try
            {
                Cursor.Current = Cursors.WaitCursor;
                json = SwDocReader.ReadNativeJson(sw, drawing, out summary);
            }
            catch (Exception ex)
            {
                MessageBox.Show("读取图纸失败：" + ex.Message, "工程图QC");
                return;
            }
            finally { Cursor.Current = Cursors.Default; }

            string name = "";
            try { name = Path.GetFileNameWithoutExtension(drawing.GetPathName()); } catch { }
            if (string.IsNullOrEmpty(name)) { try { name = drawing.GetTitle(); } catch { } }
            string tmp = SwDocReader.SaveJson(json, name);

            string outDir = Path.Combine(Path.GetTempPath(), "SwQcAddin", "out");
            QcReport rep = null;
            try
            {
                Cursor.Current = Cursors.WaitCursor;
                rep = QcRunner.RunNative(tmp, outDir, true);   // 先只读表头，弹出窗口
            }
            finally { Cursor.Current = Cursors.Default; }

            if (!string.IsNullOrEmpty(rep.RawError))
            {
                MessageBox.Show("审图未完成：\n\n" + rep.RawError +
                                "\n\n（原生读取摘要：" + summary + "）",
                    "工程图QC", MessageBoxButtons.OK, MessageBoxIcon.Error);
                return;
            }

            using (var f = new QcResultForm(rep))
            {
                f.CheckRequested += delegate
                {
                    try
                    {
                        Cursor.Current = Cursors.WaitCursor;
                        string j2 = SwDocReader.ReadNativeJson(sw, drawing, out summary);
                        string t2 = SwDocReader.SaveJson(j2, Path.GetFileNameWithoutExtension(tmp));
                        QcReport r2 = QcRunner.RunNative(t2, outDir);
                        if (!string.IsNullOrEmpty(r2.RawError)) { MessageBox.Show(r2.RawError, "工程图QC"); return; }
                        f.UpdateReport(r2);
                    }
                    finally { Cursor.Current = Cursors.Default; }
                };
                f.ShowDialog();
            }
        }

        static string FindSiblingDrawing(IModelDoc2 doc)
        {
            string path = "";
            try { path = doc.GetPathName(); } catch { }
            if (string.IsNullOrEmpty(path)) return null;
            string dir, baseName;
            try
            {
                dir = Path.GetDirectoryName(path);
                baseName = Path.GetFileNameWithoutExtension(path);
            }
            catch { return null; }
            string candidate = Path.Combine(dir, baseName + ".SLDDRW");
            if (File.Exists(candidate)) return candidate;
            try
            {
                foreach (string f in Directory.GetFiles(dir, "*.SLDDRW"))
                {
                    if (string.Equals(Path.GetFileNameWithoutExtension(f), baseName,
                                      StringComparison.OrdinalIgnoreCase))
                        return f;
                }
            }
            catch { }
            return null;
        }
    }
}
