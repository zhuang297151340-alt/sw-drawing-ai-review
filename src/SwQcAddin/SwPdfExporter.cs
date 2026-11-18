using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Runtime.InteropServices;
using SolidWorks.Interop.sldworks;
using SolidWorks.Interop.swconst;

namespace SwQc
{
    /// <summary>
    /// PDF 视觉通道专用导出器。只使用 SolidWorks 原生 PDF 导出当前活动图纸页；
    /// 不使用虚拟打印机或 SaveBMP 冒充完整 PDF。
    /// </summary>
    public static class SwPdfExporter
    {
        public static PdfExportResult ExportCurrentSheet(ISldWorks app, IModelDoc2 doc, string jobDir)
        {
            var result = new PdfExportResult();
            if (app == null || doc == null)
            {
                result.Error = "没有活动的 SolidWorks 工程图。";
                return result;
            }
            try
            {
                if (doc.GetType() != (int)swDocumentTypes_e.swDocDRAWING)
                {
                    result.Error = "当前活动文档不是 SolidWorks 工程图。";
                    return result;
                }
                var drawing = doc as IDrawingDoc;
                if (drawing == null)
                {
                    result.Error = "无法取得工程图接口。";
                    return result;
                }
                int sheetCount = drawing.GetSheetCount();
                if (sheetCount <= 0)
                {
                    result.Error = "当前工程图没有可导出的图纸页。";
                    return result;
                }
                result.ExpectedPages = 1;
                Directory.CreateDirectory(jobDir);
                string businessName;
                string nameError;
                if (!TryBuildBusinessFileName(drawing, out businessName, out nameError))
                {
                    result.Error = nameError;
                    return result;
                }
                string pdfPath = Path.Combine(jobDir, businessName + ".pdf");

                try { doc.ForceRebuild3(false); } catch { }
                IExportPdfData pdfData = app.GetExportFileData(
                    (int)swExportDataFileType_e.swExportPdfData) as IExportPdfData;
                if (pdfData == null)
                {
                    result.Error = "SolidWorks 没有返回 PDF 导出组件。请确认“另存为”中可以选择 PDF。";
                    return result;
                }
                bool sheetsOk = pdfData.SetSheets(
                    (int)swExportDataSheetsToExport_e.swExportData_ExportCurrentSheet, null);
                // 某些环境对 CurrentSheet + null 也可能返回 false；只回退当前页对象，
                // 不枚举、不激活、也不导出其他图纸页。
                if (!sheetsOk)
                {
                    ISheet current = drawing.GetCurrentSheet() as ISheet;
                    if (current != null)
                    {
                        var wrapper = new[] { new DispatchWrapper(current) };
                        sheetsOk = pdfData.SetSheets(
                            (int)swExportDataSheetsToExport_e.swExportData_ExportSpecifiedSheets,
                            wrapper);
                        if (sheetsOk)
                            result.Warning = "SolidWorks 不接受“当前页”参数，已改用当前页对象导出。";
                    }
                }
                if (!sheetsOk)
                {
                    result.Error = "SolidWorks 无法设置导出当前图纸页。";
                    return result;
                }
                pdfData.ViewPdfAfterSaving = false;
                int errors = 0, warnings = 0;
                int options = (int)swSaveAsOptions_e.swSaveAsOptions_Silent |
                              (int)swSaveAsOptions_e.swSaveAsOptions_Copy;
                bool ok = doc.Extension.SaveAs(pdfPath,
                    (int)swSaveAsVersion_e.swSaveAsCurrentVersion,
                    options, pdfData, ref errors, ref warnings);
                if (!ok || !File.Exists(pdfPath) || new FileInfo(pdfPath).Length < 2000)
                {
                    result.Error = "SolidWorks 原生 PDF 导出失败（errors=" + errors +
                                   "，warnings=" + warnings + "）。";
                    return result;
                }
                if (warnings != 0)
                {
                    if (!string.IsNullOrEmpty(result.Warning)) result.Warning += " ";
                    result.Warning += "SolidWorks 导出警告码：" + warnings +
                                      "；后续将继续校验页数与空白页。";
                }
                result.PdfPath = pdfPath;
                result.Success = true;
                return result;
            }
            catch (Exception ex)
            {
                result.Error = "SolidWorks 原生 PDF 导出异常：" + ex.Message;
                return result;
            }
        }

        /// <summary>从当前图纸页关联的 3D 模型读取“唯一编码 + 名称”。</summary>
        private static bool TryBuildBusinessFileName(IDrawingDoc drawing,
            out string fileName, out string error)
        {
            fileName = "";
            error = "";
            IModelDoc2 model = null;
            string configuration = "";
            try
            {
                IView view = drawing.GetFirstView() as IView;
                while (view != null)
                {
                    IModelDoc2 referenced = null;
                    try { referenced = view.ReferencedDocument as IModelDoc2; } catch { }
                    if (referenced != null)
                    {
                        model = referenced;
                        try { configuration = view.ReferencedConfiguration ?? ""; } catch { }
                        break;
                    }
                    try { view = view.GetNextView() as IView; }
                    catch { view = null; }
                }
            }
            catch { }
            if (model == null)
            {
                error = "无法取得当前图纸页关联的 3D 模型，不能生成业务命名的 PDF。";
                return false;
            }

            string[] codeKeys = { "物料编码", "图号", "图样代号" };
            string[] nameKeys = { "名称", "零件名称", "图样名称", "物料名称", "品名", "Description", "描述" };
            List<string> codes = PropertyValues(model, configuration, codeKeys)
                .Select(CleanFilePart).Where(x => !string.IsNullOrWhiteSpace(x))
                .Distinct(StringComparer.OrdinalIgnoreCase).ToList();
            if (codes.Count > 1)
            {
                error = "关联 3D 模型的“物料编码 / 图号 / 图样代号”存在多个不同值，无法确定 PDF 文件名。";
                return false;
            }
            string code = codes.Count == 1 ? codes[0] : "";
            string displayName = FirstProperty(model, configuration, nameKeys);
            code = CleanFilePart(code);
            displayName = CleanFilePart(displayName);
            if (string.IsNullOrWhiteSpace(code))
            {
                error = "关联 3D 模型没有填写“物料编码 / 图号 / 图样代号”，已停止导出 PDF。";
                return false;
            }
            if (string.IsNullOrWhiteSpace(displayName))
            {
                error = "关联 3D 模型没有名称，已停止导出 PDF。";
                return false;
            }
            fileName = code + " " + displayName;
            return true;
        }

        private static List<string> PropertyValues(IModelDoc2 model, string configuration, string[] keys)
        {
            var values = new List<string>();
            if (model == null) return values;
            IModelDocExtension extension = null;
            try { extension = model.Extension as IModelDocExtension; } catch { }
            if (extension == null) return values;
            string[] scopes = string.IsNullOrWhiteSpace(configuration) ?
                new[] { "" } : new[] { configuration, "" };
            foreach (string key in keys)
            {
                foreach (string scope in scopes)
                {
                    try
                    {
                        ICustomPropertyManager manager =
                            extension.get_CustomPropertyManager(scope) as ICustomPropertyManager;
                        if (manager == null) continue;
                        string raw, resolved;
                        manager.Get4(key, false, out raw, out resolved);
                        string value = string.IsNullOrWhiteSpace(resolved) ? raw : resolved;
                        if (!string.IsNullOrWhiteSpace(value) &&
                            !value.TrimStart().StartsWith("$PRP", StringComparison.OrdinalIgnoreCase))
                            values.Add(value.Trim());
                    }
                    catch { }
                }
            }
            return values;
        }

        private static string FirstProperty(IModelDoc2 model, string configuration, string[] keys)
        {
            if (model == null) return "";
            IModelDocExtension extension = null;
            try { extension = model.Extension as IModelDocExtension; } catch { }
            if (extension == null) return "";
            string[] scopes = string.IsNullOrWhiteSpace(configuration) ?
                new[] { "" } : new[] { configuration, "" };
            foreach (string key in keys)
            {
                foreach (string scope in scopes)
                {
                    try
                    {
                        ICustomPropertyManager manager =
                            extension.get_CustomPropertyManager(scope) as ICustomPropertyManager;
                        if (manager == null) continue;
                        string raw, resolved;
                        manager.Get4(key, false, out raw, out resolved);
                        string value = string.IsNullOrWhiteSpace(resolved) ? raw : resolved;
                        if (!string.IsNullOrWhiteSpace(value) &&
                            !value.TrimStart().StartsWith("$PRP", StringComparison.OrdinalIgnoreCase))
                            return value.Trim();
                    }
                    catch { }
                }
            }
            return "";
        }

        private static string CleanFilePart(string value)
        {
            string result = (value ?? "").Replace("\r", " ").Replace("\n", " ").Trim();
            foreach (char ch in Path.GetInvalidFileNameChars()) result = result.Replace(ch, '_');
            while (result.Contains("  ")) result = result.Replace("  ", " ");
            return result.Trim(' ', '.');
        }
    }
}

