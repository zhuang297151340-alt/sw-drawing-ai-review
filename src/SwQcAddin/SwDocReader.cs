using System;

using System.Collections;

using System.Collections.Generic;
using System.Globalization;

using System.IO;

using System.Text;

using SolidWorks.Interop.sldworks;

using SolidWorks.Interop.swconst;



namespace SwQc

{

    /// <summary>

    /// 从 .SLDDRW 直读信息，做成 .sw.json 交给判定层。

    ///

    /// 关键点（都是实测踩出来的）：

    /// 1. SW2016 这些 API 返回 Object(SAFEARRAY)，**不能用 dynamic 迟绑定**（会 DISP_E_BADINDEX）→ 全显式接口。

    /// 2. 图号/名称/材料/类别/规格/版本 这些属性通常在**零件(3D)的自定义属性**里，

    ///    工程图本身可能没设 → 要通过视图的 ReferencedDocument 反向读零件。

    /// 3. 零件材料可能只存在材质库属性里 → 用 MaterialIdName / MaterialUserName 兜底。

    /// </summary>

    public static class SwDocReader

    {

        public static string ReadNativeJson(ISldWorks swApp, IModelDoc2 doc, out string summary)

        {

            var warn = new List<string>();

            var probe = new List<string>();

            var props = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);

            // 零件(3D)属性单独一份：界面与判定都以它为准（工程图属性可能没填/不一致）

            var partProps = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);

            var notes = new List<string>();
                var formatNotes = new List<string>();   // 图纸格式(模板)注释，不参与判定

            var dims = new List<string>();

            var rough = new List<string>();

            var gtols = new List<string>();
            var datums = new List<string>();   // 基准符号：字母+位置+视图
            var welds = new List<string>();    // 焊接符号：文字+位置+视图
            var partHoles = new List<string>(); // 零件孔/螺纹特征（来自模型 HoleWzd）
            var seenPart = new HashSet<string>();

            var tbNotes = new List<string>();     // 图框(标题栏)注记：文字+坐标
            var notesMeta = new List<string>();   // 全部注记：文字+坐标+归属（判图层用）
            var sheetsMeta = new List<string>();   // 图纸页元数据（多 sheet）
            var viewBoxes = new List<string>();   // 视图包围盒 → 轮廓长宽

            var materialDbs = new List<string>();   // SW 材质库路径（判材料用）   // 几何公差：框内符号+数值

            // 2026-10-24：3D 模型包围盒（XYZ 轮廓的唯一可信来源，单位 mm）
            // 顺序：xmin, ymin, zmin, xmax, ymax, zmax；取不到为 null（判定层不猜）
            double[] partBox = null;
            bool isSheetMetal = false;    // 是否钣金件
            double thicknessMm = 0;       // 钣金板厚 mm，取不到为 0



            string file = "";

            string path = "";

            try { file = doc.GetTitle() ?? ""; } catch (Exception e) { warn.Add("GetTitle:" + Short(e)); }

            try { path = doc.GetPathName() ?? ""; } catch (Exception e) { warn.Add("GetPathName:" + Short(e)); }



            int sheetCount = 0;

            var sheetNames = new List<string>();

            int viewCount = 0, noteCount = 0, dimCount = 0, sfCount = 0, gtCount = 0;

            string scale1 = "", scale2 = "";

            string partPath = "";

            string partConfig = "";

            IModelDoc2 partDoc = null;



            // 取 SW 自带的材质库路径（用户要求：材料别靠手写字典，直接读 SW 材质库）

            try

            {

                foreach (object o in Flatten(swApp.GetMaterialDatabases()))

                {

                    string db = Convert.ToString(o);

                    if (!string.IsNullOrEmpty(db)) materialDbs.Add(db);

                }

                probe.Add("材质库: " + materialDbs.Count + " 个");

            }

            catch (Exception e) { warn.Add("GetMaterialDatabases:" + Short(e)); }



            IDrawingDoc dd = doc as IDrawingDoc;

            if (dd == null) warn.Add("当前文档不是工程图对象（IDrawingDoc 转换失败）");



            if (dd != null)

            {

                // ---------- 图纸 ----------

                try

                {

                    foreach (object o in Flatten(dd.GetSheetNames()))

                        sheetNames.Add(Convert.ToString(o));

                    sheetCount = sheetNames.Count;

                }

                catch (Exception e) { warn.Add("GetSheetNames:" + Short(e)); }



                // ---------- 比例 ----------

                try

                {

                    ISheet sheet = dd.GetCurrentSheet() as ISheet;

                    if (sheet != null)

                    {

                        object rawProps = sheet.GetProperties2();

                        List<object> arr = Flatten(rawProps);

                        probe.Add("GetProperties2 -> " + (rawProps == null ? "null" : rawProps.GetType().Name) +

                                  " [" + Join(arr) + "]");

                        double s1 = 0, s2 = 0;

                        if (arr.Count >= 4)

                        {

                            try { s1 = Convert.ToDouble(arr[2]); } catch { }

                            try { s2 = Convert.ToDouble(arr[3]); } catch { }

                        }

                        if (s1 > 0 && s2 > 0) { scale1 = s1.ToString("0.####"); scale2 = s2.ToString("0.####"); }

                    }

                }

                catch (Exception e) { warn.Add("GetCurrentSheet:" + Short(e)); }



                // ---------- 工程图自身属性（设计/审核等）----------

                try

                {

                    IModelDocExtension ext = doc.Extension;

                    int n = ReadProps(ext, "", props, false);

                    probe.Add("图属[文档] -> " + n + " 项");

                    // 同理：只读文档级，不读配置特定

                }

                catch (Exception e) { warn.Add("图属性:" + Short(e)); }



                string origSheetName = "";

                // ---------- 视图 / 注释 / 尺寸 / 粗糙度 / 几何公差 ----------

                try

                {

                    try { ISheet _cs0 = dd.GetCurrentSheet() as ISheet; if (_cs0 != null) origSheetName = _cs0.GetName(); } catch { }
                    var sheetOrder = new List<string>(sheetNames);
                    if (sheetOrder.Count == 0) sheetOrder.Add("");
                    foreach (string curSheet in sheetOrder)
                    {
                        if (!string.IsNullOrEmpty(curSheet))
                        {
                            try { dd.ActivateSheet(curSheet); } catch (Exception e) { warn.Add("ActivateSheet(" + curSheet + "):" + Short(e)); }
                        }
                        List<object> views = Flatten(dd.GetViews());
                        probe.Add("GetViews[" + curSheet + "] -> " + views.Count + " 个");
                        try
                        {
                            ISheet _sh = dd.GetCurrentSheet() as ISheet;
                            if (_sh != null)
                            {
                                List<object> pp = Flatten(_sh.GetProperties2());
                                double pw = 0, ph = 0, ps1 = 0, ps2 = 0;
                                if (pp.Count >= 7) { try { pw = Convert.ToDouble(pp[5]) * 1000.0; } catch { } try { ph = Convert.ToDouble(pp[6]) * 1000.0; } catch { } }
                                if (pp.Count >= 4) { try { ps1 = Convert.ToDouble(pp[2]); } catch { } try { ps2 = Convert.ToDouble(pp[3]); } catch { } }
                                sheetsMeta.Add("{\"name\":" + JsonStr(curSheet) + ",\"w_mm\":" + pw.ToString("0.#") + ",\"h_mm\":" + ph.ToString("0.#") + ",\"scale1\":" + ps1.ToString("0.####") + ",\"scale2\":" + ps2.ToString("0.####") + "}");
                                probe.Add("sheet[" + curSheet + "] paper=" + pw + "x" + ph + " scale=" + ps1 + ":" + ps2);
                            }
                        }
                        catch { }



                    foreach (object vo in views)

                    {

                        IView v = vo as IView;

                        if (v == null) { warn.Add("视图元素类型异常"); continue; }

                        viewCount++;

                        string vname = "";

                        try { vname = v.Name ?? ""; } catch { }



                        // 图纸视图（图纸1）上的注记 = 图框文字（标题栏），带坐标
                        bool isSheetView = false;
                        try { isSheetView = sheetNames.Contains(vname); } catch { }
                        if (isSheetView)
                        {
                            try
                            {
                                foreach (object ao in Flatten(v.GetAnnotations()))
                                {
                                    IAnnotation an = ao as IAnnotation;
                                    if (an == null) continue;
                                    string atxt = "";
                                    try
                                    {
                                        INote nt = an.GetSpecificAnnotation() as INote;
                                        if (nt != null) atxt = nt.GetText() ?? "";
                                    }
                                    catch { }
                                    if (string.IsNullOrWhiteSpace(atxt)) continue;
                                    double ax = 0, ay = 0;
                                    try
                                    {
                                        List<object> pos = Flatten(an.GetPosition());
                                        if (pos.Count >= 2)
                                        {
                                            ax = Convert.ToDouble(pos[0]) * 1000.0;
                                            ay = Convert.ToDouble(pos[1]) * 1000.0;
                                        }
                                    }
                                    catch { }
                                    int aowner = -1; string alayer = "";
                                    try { aowner = an.OwnerType; } catch { }
                                    try { alayer = an.Layer ?? ""; } catch { }
                                    tbNotes.Add("{\"text\":" + JsonStr(atxt.Replace("\r", " "))
                                                + ",\"x\":" + ax.ToString("0.#")
                                                + ",\"y\":" + ay.ToString("0.#")
                                                + ",\"owner\":" + aowner
                                                + ",\"layer\":" + JsonStr(alayer)
                                                + ",\"sheet\":" + JsonStr(curSheet) + "}");
                                }
                            }
                            catch { }
                        }

                        // 视图包围盒：图纸坐标(米) → 模型尺寸(mm) = 图纸尺寸 / 比例

                        try

                        {

                            List<object> ol = Flatten(v.GetOutline());

                            if (ol.Count >= 4)

                            {

                                double x0 = Convert.ToDouble(ol[0]);

                                double y0 = Convert.ToDouble(ol[1]);

                                double x1 = Convert.ToDouble(ol[2]);

                                double y1 = Convert.ToDouble(ol[3]);

                                double sc = 1.0;

                                try { if (v.ScaleDecimal > 0) sc = v.ScaleDecimal; } catch { }

                                double wmm = Math.Abs(x1 - x0) * 1000.0 / sc;

                                double hmm = Math.Abs(y1 - y0) * 1000.0 / sc;

                                viewBoxes.Add("{\"view\":" + JsonStr(vname) +

                                              ",\"w_mm\":" + wmm.ToString("0.###") +

                                              ",\"h_mm\":" + hmm.ToString("0.###") +

                                              ",\"scale\":" + sc.ToString("0.####") + ",\"sheet\":" + JsonStr(curSheet) + "}");

                            }

                        }

                        catch { }



                        if (partDoc == null && string.IsNullOrEmpty(partPath))

                        {

                            try

                            {

                                IModelDoc2 refDoc = v.ReferencedDocument as IModelDoc2;

                                if (refDoc != null) partDoc = refDoc;

                            }

                            catch { }

                            try { partPath = v.GetReferencedModelName() ?? ""; } catch { }

                            try { partConfig = v.ReferencedConfiguration ?? ""; } catch { }

                        }



                        try

                        {

                            bool isFormatView = vname.IndexOf("图纸格式", StringComparison.Ordinal) >= 0
                                             || vname.ToLowerInvariant().Contains("sheet format");
                            foreach (object no in Flatten(v.GetNotes()))

                            {

                                INote note = no as INote;

                                if (note == null) continue;

                                string txt = "";

                                try { txt = note.GetText() ?? ""; } catch { }

                                if (string.IsNullOrEmpty(txt)) continue;

                                string _t = txt.Replace("\r", "").Replace("\n", " ").Trim();
                                if (isFormatView)
                                {
                                    if (_t.Length > 0 && !formatNotes.Contains(_t)) formatNotes.Add(_t);
                                    continue;
                                }
                                if (_t.Length > 0 && !notes.Contains(JsonStr(_t)))
                                    notes.Add(JsonStr(_t));

                                noteCount++;
                                try
                                {
                                    double nx = 0, ny = 0;
                                    int nowner = -1;
                                    string nlayer = "";
                                    IAnnotation nan = note.GetAnnotation() as IAnnotation;
                                    if (nan != null)
                                    {
                                        try
                                        {
                                            List<object> np = Flatten(nan.GetPosition());
                                            if (np.Count >= 2)
                                            {
                                                nx = Convert.ToDouble(np[0]) * 1000.0;
                                                ny = Convert.ToDouble(np[1]) * 1000.0;
                                            }
                                        }
                                        catch { }
                                        try { nowner = nan.OwnerType; } catch { }
                                        try { nlayer = nan.Layer ?? ""; } catch { }
                                    }
                                    notesMeta.Add("{\"text\":" + JsonStr(_t)
                                                  + ",\"x\":" + nx.ToString("0.#")
                                                  + ",\"y\":" + ny.ToString("0.#")
                                                  + ",\"owner\":" + nowner
                                                  + ",\"layer\":" + JsonStr(nlayer)
                                                  + ",\"sheet\":" + JsonStr(curSheet)
                                                  + ",\"view\":" + JsonStr(vname) + "}");
                                }
                                catch { }

                            }

                        }

                        catch (Exception e) { warn.Add("GetNotes(" + vname + "):" + Short(e)); }



                        try

                        {

                            foreach (object so in Flatten(v.GetSFSymbols()))

                            {

                                sfCount++;

                                ISFSymbol sf = so as ISFSymbol;

                                if (sf == null) continue;

                                try

                                {

                                    int tc = 0;

                                    try { tc = sf.GetTextCount(); } catch { }

                                    var sfsb = new StringBuilder();

                                    for (int ti = 0; ti < tc; ti++)

                                    {

                                        try

                                        {

                                            string piece = sf.GetTextAtIndex(ti);

                                            if (!string.IsNullOrEmpty(piece)) sfsb.Append(piece);

                                        }

                                        catch { }

                                    }

                                    if (sfsb.Length > 0) rough.Add(JsonStr(sfsb.ToString()));

                                }

                                catch { }

                            }

                        }

                        catch (Exception e) { warn.Add("GetSFSymbols(" + vname + "):" + Short(e)); }



                        try

                        {

                            foreach (object go in Flatten(v.GetGTols()))

                            {

                                IGtol gt = go as IGtol;

                                if (gt == null) continue;

                                gtCount++;

                                var frames = new List<string>();
                                var symNames = new List<string>();

                                int fc = 0;

                                try { fc = gt.GetFrameCount(); } catch { }

                                for (int fi = 1; fi <= fc; fi++)

                                {

                                    string sym = "", val = "";

                                    // 2026-10-24：GetFrameSymbols3 在某些版本返回空 → 依次回退
                                    try { sym = Join(Flatten(gt.GetFrameSymbols3(fi))); } catch { }
                                    if (sym.Replace(",", "").Trim().Length == 0)
                                    {
                                        try { sym = Join(Flatten(gt.GetFrameSymbols2((short)fi))); } catch { }
                                    }
                                    if (sym.Replace(",", "").Trim().Length == 0)
                                    {
                                        try { sym = Join(Flatten(gt.GetFrameSymbols((short)fi))); } catch { }
                                    }

                                    try { val = Join(Flatten(gt.GetFrameValues((short)fi))); } catch { }

                                    frames.Add(JsonStr(sym + "|" + val));
                                    // 2026-10-24：符号码拿不到时，记符号名/描述做兜底
                                    try
                                    {
                                        for (int si = 0; si < 4; si++)
                                        {
                                            string snm = "";
                                            string sds = "";
                                            try { snm = gt.GetSymName((short)si) ?? ""; } catch { break; }
                                            try { sds = gt.GetSymDesc((short)si) ?? ""; } catch { }
                                            if (string.IsNullOrWhiteSpace(snm) && string.IsNullOrWhiteSpace(sds)) break;
                                            symNames.Add(JsonStr(snm + ":" + sds));
                                        }
                                    }
                                    catch { }
                                    // 原始探针：把返回值原样记下来，取不到时也能查
                                    probe.Add("gtol#" + gtCount + " frame" + fi + " sym=[" + sym + "] val=[" + val + "]");

                                }

                                string gtxt = "";

                                try

                                {

                                    int tc = 0;

                                    try { tc = gt.GetTextCount(); } catch { }

                                    var gsb = new StringBuilder();

                                    for (int ti = 0; ti < tc; ti++)

                                    {

                                        try { gsb.Append(gt.GetTextAtIndex(ti)); } catch { }

                                    }

                                    gtxt = gsb.ToString();

                                }

                                catch { }

                                double gxmm = 0, gymm = 0;
                                try
                                {
                                    List<object> _gp = Flatten(gt.GetAttachPos());
                                    if (_gp.Count >= 2)
                                    {
                                        gxmm = Convert.ToDouble(_gp[0]) * 1000.0;
                                        gymm = Convert.ToDouble(_gp[1]) * 1000.0;
                                    }
                                }
                                catch { }
                                if (Math.Abs(gxmm) < 1e-9 && Math.Abs(gymm) < 1e-9)
                                {
                                    try
                                    {
                                        List<object> _tp = Flatten(gt.GetTextPoint());
                                        if (_tp.Count >= 2)
                                        {
                                            gxmm = Convert.ToDouble(_tp[0]) * 1000.0;
                                            gymm = Convert.ToDouble(_tp[1]) * 1000.0;
                                        }
                                    }
                                    catch { }
                                }
                                // 引线箭头点（判断“几何公差指向哪个尺寸”的关键）
                                double axmm = 0, aymm = 0;
                                string leaderPointsJson = "[]";
                                try
                                {
                                    IAnnotation gan = gt.GetAnnotation() as IAnnotation;
                                    if (gan != null && gan.GetLeaderCount() > 0)
                                    {
                                        List<object> lp = Flatten(gan.GetLeaderPointsAtIndex(0));
                                        // SOLIDWORKS returns X,Y,Z triples.  The first point is the
                                        // arrowhead.  The old code used the final two array values,
                                        // which are Y,Z (not X,Y), and therefore linked GDTs to the
                                        // wrong dimension deterministically.
                                        if (lp.Count >= 3)
                                        {
                                            axmm = Convert.ToDouble(lp[0]) * 1000.0;
                                            aymm = Convert.ToDouble(lp[1]) * 1000.0;
                                            var pts = new List<string>();
                                            for (int pi = 0; pi + 2 < lp.Count; pi += 3)
                                            {
                                                double px = Convert.ToDouble(lp[pi]) * 1000.0;
                                                double py = Convert.ToDouble(lp[pi + 1]) * 1000.0;
                                                pts.Add("[" + px.ToString("0.##") + "," + py.ToString("0.##") + "]");
                                            }
                                            leaderPointsJson = "[" + string.Join(",", pts.ToArray()) + "]";
                                        }
                                    }
                                }
                                catch { }
                                gtols.Add("{\"text\":" + JsonStr(gtxt) +
                                          ",\"ax\":" + axmm.ToString("0.##") +
                                          ",\"ay\":" + aymm.ToString("0.##") +
                                          ",\"x\":" + gxmm.ToString("0.##") +
                                          ",\"y\":" + gymm.ToString("0.##") +
                                          ",\"leader_points\":" + leaderPointsJson +

                                          ",\"frames\":[" + string.Join(",", frames.ToArray()) +
                                          "],\"symnames\":[" + string.Join(",", symNames.ToArray()) +

                                          "],\"view\":" + JsonStr(vname) + "}");

                            }

                        }

                        catch { }

                        // 基准符号（Datum Feature Symbol）：字母 + 位置 + 视图
                        try
                        {
                            object dts = v.GetDatumTags();
                            foreach (object dto in Flatten(dts))
                            {
                                IDatumTag dt = dto as IDatumTag;
                                if (dt == null) continue;
                                string dlabel = "";
                                try { dlabel = dt.GetLabel() ?? ""; } catch { }
                                if (string.IsNullOrEmpty(dlabel)) continue;
                                double dx = 0, dy = 0;
                                try
                                {
                                    IAnnotation dan = dt.GetAnnotation() as IAnnotation;
                                    if (dan != null)
                                    {
                                        List<object> dp = Flatten(dan.GetPosition());
                                        if (dp.Count >= 2)
                                        {
                                            dx = Convert.ToDouble(dp[0]) * 1000.0;
                                            dy = Convert.ToDouble(dp[1]) * 1000.0;
                                        }
                                    }
                                }
                                catch { }
                                // 基准指向的要素：leader 箭头点（最后一个点）或三角形中心
                                double tx = 0, ty = 0; int tsrc = 0;
                                try
                                {
                                    IAnnotation dan2 = dt.GetAnnotation() as IAnnotation;
                                    if (dan2 != null)
                                    {
                                        int lc = dan2.GetLeaderCount();
                                        if (lc > 0)
                                        {
                                            object lp = dan2.GetLeaderPointsAtIndex(0);
                                            List<object> pts = Flatten(lp);
                                            if (pts.Count >= 2)
                                            {
                                                tx = Convert.ToDouble(pts[pts.Count - 2]) * 1000.0;
                                                ty = Convert.ToDouble(pts[pts.Count - 1]) * 1000.0;
                                                tsrc = 1;
                                            }
                                        }
                                    }
                                }
                                catch { }
                                // 无 leader/三角形时：用 line 端点推断指向
                                if (tsrc == 0)
                                {
                                    try
                                    {
                                        int lcn = dt.GetLineCount();
                                        if (lcn > 0)
                                        {
                                            List<object> ln = Flatten(dt.GetLineAtIndex(0));
                                            if (ln.Count >= 6)
                                            {
                                                double x1 = Convert.ToDouble(ln[0]);
                                                double y1 = Convert.ToDouble(ln[1]);
                                                double x2 = Convert.ToDouble(ln[3]);
                                                double y2 = Convert.ToDouble(ln[4]);
                                                tx = ((x1 + x2) / 2.0) * 1000.0;
                                                ty = ((y1 + y2) / 2.0) * 1000.0;
                                                tsrc = 3;
                                            }
                                        }
                                    }
                                    catch { }
                                }
                                if (tsrc == 0)
                                {
                                    try
                                    {
                                        List<object> tri = Flatten(dt.GetTriangleAtIndex(0));
                                        if (tri.Count >= 6)
                                        {
                                            tx = ((Convert.ToDouble(tri[0]) + Convert.ToDouble(tri[4])) / 2.0) * 1000.0;
                                            ty = ((Convert.ToDouble(tri[1]) + Convert.ToDouble(tri[5])) / 2.0) * 1000.0;
                                            tsrc = 2;
                                        }
                                    }
                                    catch { }
                                }
                                int annLeader = 0, triCnt = 0, lineCnt = 0;
                                try { var _a = dt.GetAnnotation() as IAnnotation; if (_a != null) annLeader = _a.GetLeaderCount(); } catch { }
                                try { triCnt = dt.GetTriangleCount(); } catch { }
                                try { lineCnt = dt.GetLineCount(); } catch { }
                                datums.Add("{\"ann_leaders\":" + annLeader
                                           + ",\"tri_count\":" + triCnt
                                           + ",\"line_count\":" + lineCnt
                                           + ",\"letter\":" + JsonStr(dlabel.Trim())
                                           + ",\"x\":" + dx.ToString("0.##")
                                           + ",\"y\":" + dy.ToString("0.##")
                                           + ",\"target_x\":" + tx.ToString("0.##")
                                           + ",\"target_y\":" + ty.ToString("0.##")
                                           + ",\"target_src\":" + tsrc
                                           + ",\"view\":" + JsonStr(vname)
                                           + ",\"sheet\":" + JsonStr(curSheet) + "}");
                            }
                        }
                        catch (Exception e) { warn.Add("GetDatumTags(" + vname + "):" + Short(e)); }

                        // 焊接符号（Weld Symbol）
                        try
                        {
                            object wss = v.GetWeldSymbols();
                            foreach (object wso in Flatten(wss))
                            {
                                IWeldSymbol ws = wso as IWeldSymbol;
                                if (ws == null) continue;
                                string wtxt = "";
                                try
                                {
                                    int tc = ws.GetTextCount();
                                    for (int ti = 0; ti < tc; ti++)
                                    {
                                        string seg = ws.GetTextAtIndex(ti) ?? "";
                                        if (seg.Length > 0) wtxt += (wtxt.Length > 0 ? " " : "") + seg;
                                    }
                                }
                                catch { }
                                double wx = 0, wy = 0;
                                try
                                {
                                    IAnnotation wan = ws.GetAnnotation() as IAnnotation;
                                    if (wan != null)
                                    {
                                        List<object> wp = Flatten(wan.GetPosition());
                                        if (wp.Count >= 2)
                                        {
                                            wx = Convert.ToDouble(wp[0]) * 1000.0;
                                            wy = Convert.ToDouble(wp[1]) * 1000.0;
                                        }
                                    }
                                }
                                catch { }
                                welds.Add("{\"text\":" + JsonStr(wtxt.Trim())
                                          + ",\"x\":" + wx.ToString("0.##")
                                          + ",\"y\":" + wy.ToString("0.##")
                                          + ",\"view\":" + JsonStr(vname)
                                          + ",\"sheet\":" + JsonStr(curSheet) + "}");
                            }
                        }
                        catch (Exception e) { warn.Add("GetWeldSymbols(" + vname + "):" + Short(e)); }

                        // 零件模型特征：异型孔向导（螺纹 M3 等）
                        try
                        {
                            ModelDoc2 partDocHole = v.ReferencedDocument as ModelDoc2;
                            if (partDocHole != null)
                            {
                                string ptitle = "";
                                try { ptitle = partDocHole.GetPathName() ?? ""; } catch { }
                                string pkey = string.IsNullOrEmpty(ptitle) ? vname : ptitle;
                                if (seenPart.Add(pkey))
                                {
                                    IFeature f = partDocHole.FirstFeature() as IFeature;
                                    int guard = 0;
                                    while (f != null && guard++ < 2000)
                                    {
                                        string tn = "";
                                        try { tn = f.GetTypeName2() ?? ""; } catch { }
                                        if (tn.IndexOf("HoleWzd", StringComparison.OrdinalIgnoreCase) >= 0 ||
                                            tn.IndexOf("HoleSeries", StringComparison.OrdinalIgnoreCase) >= 0)
                                        {
                                            try
                                            {
                                                IWizardHoleFeatureData2 hd = f.GetDefinition() as IWizardHoleFeatureData2;
                                                if (hd != null)
                                                {
                                                    string fs = "", ft = "", tc = "", std = "";
                                                    double hdDia = 0, thDia = 0, thDep = 0;
                                                    try { fs = hd.FastenerSize ?? ""; } catch { }
                                                    try { ft = hd.FastenerType ?? ""; } catch { }
                                                    try { tc = hd.ThreadClass ?? ""; } catch { }
                                                    try { std = hd.Standard ?? ""; } catch { }
                                                    try { hdDia = hd.HoleDiameter * 1000.0; } catch { }
                                                    try { thDia = hd.ThreadDiameter * 1000.0; } catch { }
                                                    try { thDep = hd.ThreadDepth * 1000.0; } catch { }
                                                    partHoles.Add("{\"name\":" + JsonStr(tn)
                                                        + ",\"fastener_size\":" + JsonStr(fs)
                                                        + ",\"fastener_type\":" + JsonStr(ft)
                                                        + ",\"thread_class\":" + JsonStr(tc)
                                                        + ",\"standard\":" + JsonStr(std)
                                                        + ",\"hole_dia\":" + hdDia.ToString("0.###")
                                                        + ",\"thread_dia\":" + thDia.ToString("0.###")
                                                        + ",\"thread_depth\":" + thDep.ToString("0.###")
                                                        + "}");
                                                }
                                            }
                                            catch { }
                                        }
                                        try { f = f.GetNextFeature() as IFeature; } catch { break; }
                                    }
                                }
                            }
                        }
                        catch (Exception e) { warn.Add("ReferencedDocument(" + vname + "):" + Short(e)); }

                        try

                        {

                            foreach (object ddo in Flatten(v.GetDisplayDimensions()))

                            {

                                IDisplayDimension disp = ddo as IDisplayDimension;

                                if (disp == null) { warn.Add("尺寸元素类型异常"); continue; }

                                string txt = "";

                                double mm = 0; int tolType = 0; string tolVal = "";

                                // 2026-10-24：Ⓔ 包容要求是**尺寸文本**的一部分，不在 IDimensionTolerance 上。
                                // 把各个文本位都读出来拼成 text_all，交给判定层找 Ⓔ/Ⓜ/Ⓛ。
                                string txtAll = "";
                                // 2026-10-24：记标注位置，用于把几何公差挂到对应尺寸
                                double dxmm = 0, dymm = 0;
                                try
                                {
                                    IAnnotation _an = disp.GetAnnotation() as IAnnotation;
                                    if (_an != null)
                                    {
                                        List<object> _p = Flatten(_an.GetPosition());
                                        if (_p.Count >= 2)
                                        {
                                            dxmm = Convert.ToDouble(_p[0]) * 1000.0;
                                            dymm = Convert.ToDouble(_p[1]) * 1000.0;
                                        }
                                    }
                                }
                                catch { }
                                var _tx = new StringBuilder();
                                try { _tx.Append(disp.GetText((int)swDimensionTextParts_e.swDimensionTextAll) ?? ""); } catch { }
                                try { _tx.Append("|").Append(disp.GetText((int)swDimensionTextParts_e.swDimensionTextPrefix) ?? ""); } catch { }
                                try { _tx.Append("|").Append(disp.GetText((int)swDimensionTextParts_e.swDimensionTextSuffix) ?? ""); } catch { }
                                try { _tx.Append("|").Append(disp.GetText((int)swDimensionTextParts_e.swDimensionTextCalloutAbove) ?? ""); } catch { }
                                try { _tx.Append("|").Append(disp.GetText((int)swDimensionTextParts_e.swDimensionTextCalloutBelow) ?? ""); } catch { }
                                try { _tx.Append("|").Append(disp.GetLowerText() ?? ""); } catch { }
                                txtAll = _tx.ToString();
                                try { txt = disp.GetText((int)swDimensionTextParts_e.swDimensionTextAll) ?? ""; } catch { }

                                try

                                {

                                    IDimension dim = disp.GetDimension2(0) as IDimension;

                                    if (dim != null)

                                    {

                                        mm = dim.SystemValue * 1000.0;

                                        try { tolType = dim.GetToleranceType(); } catch { }

                                        try { tolVal = Join(Flatten(dim.GetToleranceValues())); } catch { }

                                    }

                                }

                                catch { }

                                // 诊断：把所有 text part（0..10）都导出，用于找 M3 到底在哪一位
                                string textParts = "";
                                try
                                {
                                    var tpv = new List<string>();
                                    for (int tp = 0; tp <= 10; tp++)
                                    {
                                        string pv2 = "";
                                        try { pv2 = disp.GetText(tp) ?? ""; } catch { }
                                        if (!string.IsNullOrEmpty(pv2)) tpv.Add(tp + ":" + pv2);
                                    }
                                    textParts = string.Join("~", tpv.ToArray());
                                }
                                catch { }

                                // 孔标注（Hole Callout）：螺纹代号 M3 / 深度等常在这里，text_all 里可能没有
                                string holeCallout = "";
                                try
                                {
                                    object hcv = disp.GetHoleCalloutVariables();
                                    if (hcv != null)
                                        holeCallout = Join(Flatten(hcv));
                                }
                                catch { }

                                dims.Add("{\"text\":" + JsonStr(txt) +
                                         ",\"hole_callout\":" + JsonStr(holeCallout) +
                                         ",\"text_parts\":" + JsonStr(textParts) +

                                         ",\"value_mm\":" + mm.ToString("0.####") +

                                         ",\"x\":" + dxmm.ToString("0.##") +
                                         ",\"y\":" + dymm.ToString("0.##") +
                                         ",\"text_all\":" + JsonStr(txtAll) +
                                         ",\"tol_type\":" + tolType +

                                         ",\"tol_value\":" + JsonStr(tolVal) +

                                         ",\"view\":" + JsonStr(vname) + ",\"sheet\":" + JsonStr(curSheet) + "}");

                                dimCount++;

                            }

                        }

                        catch (Exception e) { warn.Add("GetDisplayDimensions(" + vname + "):" + Short(e)); }


                    }
                }
                    }

                catch (Exception e) { warn.Add("GetViews/遍历:" + Short(e)); }

                try { if (!string.IsNullOrEmpty(origSheetName)) dd.ActivateSheet(origSheetName); } catch { }



                // ---------- 关联零件(3D)的属性 + 材料 ----------

                try

                {

                    if (partDoc == null && !string.IsNullOrEmpty(partPath))

                    {

                        try { partDoc = swApp.GetOpenDocumentByName(partPath) as IModelDoc2; } catch { }

                        if (partDoc == null)

                        {

                            int err = 0, wrn = 0;

                            try

                            {

                                partDoc = swApp.OpenDoc6(partPath,

                                    (int)swDocumentTypes_e.swDocPART,

                                    (int)swOpenDocOptions_e.swOpenDocOptions_Silent |

                                    (int)swOpenDocOptions_e.swOpenDocOptions_ReadOnly,

                                    "", ref err, ref wrn) as IModelDoc2;

                            }

                            catch { }

                        }

                    }

                    probe.Add("关联模型: " + (partDoc == null ? "未拿到" : Path.GetFileName(partPath)) +

                              (partConfig.Length > 0 ? " [" + partConfig + "]" : ""));



                    if (partDoc != null)

                    {

                        IModelDocExtension pext = partDoc.Extension;

                        // ============================================================
                        // 2026-10-24 新增：3D 模型包围盒（XYZ 轮廓）+ 钣金板厚
                        // 用户要求：轮廓尺寸必须 100% 准确、不能误判 → 一律以 3D 模型为准，
                        //           取不到就是 null，判定层退回"未取到"，绝不猜。
                        // 用户要求：板厚只对钣金件识别，机加件/塑胶件不做。
                        // ============================================================
                        try
                        {
                            partBox = GetPartBoxMm(partDoc as IPartDoc);
                            if (partBox != null)
                            {
                                probe.Add(string.Format(CultureInfo.InvariantCulture,
                                    "3D包围盒(mm): X {0:0.###}~{1:0.###}  Y {2:0.###}~{3:0.###}  Z {4:0.###}~{5:0.###}",
                                    partBox[0], partBox[3], partBox[1], partBox[4], partBox[2], partBox[5]));
                            }
                            else
                            {
                                probe.Add("3D包围盒: 未取到（轮廓判定将退回视图推断）");
                            }

                            double th;
                            if (TryGetSheetMetalThickness(partDoc, out th))
                            {
                                isSheetMetal = true;
                                thicknessMm = th;
                                probe.Add(string.Format(CultureInfo.InvariantCulture,
                                    "钣金板厚: {0:0.###} mm（取自钣金特征）", th));
                            }
                            else if (HasSheetMetalFeature(partDoc))
                            {
                                isSheetMetal = true;
                                probe.Add("钣金件：找到钣金特征，但未取到厚度值");
                            }
                        }
                        catch (Exception e) { warn.Add("3D包围盒:" + Short(e)); }



                        // 用户要求（2026-10-23）：

                        // 1) 自定义(文档级) 和 配置特定 都要读

                        // 2) 以「自定义」为主：自定义有值就用自定义

                        // 3) 自定义没值，才用配置特定里的值

                        var docProps = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);

                        var cfgProps = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);



                        int nDoc = ReadProps(pext, "", docProps, true);

                        probe.Add("零属[文档/自定义] -> " + nDoc + " 项");



                        var cfgScopes = new List<string>();

                        if (!string.IsNullOrEmpty(partConfig)) cfgScopes.Add(partConfig);

                        try

                        {

                            foreach (object c in Flatten(partDoc.GetConfigurationNames()))

                            {

                                string cn = Convert.ToString(c);

                                if (!string.IsNullOrEmpty(cn) && !cfgScopes.Contains(cn))

                                    cfgScopes.Add(cn);

                            }

                        }

                        catch { }

                        foreach (string cs in cfgScopes)

                        {

                            int n = ReadProps(pext, cs, cfgProps, false);  // 不覆盖，先出现的优先

                            probe.Add("零属[配置/" + cs + "] -> " + n + " 项");

                        }



                        // 合并：自定义优先

                        foreach (KeyValuePair<string, string> kv in docProps)

                            partProps[kv.Key] = kv.Value;

                        foreach (KeyValuePair<string, string> kv in cfgProps)

                            if (!partProps.ContainsKey(kv.Key))

                            {

                                partProps[kv.Key] = kv.Value;

                                probe.Add("配置补充: " + kv.Key);

                            }

                        foreach (KeyValuePair<string, string> kv in partProps)

                            props[kv.Key] = kv.Value;



                        if (!props.ContainsKey("材料"))

                        {

                            string mat = "";

                            try { mat = partDoc.MaterialIdName ?? ""; } catch { }

                            if (string.IsNullOrEmpty(mat)) { try { mat = partDoc.MaterialUserName ?? ""; } catch { } }

                            if (!string.IsNullOrEmpty(mat))

                            {

                                props["材料"] = mat;

                                probe.Add("材料来自材质库: " + mat);

                            }

                        }

                    }

                }

                catch (Exception e) { warn.Add("关联零件属性:" + Short(e)); }

            }



            var sb = new StringBuilder();

            sb.Append("{");

            sb.Append("\"source\":\"solidworks\",");

            sb.Append("\"file\":").Append(JsonStr(file)).Append(",");

            sb.Append("\"path\":").Append(JsonStr(path)).Append(",");

            sb.Append("\"sheet_count\":").Append(sheetCount).Append(",");

            sb.Append("\"sheet_names\":[").Append(JsonStrList(sheetNames)).Append("],");

            sb.Append("\"scale\":{\"num\":").Append(JsonStr(scale1)).Append(",\"den\":").Append(JsonStr(scale2)).Append("},");

            sb.Append("\"props\":{");

            bool first = true;

            foreach (KeyValuePair<string, string> kv in props)

            {

                if (!first) sb.Append(",");

                first = false;

                sb.Append(JsonStr(kv.Key)).Append(":").Append(JsonStr(kv.Value));

            }

            sb.Append("},");

            sb.Append("\"props_part\":{");

            {

                bool firstP = true;

                foreach (KeyValuePair<string, string> kv in partProps)

                {

                    if (!firstP) sb.Append(",");

                    firstP = false;

                    sb.Append(JsonStr(kv.Key)).Append(":").Append(JsonStr(kv.Value));

                }

            }

            sb.Append("},");

            sb.Append("\"title_block\":{},");

            sb.Append("\"notes\":[").Append(Join(notes)).Append("],");
                sb.Append("\"format_notes\":[").Append(Join(formatNotes)).Append("],");
                try
                {
                    foreach (string fn in formatNotes)
                    {
                        if (fn.Contains("技术要求") || fn.Contains("未注") || fn.Contains("表面处理")
                            || fn.Contains("镀") || fn.Contains("公差"))
                        {
                            warn.Add("图纸格式(模板)里有残留技术要求文本（不参与判定，建议清理模板）："
                                     + (fn.Length > 60 ? fn.Substring(0, 60) + "…" : fn));
                            break;
                        }
                    }
                }
                catch { }

            sb.Append("\"dimensions\":[").Append(Join(dims)).Append("],");

            sb.Append("\"roughness\":[").Append(Join(rough)).Append("],");

            sb.Append("\"gtols\":[").Append(Join(gtols)).Append("],");
            sb.Append("\"datums\":[").Append(Join(datums)).Append("],");
            sb.Append("\"welds\":[").Append(Join(welds)).Append("],");
            sb.Append("\"part_holes\":[").Append(Join(partHoles)).Append("],");

            sb.Append("\"tb_notes\":[").Append(Join(tbNotes)).Append("],");
            sb.Append("\"notes_meta\":[").Append(Join(notesMeta)).Append("],");
            sb.Append("\"sheets\":[").Append(Join(sheetsMeta)).Append("],");
            sb.Append("\"view_boxes\":[").Append(Join(viewBoxes)).Append("],");

            // 2026-10-24：3D 包围盒（mm）与钣金板厚
            if (partBox != null && partBox.Length >= 6)
            {
                sb.Append("\"part_box\":[");
                for (int bi = 0; bi < 6; bi++)
                {
                    if (bi > 0) sb.Append(",");
                    sb.Append(partBox[bi].ToString("0.####", CultureInfo.InvariantCulture));
                }
                sb.Append("],");
            }
            else
            {
                sb.Append("\"part_box\":null,");
            }
            sb.Append("\"is_sheet_metal\":").Append(isSheetMetal ? "true" : "false").Append(",");
            sb.Append("\"thickness_mm\":").Append(
                thicknessMm.ToString("0.####", CultureInfo.InvariantCulture)).Append(",");

            sb.Append("\"material_dbs\":[").Append(JsonStrList(materialDbs)).Append("],");

            sb.Append("\"counts\":{\"views\":").Append(viewCount)

              .Append(",\"notes\":").Append(noteCount)

              .Append(",\"dimensions\":").Append(dimCount)

              .Append(",\"roughness\":").Append(sfCount)

              .Append(",\"gtol\":").Append(gtCount).Append("},");

            sb.Append("\"probe\":[").Append(JsonStrList(probe)).Append("],");

            sb.Append("\"warnings\":[").Append(JsonStrList(warn)).Append("]");

            sb.Append("}");



            summary = "视图 " + viewCount + "，尺寸 " + dimCount + "，注释 " + noteCount +

                      "，粗糙度 " + sfCount + "，几何公差 " + gtCount +

                      "，属性 " + props.Count + " 项";

            return sb.ToString();

        }



        /// <summary>读一个作用域的自定义属性；返回本作用域有效项数。</summary>

        static int ReadProps(IModelDocExtension ext, string scope,

                             Dictionary<string, string> into, bool overwrite)

        {

            int count = 0;

            try

            {

                ICustomPropertyManager cpm = ext.get_CustomPropertyManager(scope) as ICustomPropertyManager;

                if (cpm == null) return 0;

                foreach (object o in Flatten(cpm.GetNames()))

                {

                    string n = Convert.ToString(o);

                    if (string.IsNullOrEmpty(n)) continue;

                    string val = "";

                    try

                    {

                        string v1, v2;

                        cpm.Get4(n, false, out v1, out v2);

                        val = string.IsNullOrEmpty(v2) ? v1 : v2;

                    }

                    catch

                    {

                        try { val = cpm.Get(n); } catch { }

                    }

                    if (string.IsNullOrEmpty(val)) continue;

                    string key = n.Trim();

                    if (!into.ContainsKey(key) || overwrite) into[key] = val.Trim();

                    count++;

                }

            }

            catch { }

            return count;

        }



        static string Short(Exception e)

        {

            string m = e.Message ?? "";

            int i = m.IndexOf("(异常来自", StringComparison.Ordinal);

            return i > 0 ? m.Substring(0, i).Trim() : m;

        }




        // ====================================================================
        // 2026-10-24：3D 模型包围盒 / 钣金板厚
        // ====================================================================

        /// <summary>
        /// 取零件所有实体的并集包围盒，单位 **mm**；取不到返回 null。
        /// SW API 的 IBody2.GetBodyBox() 返回 6 个 double（xmin,ymin,zmin,xmax,ymax,zmax），**单位米**。
        /// 多实体取并集；只取实体（swSolidBody），不含曲面/基准面。
        /// </summary>
        static double[] GetPartBoxMm(IPartDoc part)
        {
            if (part == null) return null;
            double xmin = double.MaxValue, ymin = double.MaxValue, zmin = double.MaxValue;
            double xmax = double.MinValue, ymax = double.MinValue, zmax = double.MinValue;
            int used = 0;
            try
            {
                object bodies = part.GetBodies2((int)swBodyType_e.swSolidBody, true);
                foreach (object o in Flatten(bodies))
                {
                    IBody2 body = o as IBody2;
                    if (body == null) continue;
                    object boxObj = null;
                    try { boxObj = body.GetBodyBox(); } catch { continue; }
                    double[] b = boxObj as double[];
                    if (b == null || b.Length < 6) continue;
                    bool ok = true;
                    for (int i = 0; i < 6; i++) if (double.IsNaN(b[i]) || double.IsInfinity(b[i])) ok = false;
                    if (!ok) continue;
                    if (b[0] < xmin) xmin = b[0];
                    if (b[1] < ymin) ymin = b[1];
                    if (b[2] < zmin) zmin = b[2];
                    if (b[3] > xmax) xmax = b[3];
                    if (b[4] > ymax) ymax = b[4];
                    if (b[5] > zmax) zmax = b[5];
                    used++;
                }
            }
            catch { }
            if (used == 0) return null;
            // 米 → 毫米；顺手校验范围（0.01mm ~ 100m），异常就当作取不到
            double[] mm = new double[] {
                xmin * 1000.0, ymin * 1000.0, zmin * 1000.0,
                xmax * 1000.0, ymax * 1000.0, zmax * 1000.0
            };
            for (int i = 0; i < 6; i++)
            {
                if (double.IsNaN(mm[i]) || Math.Abs(mm[i]) > 100000.0) return null;
            }
            if (mm[3] - mm[0] < 0.01 && mm[4] - mm[1] < 0.01 && mm[5] - mm[2] < 0.01) return null;
            return mm;
        }

        /// <summary>是否含钣金特征（GetTypeName2 含 SheetMetal）</summary>
        static bool HasSheetMetalFeature(IModelDoc2 doc)
        {
            double th;
            if (TryGetSheetMetalThickness(doc, out th)) return true;
            try
            {
                IFeature f = doc.FirstFeature() as IFeature;
                int guard = 0;
                while (f != null && guard++ < 5000)
                {
                    string tn = "";
                    try { tn = f.GetTypeName2() ?? ""; } catch { }
                    if (tn.IndexOf("SheetMetal", StringComparison.OrdinalIgnoreCase) >= 0) return true;
                    try { f = f.GetNextFeature() as IFeature; } catch { f = null; }
                }
            }
            catch { }
            return false;
        }

        /// <summary>
        /// 取钣金板厚（mm）。来源：钣金特征的 ISheetMetalFeatureData.Thickness（米）。
        /// 取不到返回 false —— 绝不从包围盒/尺寸里猜。
        /// </summary>
        static bool TryGetSheetMetalThickness(IModelDoc2 doc, out double mm)
        {
            mm = 0;
            try
            {
                IFeature f = doc.FirstFeature() as IFeature;
                int guard = 0;
                while (f != null && guard++ < 5000)
                {
                    string tn = "";
                    try { tn = f.GetTypeName2() ?? ""; } catch { }
                    if (tn.IndexOf("SheetMetal", StringComparison.OrdinalIgnoreCase) >= 0)
                    {
                        ISheetMetalFeatureData data = null;
                        try { data = f.GetDefinition() as ISheetMetalFeatureData; } catch { }
                        if (data != null)
                        {
                            double t = 0;
                            try { t = data.Thickness; } catch { }
                            if (t > 1e-6 && t < 1.0)
                            {
                                mm = t * 1000.0;
                                return true;
                            }
                        }
                    }
                    try { f = f.GetNextFeature() as IFeature; } catch { f = null; }
                }
            }
            catch { }
            return false;
        }

        static List<object> Flatten(object raw)

        {

            var list = new List<object>();

            if (raw == null) return list;

            IEnumerable en = raw as IEnumerable;

            if (en == null) { list.Add(raw); return list; }

            foreach (object o in en)

            {

                if (o == null) continue;

                if (o is IEnumerable && !(o is string)) list.AddRange(Flatten(o));

                else list.Add(o);

            }

            return list;

        }



        static string Join(IEnumerable<object> items)

        {

            var list = new List<string>();

            if (items != null) foreach (object o in items) list.Add(Convert.ToString(o));

            return string.Join(",", list.ToArray());

        }



        static string JsonStrList(List<string> items)

        {

            var parts = new List<string>();

            foreach (string s in items) parts.Add(JsonStr(s));

            return string.Join(",", parts.ToArray());

        }



        static string JsonStr(string s)

        {

            if (s == null) s = "";

            var sb = new StringBuilder("\"");

            foreach (char c in s)

            {

                switch (c)

                {

                    case '"': sb.Append("\\\""); break;

                    case '\\': sb.Append("\\\\"); break;

                    case '\r': break;

                    case '\n': sb.Append("\\n"); break;

                    case '\t': sb.Append("\\t"); break;

                    default:

                        if (c < 32) sb.Append(' ');

                        else sb.Append(c);

                        break;

                }

            }

            sb.Append("\"");

            return sb.ToString();

        }



        public static string SaveJson(string json, string nameHint)

        {

            string dir = Path.Combine(Path.GetTempPath(), "SwQcAddin");

            try { if (!Directory.Exists(dir)) Directory.CreateDirectory(dir); } catch { }

            string safe = nameHint;

            foreach (char c in Path.GetInvalidFileNameChars()) safe = safe.Replace(c, '_');

            if (string.IsNullOrEmpty(safe)) safe = "drawing";

            string p = Path.Combine(dir, safe + ".sw.json");

            try { File.WriteAllText(p, json, new UTF8Encoding(false)); } catch { }

            return p;

        }

        /// <summary>

        /// 导出用于审图的文件（2026-10-25：**三级兜底，不依赖 SW 的 PDF 导出组件**）

        ///   ① SaveAs(.pdf)：矢量 PDF，最好（有文字层，判定精确）

        ///   ② PrintOut2 到 Windows 自带「Microsoft Print to PDF」→ 仍是 PDF（有文字层）

        ///   ③ SaveBMP：SW 核心 API，任何机器都有 → PNG（无文字层，只能靠 AI 视觉）

        /// 返回 out 类型："pdf" / "png"；失败返回 null。

        /// </summary>

        public static string ExportForAudit(IModelDoc2 doc, string basePath, out string kind, out string err)

        {

            kind = ""; err = "";

            if (doc == null) { err = "没有打开工程图"; return null; }

            try { doc.ViewZoomtofit2(); } catch { }

            // ① 直接另存 PDF

            try

            {

                string e1;

                string pdf = ExportPdf(doc, basePath + ".pdf", out e1);

                if (pdf != null) { kind = "pdf"; return pdf; }

            }

            catch { }

            // ② 打印到 Windows 自带 PDF 打印机

            foreach (string printer in new string[] {

                "Microsoft Print to PDF", "Microsoft PDF 打印机", "Microsoft Print To PDF" })

            {

                try

                {

                    string p2 = basePath + ".pdf";

                    doc.Extension.PrintOut2(null, 1, true, printer, p2);   // 返回 void

                    if (File.Exists(p2) && new FileInfo(p2).Length > 2000)

                    {

                        kind = "pdf";

                        return p2;

                    }

                }

                catch { }

            }

            // ③ SaveBMP 兜底（核心 API，任何装 SW 的机器都有）

            try

            {

                string png = basePath + ".bmp";

                bool ok = doc.SaveBMP(png, 2400, 1700);

                if (ok && File.Exists(png) && new FileInfo(png).Length > 5000)

                {

                    kind = "png";

                    return png;

                }

                err = "SaveBMP 也失败了";

            }

            catch (Exception ex3) { err = "SaveBMP 异常：" + ex3.Message; }

            if (string.IsNullOrEmpty(err)) err = "三种导出方式都失败（PDF 另存/PDF 打印机/位图）";

            return null;

        }

        /// <summary>

        /// 把工程图导出为 PDF（临时文件，审完即删）。

        /// 2026-10-25 用户定稿方向：不做几何 API 判定，改为「导 PDF → 最强 AI 视觉 + 规则」审核。

        /// 用 SaveAs2 + swSaveAsOptions_Copy：只写副本，**不改变当前文档路径/关联**。

        /// </summary>

        public static string ExportPdf(IModelDoc2 doc, string pdfPath, out string err)

        {

            err = "";

            if (doc == null) { err = "没有打开工程图"; return null; }

            try

            {

                int e = 0, w = 0;

                int options = (int)swSaveAsOptions_e.swSaveAsOptions_Silent

                            | (int)swSaveAsOptions_e.swSaveAsOptions_Copy;

                // SW2016 interop 只有 IModelDocExtension.SaveAs(Name, Version, Options, ExportData, ref err, ref warn)

                bool ok = doc.Extension.SaveAs(pdfPath,

                    (int)swSaveAsVersion_e.swSaveAsCurrentVersion,

                    options, null, ref e, ref w);

                if (!ok || !File.Exists(pdfPath))

                {

                    // 兜底：SaveAsSilent(作为副本)

                    try { doc.SaveAsSilent(pdfPath, true); } catch { }

                }

                if (!File.Exists(pdfPath))

                {

                    err = "导出 PDF 失败（errors=" + e + "，warnings=" + w + "）。\n"

                        + "请确认 SolidWorks 装有 PDF 导出组件（另存为里能看到 .pdf 即可）。";

                    return null;

                }

                return pdfPath;

            }

            catch (Exception ex)

            {

                err = "导出 PDF 异常：" + ex.Message;

                return null;

            }

        }

    }

}

