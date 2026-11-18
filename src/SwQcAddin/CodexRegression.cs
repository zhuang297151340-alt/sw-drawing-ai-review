using System;
using System.Collections.Generic;

namespace SwQc
{
    /// <summary>本次修复的最小 C# 回归，不依赖 SOLIDWORKS。</summary>
    static class CodexRegression
    {
        static int Main()
        {
            var rep = new QcReport();
            var a = new QcDimRow { Content = "3.5" };
            var b = new QcDimRow { Content = "5" };
            a.Gtols.Add(new QcGtolLine { Sym = "⌓", Val = "0.02", Dat1 = "A" });
            b.Gtols.Add(new QcGtolLine { Sym = "⌓", Val = "0.02", Dat1 = "A" });
            rep.DimRows.Add(a);
            rep.DimRows.Add(b);

            QcRunner.DedupeDimRows(rep, "TEST");
            if (a.Gtols.Count != 1 || b.Gtols.Count != 1)
                throw new Exception("跨尺寸行误删了内容相同的独立几何公差");

            var edited = new QcDimRow { Content = "3.5" };
            QcResultForm.ApplyGtolTextOf(edited, "⌖ φ0.02 |A |B |C");
            if (edited.Gtols.Count != 1 || edited.GtolSym != "⌖" ||
                edited.GtolVal != "φ0.02" || edited.GtolDat1 != "A" ||
                edited.GtolDat2 != "B" || edited.GtolDat3 != "C")
                throw new Exception("几何公差编辑后未同步到持久化字段");
            if (QcResultForm.GtolTextOf(edited).IndexOf("⌖ φ0.02 |A |B |C", StringComparison.Ordinal) < 0)
                throw new Exception("几何公差无法从尺寸表重新序列化");

            if (QcRunner.GtolKey("⌖", "φ0.02", "A", "B", "Ⓜ", "C") ==
                QcRunner.GtolKey("⌖", "φ0.02", "A", "B", "Ⓜ", "D"))
                throw new Exception("几何公差签名遗漏第三基准");

            // 不同物理公差框不能因符号相同而互相“取严”。
            var separate = new List<QcGtolLine> {
                new QcGtolLine { GtolId = "G1", Sym = "—", Val = "0.05", ValueMm = 0.05 },
                new QcGtolLine { GtolId = "G2", Sym = "—", Val = "0.03", ValueMm = 0.03 }
            };
            if (Math.Abs(GtolRules.Recompute(separate, false) - 0.08) > 0.00001)
                throw new Exception("不同公差框被错误取严");

            // 同一复合框内的同项目多值才按最严值计算。
            var compound = new List<QcGtolLine> {
                new QcGtolLine { GtolId = "G3", GroupId = "GROUP-1", Sym = "—", Val = "0.05", ValueMm = 0.05 },
                new QcGtolLine { GtolId = "G4", GroupId = "GROUP-1", Sym = "—", Val = "0.03", ValueMm = 0.03 }
            };
            if (Math.Abs(GtolRules.Recompute(compound, false) - 0.03) > 0.00001)
                throw new Exception("复合框未按最严值计算");

            // 未归属复合框在主文档投影后仍只能是一行，成员和顺序完整保留。
            var rep2 = new QcReport();
            rep2.UnlinkedGtols.Add(new QcUnlinkedGtol {
                GtolId = "U1", GroupId = "UG-1", FrameOrder = 1,
                Sym = "—", Val = "0.02", MeasuredFeature = "轴心" });
            rep2.UnlinkedGtols.Add(new QcUnlinkedGtol {
                GtolId = "U2", GroupId = "UG-1", FrameOrder = 2,
                Sym = "⌭", Val = "0.01", MeasuredFeature = "圆柱面" });
            QcDocument doc = QcDocument.FromReport(rep2);
            var projected = new QcReport();
            doc.ApplyToReport(projected);
            if (projected.UnlinkedGtols.Count != 1 || projected.UnlinkedGtols[0].Members.Count != 2 ||
                projected.UnlinkedGtols[0].Members[1].FrameOrder != 2)
                throw new Exception("未归属复合框在主文档投影时被拆散");

            // 双框编辑必须保留组号、顺序和框数；旧的长分类备注由主文档重建为简短结论。
            var editGroup = new QcDimRow { Content = "5" };
            editGroup.Gtols.Add(new QcGtolLine { GtolId = "E1", GroupId = "EG-1", FrameOrder = 1,
                FrameCount = 2, FrameType = "独立", Sym = "⌖", Val = "φ0.05", Dat1 = "A", Dat2 = "B" });
            editGroup.Gtols.Add(new QcGtolLine { GtolId = "E2", GroupId = "EG-1", FrameOrder = 2,
                FrameCount = 2, FrameType = "独立", Sym = "⌭", Val = "0.03" });
            QcResultForm.ApplyGtolTextOf(editGroup, "⌖ φ0.05 |A |B；⌭ 0.03");
            if (editGroup.Gtols.Count != 2 || editGroup.Gtols[0].GroupId != "EG-1" ||
                editGroup.Gtols[1].GroupId != "EG-1" || editGroup.Gtols[1].FrameOrder != 2 ||
                editGroup.Gtols[0].FrameCount != 2 || editGroup.Gtols[1].FrameCount != 2)
                throw new Exception("编辑双框后组身份或框数丢失");

            var rep3 = new QcReport();
            editGroup.Note = "公差框类型：独立公差（独立公差（单框）：框数=1）";
            rep3.DimRows.Add(editGroup);
            QcDocument doc3 = QcDocument.FromReport(rep3);
            var projected3 = new QcReport();
            doc3.ApplyToReport(projected3);
            if (projected3.DimRows.Count != 1 || !projected3.DimRows[0].Note.StartsWith("独立公差") ||
                projected3.DimRows[0].Note.Contains("公差框类型") ||
                projected3.DimRows[0].Gtols.Count != 2 || projected3.DimRows[0].Gtols[0].FrameCount != 2)
                throw new Exception("框格分类未跟随几何公差组，或备注仍含旧长文本：rows=" +
                    projected3.DimRows.Count + " note=" + (projected3.DimRows.Count > 0 ? projected3.DimRows[0].Note : "") +
                    " gtols=" + (projected3.DimRows.Count > 0 ? projected3.DimRows[0].Gtols.Count.ToString() : "0") +
                    " frameCount=" + (projected3.DimRows.Count > 0 && projected3.DimRows[0].Gtols.Count > 0 ?
                        projected3.DimRows[0].Gtols[0].FrameCount.ToString() : "0"));

            Console.WriteLine("CSharp regression OK");
            return 0;
        }
    }
}
