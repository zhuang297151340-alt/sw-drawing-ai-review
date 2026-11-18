using System;
using System.Collections.Generic;
using System.Text.RegularExpressions;
using System.Web.Script.Serialization;

namespace SwQc
{
    /// <summary>
    /// 本次插件窗口的唯一数据源。
    /// SolidWorks 只负责创建它；尺寸表、几何归属表、基准表均为它的投影视图。
    /// </summary>
    public sealed class QcDocument
    {
        sealed class GtolRecord
        {
            public string Id = "", RawId = "", GroupId = "", OriginalGroupId = "", TargetRowId = "";
            public int FrameOrder = 1, FrameCount = 1;
            public string FrameType = "", FrameReason = "", ZoneType = "";
            public string Sym = "", Val = "", Dat1 = "", Dat2 = "", Dat3 = "", Mod = "";
            public string MeasuredFeature = "", Reason = "", Source = "drawing";
            public bool Include;
            public double ValueMm;
        }

        sealed class DatumRecord
        {
            public string Id = "", Letter = "", View = "", FeatureType = "";
            public string SizeRowId = "", SizeText = "", Coord = "", Source = "drawing";
        }

        readonly List<QcDimRow> _rows = new List<QcDimRow>();
        readonly List<GtolRecord> _gtols = new List<GtolRecord>();
        readonly List<DatumRecord> _datums = new List<DatumRecord>();
        int _nextRow = 1, _nextGtol = 1;

        public string DrawingNo { get; private set; }
        public int Version { get; private set; }

        QcDocument() { }

        public static QcDocument FromReport(QcReport report)
        {
            var doc = new QcDocument();
            doc.DrawingNo = report != null ? (report.DwgNo ?? "") : "";
            doc.ImportReport(report);
            doc.NormalizeAndValidate();
            return doc;
        }

        /// <summary>只从确认保存的结构化快照恢复；禁止从三张表显示文字反向拼装。</summary>
        public static QcDocument FromSnapshotJson(string json)
        {
            if (string.IsNullOrWhiteSpace(json)) throw new InvalidOperationException("文档快照为空");
            object raw = new JavaScriptSerializer().DeserializeObject(json);
            var root = raw as Dictionary<string, object>;
            if (root == null) throw new InvalidOperationException("文档快照格式无效");
            int schema = ObjInt(root, "schema_version", 0);
            if (schema != 2) throw new InvalidOperationException("不支持的文档快照版本：" + schema);

            var doc = new QcDocument();
            doc.DrawingNo = ObjStr(root, "dwg_no");
            doc.Version = ObjInt(root, "version", 0);
            foreach (object ro in ObjList(root, "rows"))
            {
                var d = ro as Dictionary<string, object>;
                if (d == null) throw new InvalidOperationException("尺寸记录格式无效");
                var row = new QcDimRow
                {
                    RowId = ObjStr(d, "row_id"), Kind = ObjStr(d, "kind"), Content = ObjStr(d, "content"),
                    TolDisplay = ObjStr(d, "tol"), IT = ObjStr(d, "it"), Capability = ObjStr(d, "capability"),
                    Note = ObjStr(d, "user_note"), Modifier = ObjStr(d, "modifier"),
                    Nominal = ObjDouble(d, "nominal_mm"), TolUp = ObjDouble(d, "tol_up"),
                    TolLow = ObjDouble(d, "tol_low"), HasTol = ObjBool(d, "has_tol"),
                    Band = ObjDouble(d, "band"), IsOutline = ObjBool(d, "is_outline"),
                    IsManual = ObjBool(d, "is_manual"), SigmaT = ObjDouble(d, "sigma_t"),
                    MmsShaft = ObjDouble(d, "mms_shaft"), MmsHole = ObjDouble(d, "mms_hole"),
                    MmvcShaft = ObjDouble(d, "mmvc_shaft"), MmvcHole = ObjDouble(d, "mmvc_hole"),
                    Gtols = new List<QcGtolLine>()
                };
                doc._rows.Add(row);
                doc.TouchRowCounter(row.RowId);
                foreach (object go in ObjList(d, "gtols"))
                {
                    var gd = go as Dictionary<string, object>;
                    if (gd == null) throw new InvalidOperationException("几何公差记录格式无效");
                    doc.AddSnapshotGtol(gd, row.RowId);
                }
            }
            foreach (object go in ObjList(root, "unlinked_gtols"))
            {
                var gd = go as Dictionary<string, object>;
                if (gd == null) throw new InvalidOperationException("未归属几何公差格式无效");
                doc.AddSnapshotGtol(gd, "");
            }
            foreach (object o in ObjList(root, "datums"))
            {
                var d = o as Dictionary<string, object>;
                if (d == null) throw new InvalidOperationException("基准记录格式无效");
                doc._datums.Add(new DatumRecord
                {
                    Id = ObjStr(d, "id"), Letter = ObjStr(d, "letter"), View = ObjStr(d, "view"),
                    FeatureType = ObjStr(d, "feature_type"), SizeRowId = ObjStr(d, "size_row_id"),
                    SizeText = ObjStr(d, "size_text"), Coord = ObjStr(d, "coord"), Source = ObjStr(d, "source")
                });
            }
            doc.NormalizeAndValidate();
            string error;
            if (!doc.ValidateForPersistence(out error)) throw new InvalidOperationException(error);
            return doc;
        }

        void AddSnapshotGtol(Dictionary<string, object> d, string targetRowId)
        {
            var g = new GtolRecord
            {
                Id = ObjStr(d, "gtol_id"), RawId = ObjStr(d, "raw_id"), GroupId = ObjStr(d, "group_id"),
                OriginalGroupId = ObjStr(d, "group_id"), FrameOrder = ObjInt(d, "frame_order", 1),
                FrameCount = ObjInt(d, "frame_count", 1), FrameType = ObjStr(d, "frame_type"),
                FrameReason = ObjStr(d, "frame_reason"), ZoneType = ObjStr(d, "zone_type"),
                TargetRowId = targetRowId ?? "", Sym = ObjStr(d, "sym"), Val = ObjStr(d, "val"),
                Dat1 = ObjStr(d, "dat1"), Dat2 = ObjStr(d, "dat2"), Dat3 = ObjStr(d, "dat3"), Mod = ObjStr(d, "mod"),
                MeasuredFeature = ObjStr(d, "measured_feature"), Include = ObjBool(d, "include"),
                Reason = ObjStr(d, "reason"), ValueMm = ObjDouble(d, "value_mm"), Source = ObjStr(d, "source")
            };
            if (string.IsNullOrEmpty(g.Source)) g.Source = "drawing";
            _gtols.Add(g); TouchGtolCounter(g.Id);
        }

        static string ObjStr(Dictionary<string, object> d, string key)
        { object o; return d != null && d.TryGetValue(key, out o) && o != null ? Convert.ToString(o) ?? "" : ""; }
        static int ObjInt(Dictionary<string, object> d, string key, int fallback)
        { int n; return int.TryParse(ObjStr(d, key), out n) ? n : fallback; }
        static double ObjDouble(Dictionary<string, object> d, string key)
        { double n; return double.TryParse(ObjStr(d, key), System.Globalization.NumberStyles.Float,
            System.Globalization.CultureInfo.InvariantCulture, out n) ? n : 0.0; }
        static bool ObjBool(Dictionary<string, object> d, string key)
        { bool b; string s = ObjStr(d, key); return bool.TryParse(s, out b) ? b : s == "1"; }
        static object[] ObjList(Dictionary<string, object> d, string key)
        {
            object o; if (d == null || !d.TryGetValue(key, out o) || o == null) return new object[0];
            var a = o as object[]; if (a != null) return a;
            var list = o as System.Collections.IList;
            if (list == null) return new object[0];
            var outList = new List<object>(); foreach (object x in list) outList.Add(x);
            return outList.ToArray();
        }

        public void CommitDimensionView(IList<QcDimRow> viewRows)
        {
            Execute(delegate
            {
                var oldRows = new Dictionary<string, QcDimRow>(StringComparer.OrdinalIgnoreCase);
                foreach (QcDimRow r in _rows)
                    if (r != null && !string.IsNullOrEmpty(r.RowId)) oldRows[r.RowId] = r;

                var keptRows = new List<QcDimRow>();
                var keptIds = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
                var seenGtols = new HashSet<string>(StringComparer.OrdinalIgnoreCase);

                foreach (QcDimRow input in viewRows ?? new List<QcDimRow>())
                {
                    if (input == null) continue;
                    string rowId = input.RowId;
                    if (string.IsNullOrEmpty(rowId) || keptIds.Contains(rowId)) rowId = NewRowId();
                    input.RowId = rowId;
                    keptIds.Add(rowId);

                    QcDimRow row;
                    if (!oldRows.TryGetValue(rowId, out row)) row = CloneRow(input, false);
                    CopyEditableRow(input, row);
                    row.RowId = rowId;
                    row.Gtols = new List<QcGtolLine>();
                    keptRows.Add(row);

                    var oldForRow = new List<GtolRecord>();
                    foreach (GtolRecord g in _gtols)
                        if (string.Equals(g.TargetRowId, rowId, StringComparison.OrdinalIgnoreCase)) oldForRow.Add(g);

                    foreach (QcGtolLine line in input.Gtols ?? new List<QcGtolLine>())
                    {
                        if (line == null) continue;
                        GtolRecord rec = FindGtol(line.GtolId);
                        if (rec == null && !string.IsNullOrEmpty(line.RawId))
                        {
                            foreach (GtolRecord x in oldForRow)
                                if (!seenGtols.Contains(x.Id) && x.RawId == line.RawId) { rec = x; break; }
                        }
                        if (rec == null)
                        {
                            foreach (GtolRecord x in oldForRow)
                                if (!seenGtols.Contains(x.Id) && SameGtol(x, line)) { rec = x; break; }
                        }
                        if (rec == null)
                        {
                            rec = new GtolRecord { Id = NewGtolId(), Source = "manual" };
                            _gtols.Add(rec);
                        }
                        CopyGtol(line, rec);
                        rec.TargetRowId = rowId;
                        line.GtolId = rec.Id;
                        seenGtols.Add(rec.Id);
                    }
                }

                // 删除整个尺寸行：几何公差转为未挂；删除某行内的一条公差：该记录真正删除。
                var removeGtols = new List<GtolRecord>();
                foreach (GtolRecord g in _gtols)
                {
                    if (string.IsNullOrEmpty(g.TargetRowId)) continue;
                    if (!keptIds.Contains(g.TargetRowId)) g.TargetRowId = "";
                    else if (!seenGtols.Contains(g.Id)) removeGtols.Add(g);
                }
                foreach (GtolRecord g in removeGtols) _gtols.Remove(g);
                _rows.Clear();
                _rows.AddRange(keptRows);
            });
        }

        public void ApplyAssignments(QcReport report, IList<QcUnlinkedGtol> items,
                                     IList<int> assignment, IList<string> custom)
        {
            Execute(delegate
            {
                int n = Math.Min(items != null ? items.Count : 0, assignment != null ? assignment.Count : 0);
                for (int i = 0; i < n; i++)
                {
                    QcUnlinkedGtol item = items[i];
                    if (item == null) continue;
                    string targetId = "";
                    int di = assignment[i];
                    if (di >= 0 && report != null && report.DimRows != null && di < report.DimRows.Count)
                    {
                        targetId = report.DimRows[di].RowId;
                    }
                    else
                    {
                        string text = (custom != null && i < custom.Count) ? (custom[i] ?? "").Trim() : "";
                        if (!string.IsNullOrEmpty(text)) targetId = FindOrCreateCustomRow(text).RowId;
                    }

                    var ids = new List<string>();
                    if (item.Members != null && item.Members.Count > 0)
                    {
                        foreach (QcGtolLine m in item.Members)
                        {
                            GtolRecord g = FindGtolLine(m);
                            if (g == null) continue;
                            // Affiliation edits only target + measured feature. Do not let
                            // dialog projections overwrite group identity or calculation flags.
                            g.TargetRowId = targetId;
                            g.MeasuredFeature = m.MeasuredFeature ?? "";
                            g.Source = "manual";
                            ids.Add(g.Id);
                        }
                    }
                    if (ids.Count == 0)
                    {
                        GtolRecord g = FindGtolItem(item);
                        if (g == null) continue;
                        g.TargetRowId = targetId;
                        g.MeasuredFeature = item.MeasuredFeature ?? "";
                        g.Source = "manual";
                    }
                }
            });
        }

        public void ApplyDatumRows(IList<DatumRow> rows)
        {
            Execute(delegate
            {
                foreach (DatumRow input in rows ?? new List<DatumRow>())
                {
                    if (input == null || string.IsNullOrEmpty(input.Letter)) continue;
                    DatumRecord d = null;
                    foreach (DatumRecord x in _datums)
                        if (string.Equals(x.Letter, input.Letter, StringComparison.OrdinalIgnoreCase)) { d = x; break; }
                    if (d == null)
                    {
                        d = new DatumRecord { Id = "DAT-" + input.Letter.ToUpperInvariant(), Letter = input.Letter };
                        _datums.Add(d);
                    }
                    d.View = input.View ?? "";
                    d.Coord = input.Coord ?? "";
                    d.FeatureType = string.IsNullOrEmpty(input.Manual) ? (input.Auto ?? "") : input.Manual;
                    d.SizeText = input.Near ?? "";
                    d.SizeRowId = FindRowIdByContent(d.SizeText);
                    d.Source = "manual";
                }
            });
        }

        public List<QcUnlinkedGtol> BuildAllGtolItems()
        {
            var list = new List<QcUnlinkedGtol>();
            var grouped = new Dictionary<string, List<GtolRecord>>();
            foreach (GtolRecord g in _gtols)
            {
                string key = string.IsNullOrEmpty(g.GroupId) ? g.Id : ("GROUP:" + g.GroupId);
                List<GtolRecord> bucket;
                if (!grouped.TryGetValue(key, out bucket)) { bucket = new List<GtolRecord>(); grouped[key] = bucket; }
                bucket.Add(g);
            }
            foreach (List<GtolRecord> bucket in grouped.Values)
            {
                if (bucket.Count == 0) continue;
                GtolRecord first = bucket[0];
                var item = ToUnlinked(first);
                item.Target = RowContent(first.TargetRowId);
                foreach (GtolRecord g in bucket) item.Members.Add(ToLine(g));
                list.Add(item);
            }
            return list;
        }

        public void ApplyToReport(QcReport report)
        {
            if (report == null) return;
            var map = new Dictionary<string, QcDimRow>(StringComparer.OrdinalIgnoreCase);
            var rows = new List<QcDimRow>();
            foreach (QcDimRow source in _rows)
            {
                QcDimRow row = CloneRow(source, false);
                row.Gtols = new List<QcGtolLine>();
                row.Note = ComposeNote(source);
                rows.Add(row);
                map[row.RowId] = row;
            }
            var unlinked = new List<QcUnlinkedGtol>();
            var unlinkedGroups = new Dictionary<string, List<GtolRecord>>(StringComparer.OrdinalIgnoreCase);
            foreach (GtolRecord g in _gtols)
            {
                QcDimRow target;
                if (!string.IsNullOrEmpty(g.TargetRowId) && map.TryGetValue(g.TargetRowId, out target))
                    target.Gtols.Add(ToLine(g));
                else
                {
                    string key = string.IsNullOrEmpty(g.GroupId) ? g.Id : ("GROUP:" + g.GroupId);
                    List<GtolRecord> bucket;
                    if (!unlinkedGroups.TryGetValue(key, out bucket))
                    { bucket = new List<GtolRecord>(); unlinkedGroups[key] = bucket; }
                    bucket.Add(g);
                }
            }
            foreach (List<GtolRecord> bucket in unlinkedGroups.Values)
            {
                if (bucket.Count == 0) continue;
                bucket.Sort(delegate(GtolRecord a, GtolRecord b) { return a.FrameOrder.CompareTo(b.FrameOrder); });
                QcUnlinkedGtol item = ToUnlinked(bucket[0]);
                foreach (GtolRecord g in bucket) item.Members.Add(ToLine(g));
                unlinked.Add(item);
            }
            foreach (QcDimRow row in rows) QcRunner.NormalizeRowGtols(row);
            report.DimRows = rows;
            report.UnlinkedGtols = unlinked;
            report.Datums = BuildDatumView();
        }

        public string ToSnapshotJson()
        {
            var root = new Dictionary<string, object>();
            root["schema_version"] = 2;
            root["version"] = Version;
            root["dwg_no"] = DrawingNo ?? "";
            var rows = new List<object>();
            foreach (QcDimRow row in _rows)
            {
                var d = new Dictionary<string, object>();
                d["row_id"] = row.RowId; d["kind"] = row.Kind; d["content"] = row.Content;
                d["tol"] = row.TolDisplay; d["it"] = row.IT; d["capability"] = row.Capability;
                d["note"] = ComposeNote(row); d["user_note"] = StripDatumNote(row.Note);
                d["modifier"] = row.Modifier; d["has_tol"] = row.HasTol;
                d["band"] = row.Band; d["is_outline"] = row.IsOutline; d["is_manual"] = row.IsManual;
                d["nominal_mm"] = row.Nominal; d["tol_up"] = row.TolUp; d["tol_low"] = row.TolLow;
                d["sigma_t"] = row.SigmaT; d["mms_shaft"] = row.MmsShaft; d["mms_hole"] = row.MmsHole;
                d["mmvc_shaft"] = row.MmvcShaft; d["mmvc_hole"] = row.MmvcHole;
                var gs = new List<object>();
                foreach (GtolRecord g in _gtols)
                {
                    if (g.TargetRowId != row.RowId) continue;
                    gs.Add(GtolDict(g));
                }
                d["gtols"] = gs;
                rows.Add(d);
            }
            root["rows"] = rows;
            var un = new List<object>();
            foreach (GtolRecord g in _gtols) if (string.IsNullOrEmpty(g.TargetRowId)) un.Add(GtolDict(g));
            root["unlinked_gtols"] = un;
            var datums = new List<object>();
            foreach (DatumRecord x in _datums)
            {
                var d = new Dictionary<string, object>();
                d["id"] = x.Id; d["letter"] = x.Letter; d["view"] = x.View;
                d["feature_type"] = x.FeatureType; d["size_row_id"] = x.SizeRowId;
                d["size_text"] = !string.IsNullOrEmpty(x.SizeRowId) ? RowContent(x.SizeRowId) : x.SizeText;
                d["coord"] = x.Coord; d["source"] = x.Source;
                datums.Add(d);
            }
            root["datums"] = datums;
            return new JavaScriptSerializer().Serialize(root);
        }

        /// <summary>
        /// 保存、送 AI 和回读前共同使用的硬校验。失败时调用方必须停止，不允许静默修复后继续输出。
        /// </summary>
        public bool ValidateForPersistence(out string error)
        {
            error = "";
            try
            {
                NormalizeAndValidate();
                var rowIds = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
                foreach (QcDimRow r in _rows)
                {
                    if (string.IsNullOrWhiteSpace(r.RowId)) throw new InvalidOperationException("存在没有 RowId 的尺寸行");
                    if (string.IsNullOrWhiteSpace(r.Content)) throw new InvalidOperationException("尺寸 " + r.RowId + " 内容为空");
                    rowIds.Add(r.RowId);
                }
                var groupOrders = new Dictionary<string, HashSet<int>>(StringComparer.OrdinalIgnoreCase);
                var groupCounts = new Dictionary<string, int>(StringComparer.OrdinalIgnoreCase);
                var groupSymbolCounts = new Dictionary<string, int>(StringComparer.OrdinalIgnoreCase);
                var groupHasFirstSymbol = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
                foreach (GtolRecord x in _gtols)
                {
                    if (string.IsNullOrEmpty(x.GroupId)) continue;
                    if (!groupSymbolCounts.ContainsKey(x.GroupId)) groupSymbolCounts[x.GroupId] = 0;
                    if (!string.IsNullOrWhiteSpace(x.Sym))
                    {
                        groupSymbolCounts[x.GroupId] = groupSymbolCounts[x.GroupId] + 1;
                        if (x.FrameOrder == 1) groupHasFirstSymbol.Add(x.GroupId);
                    }
                }
                foreach (GtolRecord g in _gtols)
                {
                    if (string.IsNullOrWhiteSpace(g.Id)) throw new InvalidOperationException("存在没有 GtolId 的几何公差");
                    if (string.IsNullOrWhiteSpace(g.Val))
                        throw new InvalidOperationException("几何公差 " + g.Id + " 的数值为空");
                    if (string.IsNullOrWhiteSpace(g.Sym))
                    {
                        int symbolCount = 0;
                        if (!string.IsNullOrEmpty(g.GroupId)) groupSymbolCounts.TryGetValue(g.GroupId, out symbolCount);
                        bool validCompositeLower = !string.IsNullOrEmpty(g.GroupId) && g.FrameOrder > 1 &&
                            symbolCount == 1 && groupHasFirstSymbol.Contains(g.GroupId);
                        if (!validCompositeLower)
                            throw new InvalidOperationException("几何公差 " + g.Id + " 的符号为空，且不符合复合公差下层继承上层符号的结构");
                    }
                    if (!string.IsNullOrEmpty(g.TargetRowId) && !rowIds.Contains(g.TargetRowId))
                        throw new InvalidOperationException("几何公差 " + g.Id + " 指向不存在的尺寸");
                    if (!string.IsNullOrEmpty(g.GroupId))
                    {
                        HashSet<int> orders;
                        if (!groupOrders.TryGetValue(g.GroupId, out orders))
                        { orders = new HashSet<int>(); groupOrders[g.GroupId] = orders; groupCounts[g.GroupId] = 0; }
                        if (g.FrameOrder <= 0 || !orders.Add(g.FrameOrder))
                            throw new InvalidOperationException("公差框组 " + g.GroupId + " 的框内顺序重复或无效");
                        groupCounts[g.GroupId] = groupCounts[g.GroupId] + 1;
                    }
                }
                foreach (var kv in groupOrders)
                    for (int n = 1; n <= groupCounts[kv.Key]; n++)
                        if (!kv.Value.Contains(n)) throw new InvalidOperationException("公差框组 " + kv.Key + " 的框内顺序不连续");
                var letters = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
                foreach (DatumRecord d in _datums)
                {
                    if (string.IsNullOrWhiteSpace(d.Letter)) throw new InvalidOperationException("存在没有字母的基准");
                    if (!letters.Add(d.Letter)) throw new InvalidOperationException("基准字母重复：" + d.Letter);
                    if (!string.IsNullOrEmpty(d.SizeRowId) && !rowIds.Contains(d.SizeRowId))
                        throw new InvalidOperationException("基准 " + d.Letter + " 指向不存在的尺寸");
                }
                return true;
            }
            catch (Exception ex) { error = ex.Message; return false; }
        }

        void ImportReport(QcReport report)
        {
            if (report == null) return;
            foreach (QcDimRow source in report.DimRows ?? new List<QcDimRow>())
            {
                if (source == null) continue;
                QcDimRow row = CloneRow(source, false);
                row.RowId = string.IsNullOrEmpty(source.RowId) ? NewRowId() : source.RowId;
                TouchRowCounter(row.RowId);
                row.Note = StripDatumNote(row.Note);
                row.Gtols = new List<QcGtolLine>();
                _rows.Add(row);
                foreach (QcGtolLine line in source.Gtols ?? new List<QcGtolLine>())
                    AddImportedGtol(line, row.RowId);
            }
            foreach (QcUnlinkedGtol item in report.UnlinkedGtols ?? new List<QcUnlinkedGtol>())
            {
                if (item == null) continue;
                if (item.Members != null && item.Members.Count > 0)
                {
                    foreach (QcGtolLine line in item.Members) AddImportedGtol(line, "");
                }
                else
                {
                    var line = new QcGtolLine
                    {
                        GtolId = item.GtolId, RawId = item.RawId, GroupId = item.GroupId,
                        FrameOrder = item.FrameOrder, Sym = item.Sym, Val = item.Val,
                        Dat1 = item.Dat1, Dat2 = item.Dat2, Dat3 = item.Dat3, Mod = item.Mod,
                        MeasuredFeature = item.MeasuredFeature
                    };
                    AddImportedGtol(line, "");
                }
            }
            foreach (QcDatum q in report.Datums ?? new List<QcDatum>())
            {
                if (q == null || string.IsNullOrEmpty(q.Letter)) continue;
                string size = string.IsNullOrEmpty(q.NearManual) ? (q.Near ?? "") : q.NearManual;
                _datums.Add(new DatumRecord
                {
                    Id = "DAT-" + q.Letter.ToUpperInvariant(), Letter = q.Letter, View = q.View,
                    FeatureType = string.IsNullOrEmpty(q.Manual) ? q.Target : q.Manual,
                    SizeText = size, SizeRowId = FindRowIdByContent(size), Coord = q.Coord,
                    Source = string.IsNullOrEmpty(q.Manual) ? "drawing" : "manual"
                });
            }
        }

        void Execute(Action change)
        {
            QcDocument backup = Clone();
            try
            {
                change();
                NormalizeAndValidate();
                Version++;
            }
            catch
            {
                Restore(backup);
                throw;
            }
        }

        void NormalizeAndValidate()
        {
            var rowIds = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
            foreach (QcDimRow r in _rows)
            {
                if (r == null) throw new InvalidOperationException("尺寸表存在空记录");
                if (string.IsNullOrEmpty(r.RowId)) r.RowId = NewRowId();
                if (!rowIds.Add(r.RowId)) throw new InvalidOperationException("尺寸编号重复：" + r.RowId);
                r.Content = (r.Content ?? "").Trim();
                r.Note = StripDatumNote(r.Note);
            }
            var gtolIds = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
            var groupTargets = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
            foreach (GtolRecord g in _gtols)
            {
                if (string.IsNullOrEmpty(g.Id)) g.Id = NewGtolId();
                if (!gtolIds.Add(g.Id)) throw new InvalidOperationException("几何公差编号重复：" + g.Id);
                g.Sym = (g.Sym ?? "").Trim(); g.Val = (g.Val ?? "").Trim(); g.Mod = (g.Mod ?? "").Trim();
                g.Dat1 = (g.Dat1 ?? "").Trim().ToUpperInvariant(); g.Dat2 = (g.Dat2 ?? "").Trim().ToUpperInvariant();
                g.Dat3 = (g.Dat3 ?? "").Trim().ToUpperInvariant();
                // 同一物理公差标注可能是独立多框、组合多单段或复合框；编辑归属时均不得拆组。
                if (!string.IsNullOrEmpty(g.OriginalGroupId)) g.GroupId = g.OriginalGroupId;
                else if (!string.IsNullOrEmpty(g.GroupId)) g.OriginalGroupId = g.GroupId;
                if (!string.IsNullOrEmpty(g.TargetRowId) && !rowIds.Contains(g.TargetRowId))
                    throw new InvalidOperationException("几何公差 " + g.Id + " 指向不存在的尺寸 " + g.TargetRowId);
                if (!string.IsNullOrEmpty(g.GroupId))
                {
                    string old;
                    if (groupTargets.TryGetValue(g.GroupId, out old) && old != g.TargetRowId)
                        throw new InvalidOperationException("公差框组 " + g.GroupId + " 的成员指向了不同尺寸");
                    groupTargets[g.GroupId] = g.TargetRowId;
                }
            }
            // FrameCount 是组属性，必须由当前主数据重算；不能沿用旧备注里的“框数=1”。
            var frameGroups = new Dictionary<string, List<GtolRecord>>(StringComparer.OrdinalIgnoreCase);
            foreach (GtolRecord g in _gtols)
            {
                string key = string.IsNullOrEmpty(g.GroupId) ? ("ONE:" + g.Id) : ("GROUP:" + g.GroupId);
                List<GtolRecord> bucket;
                if (!frameGroups.TryGetValue(key, out bucket)) { bucket = new List<GtolRecord>(); frameGroups[key] = bucket; }
                bucket.Add(g);
            }
            foreach (List<GtolRecord> bucket in frameGroups.Values)
                foreach (GtolRecord g in bucket) g.FrameCount = bucket.Count;
        }

        string ComposeNote(QcDimRow row)
        {
            string user = StripDatumNote(row != null ? row.Note : "");
            var parts = new List<string>();
            if (!string.IsNullOrEmpty(user)) parts.Add(user);
            // 框格类别属于几何公差组。尺寸行备注只是该组的简短投影，
            // 禁止保留“公差框类型：...”或判定理由/框数等冗长旧文本。
            var grouped = new Dictionary<string, List<GtolRecord>>(StringComparer.OrdinalIgnoreCase);
            foreach (GtolRecord g in _gtols)
            {
                if (row == null || g.TargetRowId != row.RowId) continue;
                string key = string.IsNullOrEmpty(g.GroupId) ? ("ONE:" + g.Id) : ("GROUP:" + g.GroupId);
                List<GtolRecord> bucket;
                if (!grouped.TryGetValue(key, out bucket)) { bucket = new List<GtolRecord>(); grouped[key] = bucket; }
                bucket.Add(g);
            }
            var frameLabels = new List<string>();
            var zoneLabels = new List<string>();
            foreach (List<GtolRecord> bucket in grouped.Values)
            {
                string type = "";
                foreach (GtolRecord g in bucket) if (!string.IsNullOrEmpty(g.FrameType)) { type = g.FrameType; break; }
                if (string.IsNullOrEmpty(type))
                {
                    int symbolCount = 0;
                    var symbols = new HashSet<string>();
                    foreach (GtolRecord g in bucket)
                        if (!string.IsNullOrEmpty(g.Sym)) { symbolCount++; symbols.Add(g.Sym); }
                    if (bucket.Count <= 1) type = "独立";
                    else if (symbolCount == 1 && !string.IsNullOrEmpty(bucket[0].Sym)) type = "复合";
                    else if (symbolCount == bucket.Count && symbols.Count == 1) type = "组合";
                    else if (symbolCount == bucket.Count) type = "独立";
                    else type = "待确认";
                }
                string label = type == "独立" ? "独立公差" :
                               type == "组合" ? "组合公差" :
                               type == "复合" ? "复合公差" : "公差框待确认";
                if (!frameLabels.Contains(label)) frameLabels.Add(label);
                foreach (GtolRecord g in bucket)
                {
                    string zone = (g.ZoneType ?? "").ToUpperInvariant();
                    string zl = zone == "CZ" ? "CZ组合公差带" : zone == "SZ" ? "SZ独立公差带" :
                                zone == "冲突" ? "CZ/SZ公差带冲突" : "";
                    if (!string.IsNullOrEmpty(zl) && !zoneLabels.Contains(zl)) zoneLabels.Add(zl);
                }
            }
            parts.AddRange(frameLabels);
            parts.AddRange(zoneLabels);
            var letters = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
            foreach (GtolRecord g in _gtols)
            {
                if (row == null || g.TargetRowId != row.RowId) continue;
                if (!string.IsNullOrEmpty(g.Dat1)) letters.Add(g.Dat1);
                if (!string.IsNullOrEmpty(g.Dat2)) letters.Add(g.Dat2);
                if (!string.IsNullOrEmpty(g.Dat3)) letters.Add(g.Dat3);
            }
            foreach (string letter in letters)
            {
                DatumRecord d = null;
                foreach (DatumRecord x in _datums)
                    if (string.Equals(x.Letter, letter, StringComparison.OrdinalIgnoreCase)) { d = x; break; }
                string text = "基准 " + letter + " → " + ((d == null || string.IsNullOrEmpty(d.FeatureType)) ? "未指定要素" : d.FeatureType);
                string size = d == null ? "" : (!string.IsNullOrEmpty(d.SizeRowId) ? RowContent(d.SizeRowId) : d.SizeText);
                if (!string.IsNullOrEmpty(size)) text += "，尺寸 " + size;
                parts.Add(text);
            }
            return string.Join("；", parts.ToArray());
        }

        static string StripDatumNote(string note)
        {
            var keep = new List<string>();
            foreach (string raw in (note ?? "").Split(new char[] { '；' }, StringSplitOptions.RemoveEmptyEntries))
            {
                string x = raw.Trim();
                if (Regex.IsMatch(x, @"^基准\s*[A-Za-z]+\s*(?:→|->)")) continue;
                if (x.StartsWith("公差框类型：") || x.StartsWith("公差带修饰：") || x.StartsWith("公差带：")) continue;
                if (x == "独立公差" || x == "组合公差" || x == "复合公差" || x == "公差框待确认" ||
                    x == "CZ组合公差带" || x == "SZ独立公差带" || x == "CZ/SZ公差带冲突") continue;
                if (!string.IsNullOrEmpty(x)) keep.Add(x);
            }
            return string.Join("；", keep.ToArray());
        }

        void AddImportedGtol(QcGtolLine line, string targetRowId)
        {
            if (line == null) return;
            // The report can expose the same record through a dimension row and
            // the affiliation projection.  Stable IDs mean it is one entity,
            // never two copied tolerances.
            GtolRecord existing = FindGtolLine(line);
            if (existing != null)
            {
                CopyGtol(line, existing);
                if (!string.IsNullOrEmpty(targetRowId)) existing.TargetRowId = targetRowId;
                return;
            }
            var g = new GtolRecord();
            g.Id = UniqueImportedGtolId(!string.IsNullOrEmpty(line.GtolId) ? line.GtolId : line.RawId);
            CopyGtol(line, g);
            g.TargetRowId = targetRowId ?? "";
            g.Source = line.Manual ? "manual" : "drawing";
            _gtols.Add(g);
        }

        string UniqueImportedGtolId(string preferred)
        {
            string id = (preferred ?? "").Trim();
            if (string.IsNullOrEmpty(id)) id = NewGtolId();
            string baseId = id; int n = 2;
            while (FindGtol(id) != null) id = baseId + "-" + (n++).ToString();
            TouchGtolCounter(id);
            return id;
        }

        GtolRecord FindGtolLine(QcGtolLine line)
        {
            if (line == null) return null;
            GtolRecord g = FindGtol(line.GtolId);
            if (g != null) return g;
            if (!string.IsNullOrEmpty(line.RawId))
                foreach (GtolRecord x in _gtols) if (x.RawId == line.RawId) return x;
            return null;
        }

        GtolRecord FindGtolItem(QcUnlinkedGtol item)
        {
            if (item == null) return null;
            GtolRecord g = FindGtol(item.GtolId);
            if (g != null) return g;
            if (!string.IsNullOrEmpty(item.RawId))
                foreach (GtolRecord x in _gtols) if (x.RawId == item.RawId) return x;
            return null;
        }

        GtolRecord FindGtol(string id)
        {
            if (string.IsNullOrEmpty(id)) return null;
            foreach (GtolRecord g in _gtols)
                if (string.Equals(g.Id, id, StringComparison.OrdinalIgnoreCase)) return g;
            return null;
        }

        QcDimRow FindOrCreateCustomRow(string content)
        {
            foreach (QcDimRow r in _rows)
                if (string.Equals(r.Content, content, StringComparison.OrdinalIgnoreCase)) return r;
            var row = new QcDimRow
            {
                RowId = NewRowId(), Kind = "自定义", Content = content,
                HasTol = true, IsManual = true, Note = "人工填写尺寸（用户指定归属）"
            };
            _rows.Insert(0, row);
            return row;
        }

        string FindRowIdByContent(string content)
        {
            if (string.IsNullOrEmpty(content)) return "";
            foreach (QcDimRow r in _rows)
                if (string.Equals((r.Content ?? "").Trim(), content.Trim(), StringComparison.OrdinalIgnoreCase)) return r.RowId;
            return "";
        }

        string RowContent(string rowId)
        {
            foreach (QcDimRow r in _rows) if (r.RowId == rowId) return r.Content ?? "";
            return "";
        }

        static bool SameGtol(GtolRecord g, QcGtolLine l)
        {
            return g.Sym == (l.Sym ?? "").Trim() && g.Val == (l.Val ?? "").Trim()
                && g.Dat1 == (l.Dat1 ?? "").Trim().ToUpperInvariant()
                && g.Dat2 == (l.Dat2 ?? "").Trim().ToUpperInvariant()
                && g.Dat3 == (l.Dat3 ?? "").Trim().ToUpperInvariant()
                && g.Mod == (l.Mod ?? "").Trim();
        }

        static void CopyGtol(QcGtolLine line, GtolRecord g)
        {
            g.RawId = line.RawId ?? g.RawId;
            if (string.IsNullOrEmpty(g.OriginalGroupId) && !string.IsNullOrEmpty(line.GroupId))
                g.OriginalGroupId = line.GroupId;
            if (!string.IsNullOrEmpty(g.OriginalGroupId)) g.GroupId = g.OriginalGroupId;
            else if (!string.IsNullOrEmpty(line.GroupId)) g.GroupId = line.GroupId;
            if (line.FrameOrder > 0) g.FrameOrder = line.FrameOrder;
            if (line.FrameCount > 0) g.FrameCount = line.FrameCount;
            g.FrameType = line.FrameType ?? "";
            g.FrameReason = line.FrameReason ?? "";
            g.ZoneType = line.ZoneType ?? "";
            g.Sym = line.Sym ?? ""; g.Val = line.Val ?? ""; g.Dat1 = line.Dat1 ?? ""; g.Dat2 = line.Dat2 ?? "";
            g.Dat3 = line.Dat3 ?? "";
            g.Mod = line.Mod ?? "";
            if (!string.IsNullOrEmpty(line.MeasuredFeature)) g.MeasuredFeature = line.MeasuredFeature;
            g.Include = line.Include; g.Reason = line.Reason ?? ""; g.ValueMm = line.ValueMm;
        }

        static void CopyEditableRow(QcDimRow from, QcDimRow to)
        {
            to.Kind = from.Kind ?? ""; to.Content = from.Content ?? ""; to.TolDisplay = from.TolDisplay ?? "";
            to.IT = from.IT ?? ""; to.Capability = from.Capability ?? ""; to.Note = StripDatumNote(from.Note);
            to.Modifier = from.Modifier ?? ""; to.Nominal = from.Nominal; to.TolUp = from.TolUp; to.TolLow = from.TolLow;
            to.HasTol = from.HasTol; to.Band = from.Band; to.IsOutline = from.IsOutline; to.IsManual = from.IsManual;
        }

        static QcDimRow CloneRow(QcDimRow r, bool withGtols)
        {
            var x = new QcDimRow();
            x.RowId = r.RowId; CopyEditableRow(r, x);
            x.SigmaT = r.SigmaT; x.MmsShaft = r.MmsShaft; x.MmsHole = r.MmsHole;
            x.MmvcShaft = r.MmvcShaft; x.MmvcHole = r.MmvcHole;
            if (withGtols) foreach (QcGtolLine g in r.Gtols ?? new List<QcGtolLine>()) x.Gtols.Add(CloneLine(g));
            return x;
        }

        static QcGtolLine CloneLine(QcGtolLine g)
        {
            return new QcGtolLine
            {
                GtolId = g.GtolId, RawId = g.RawId, GroupId = g.GroupId, FrameOrder = g.FrameOrder,
                FrameCount = g.FrameCount, FrameType = g.FrameType, FrameReason = g.FrameReason, ZoneType = g.ZoneType,
                Sym = g.Sym, Val = g.Val, Dat1 = g.Dat1, Dat2 = g.Dat2, Dat3 = g.Dat3, Mod = g.Mod,
                MeasuredFeature = g.MeasuredFeature, Include = g.Include, Reason = g.Reason,
                ValueMm = g.ValueMm, Manual = g.Manual
            };
        }

        QcGtolLine ToLine(GtolRecord g)
        {
            return new QcGtolLine
            {
                GtolId = g.Id, RawId = g.RawId, GroupId = g.GroupId, FrameOrder = g.FrameOrder,
                FrameCount = g.FrameCount, FrameType = g.FrameType, FrameReason = g.FrameReason, ZoneType = g.ZoneType,
                Sym = g.Sym, Val = g.Val, Dat1 = g.Dat1, Dat2 = g.Dat2, Dat3 = g.Dat3, Mod = g.Mod,
                MeasuredFeature = g.MeasuredFeature, Include = g.Include, Reason = g.Reason,
                ValueMm = g.ValueMm, Manual = g.Source == "manual"
            };
        }

        QcUnlinkedGtol ToUnlinked(GtolRecord g)
        {
            return new QcUnlinkedGtol
            {
                GtolId = g.Id, RawId = g.RawId, GroupId = g.GroupId, FrameOrder = g.FrameOrder,
                FrameCount = g.FrameCount, FrameType = g.FrameType, FrameReason = g.FrameReason, ZoneType = g.ZoneType,
                Sym = g.Sym, Val = g.Val, Dat1 = g.Dat1, Dat2 = g.Dat2, Dat3 = g.Dat3, Mod = g.Mod,
                MeasuredFeature = g.MeasuredFeature, Target = RowContent(g.TargetRowId)
            };
        }

        Dictionary<string, object> GtolDict(GtolRecord g)
        {
            return new Dictionary<string, object>
            {
                {"gtol_id", g.Id}, {"raw_id", g.RawId}, {"group_id", g.GroupId}, {"frame_order", g.FrameOrder},
                {"frame_count", g.FrameCount}, {"frame_type", g.FrameType},
                {"frame_reason", g.FrameReason}, {"zone_type", g.ZoneType},
                {"sym", g.Sym}, {"val", g.Val}, {"dat1", g.Dat1}, {"dat2", g.Dat2}, {"dat3", g.Dat3}, {"mod", g.Mod},
                {"measured_feature", g.MeasuredFeature}, {"include", g.Include}, {"reason", g.Reason},
                {"value_mm", g.ValueMm}, {"target_row_id", g.TargetRowId}, {"source", g.Source}
            };
        }

        List<QcDatum> BuildDatumView()
        {
            var outList = new List<QcDatum>();
            foreach (DatumRecord d in _datums)
                outList.Add(new QcDatum
                {
                    Letter = d.Letter, View = d.View, Target = d.FeatureType,
                    Near = !string.IsNullOrEmpty(d.SizeRowId) ? RowContent(d.SizeRowId) : d.SizeText,
                    NearManual = d.Source == "manual" ? (!string.IsNullOrEmpty(d.SizeRowId) ? RowContent(d.SizeRowId) : d.SizeText) : "",
                    Coord = d.Coord, Manual = d.Source == "manual" ? d.FeatureType : ""
                });
            return outList;
        }

        QcDocument Clone()
        {
            var x = new QcDocument();
            x.DrawingNo = DrawingNo; x.Version = Version; x._nextRow = _nextRow; x._nextGtol = _nextGtol;
            foreach (QcDimRow r in _rows) x._rows.Add(CloneRow(r, false));
            foreach (GtolRecord g in _gtols) x._gtols.Add(new GtolRecord
            {
                Id = g.Id, RawId = g.RawId, GroupId = g.GroupId, OriginalGroupId = g.OriginalGroupId,
                TargetRowId = g.TargetRowId,
                FrameOrder = g.FrameOrder, Sym = g.Sym, Val = g.Val, Dat1 = g.Dat1, Dat2 = g.Dat2, Dat3 = g.Dat3,
                FrameCount = g.FrameCount, FrameType = g.FrameType, FrameReason = g.FrameReason, ZoneType = g.ZoneType,
                Mod = g.Mod, MeasuredFeature = g.MeasuredFeature, Reason = g.Reason, Source = g.Source,
                Include = g.Include, ValueMm = g.ValueMm
            });
            foreach (DatumRecord d in _datums) x._datums.Add(new DatumRecord
            {
                Id = d.Id, Letter = d.Letter, View = d.View, FeatureType = d.FeatureType,
                SizeRowId = d.SizeRowId, SizeText = d.SizeText, Coord = d.Coord, Source = d.Source
            });
            return x;
        }

        void Restore(QcDocument x)
        {
            DrawingNo = x.DrawingNo; Version = x.Version; _nextRow = x._nextRow; _nextGtol = x._nextGtol;
            _rows.Clear(); _gtols.Clear(); _datums.Clear();
            foreach (QcDimRow r in x._rows) _rows.Add(CloneRow(r, false));
            foreach (GtolRecord g in x._gtols) _gtols.Add(g);
            foreach (DatumRecord d in x._datums) _datums.Add(d);
        }

        string NewRowId() { return "DIM-L" + (_nextRow++).ToString("0000"); }
        string NewGtolId() { return "GDT-L" + (_nextGtol++).ToString("0000"); }
        void TouchRowCounter(string id) { int n; if (TryTail(id, out n) && n >= _nextRow) _nextRow = n + 1; }
        void TouchGtolCounter(string id) { int n; if (TryTail(id, out n) && n >= _nextGtol) _nextGtol = n + 1; }
        static bool TryTail(string id, out int n)
        {
            n = 0; Match m = Regex.Match(id ?? "", @"(\d+)$"); return m.Success && int.TryParse(m.Value, out n);
        }
    }
}
