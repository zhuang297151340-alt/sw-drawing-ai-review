using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Runtime.InteropServices;
using System.Text;
using System.Web.Script.Serialization;
using System.Windows.Forms;

namespace SwQc
{
    /// <summary>报告数据模型（从 Python 判定引擎的 JSON 读取）</summary>
    public class QcItem
    {
        public string RuleId { get; set; }
        public string Severity { get; set; }     // critical / major / minor
        public string Title { get; set; }
        public string Detail { get; set; }
        public string Suggestion { get; set; }
        public string Standard { get; set; }
        public string Location { get; set; }
        public string Evidence { get; set; }
        /// <summary>审图结果三大类：图纸信息 / 尺寸 / 技术要求</summary>
        public string Category { get; set; }

        public string SeverityText
        {
            get
            {
                if (Severity == "critical") return "严重";
                if (Severity == "major") return "一般";
                if (Severity == "minor") return "轻微";
                if (Severity == "info") return "—";
                return "轻微";
            }
        }
    }

    /// <summary>通过项（带类别，用于三段分组显示）</summary>
    public class QcPassed
    {
        public string Category = "图纸信息";
        public string Text = "";
        public QcPassed() { }
        public QcPassed(string cat, string text) { Category = cat; Text = text; }
    }

    /// <summary>
    /// 尺寸/公差一行（结构化）。
    /// 用户要求（2026-10-23）：只有**带公差**的尺寸才参与「公差计算」，
    /// 所以这里把上/下偏差换算成 mm 数值，界面据此判断 HasTol 并排序。
    /// </summary>
    public class QcDimRow
    {
        /// <summary>会话内稳定编号。修改尺寸内容时保持不变，供几何公差归属引用。</summary>
        public string RowId = "";
        public string Kind = "";          // 类别：轮廓-长 / 板厚 / 特征尺寸 …
        public string Content = "";       // 内容：φ35 / 452 …
        public string TolDisplay = "";    // 显示串：上 +0.005 下 -0.005
        public string IT = "";            // IT 等级
        public string Capability = "";    // 常规加工能否满足
        public string Note = "";          // 备注
        /// <summary>修正符号：Ⓔ包容要求 / Ⓜ最大实体 / Ⓛ最小实体（图上标了才有）</summary>
        public string Modifier = "";
        // 2026-10-24：挂在该尺寸上的几何公差（用于公差计算面板预填框格）
        public string GtolSym = "";     // 符号，如 "—"
        public string GtolVal = "";     // 公差值，如 "0.01"
        public string GtolMod = "";     // 修正符号，如 "Ⓜ"
        public string GtolDat1 = "";    // 基准 1
        public string GtolDat2 = "";    // 基准 2
        public string GtolDat3 = "";    // 基准 3
        public double Nominal;            // 基本尺寸（mm）
        public double TolUp;              // 上偏差（mm，带符号）
        public double TolLow;             // 下偏差（mm，带符号）
        public bool HasTol;               // 有尺寸公差或几何公差
        public double Band;               // 公差带宽度 = 上 - 下
        public bool IsOutline;            // 轮廓/板厚（不可勾选计算？仍可）
        public bool IsManual;             // 手工新增/编辑的行（仅本会话）
        // 2026-10-25：一个尺寸可挂多条几何公差；计入规则由 Python 侧自动判定
        public List<QcGtolLine> Gtols = new List<QcGtolLine>();
        public double SigmaT;             // Σt：计入作用边界的几何公差之和（mm）
        public double MmsShaft, MmsHole;  // 最大实体尺寸（轴/孔两种口径）
        public double MmvcShaft, MmvcHole;// 最大作用边界（轴 MMS+Σt / 孔 MMS−Σt）

        public double Hi { get { return Nominal + TolUp; } }
        public double Lo { get { return Nominal + TolLow; } }
    }

    /// <summary>未关联到任何尺寸的几何公差（交给用户确认归属）</summary>
    public class QcUnlinkedGtol
    {
        /// <summary>会话内唯一编号；不能再用“符号+数值+基准”代替身份。</summary>
        public string GtolId = "";
        public string Sym = "", Val = "", Dat1 = "", Dat2 = "", Dat3 = "", Mod = "", Label = "", View = "";
        /// <summary>原始挂接的尺寸行内容（用于区分同签名、挂在不同尺寸上的多条公差）</summary>
        public string Target = "";
        /// <summary>附加说明（如：被测要素 轴心/平面）</summary>
        public string Extra = "";
        public string RawId = "";
        public string GroupId = "";
        public int FrameOrder = 1;
        public int FrameCount = 1;
        public string FrameType = "";          // 独立 / 组合 / 复合 / 待确认
        public string FrameReason = "";        // 结构化判定依据，只给 AI/诊断，不塞进备注
        public string ZoneType = "";           // CZ / SZ / 冲突（公差带修饰，不是框格类别）
        public string MeasuredFeature = "";
        /// <summary>同一物理公差标注的成员（可能是独立多框、组合多单段或复合框）</summary>
        public List<QcGtolLine> Members = new List<QcGtolLine>();
        /// <summary>人工记忆为「不参与计算」：仍显示、可再改</summary>
        public bool Ignored = false;
        public string Display
        {
            get
            {
                string t = (Sym ?? "") + (Val ?? "");
                if (!string.IsNullOrEmpty(Dat1)) t += " " + Dat1;
                if (!string.IsNullOrEmpty(Dat2)) t += " " + Dat2;
                if (!string.IsNullOrEmpty(Dat3)) t += " " + Dat3;
                if (!string.IsNullOrEmpty(View)) t += "（" + View + "）";
                if (!string.IsNullOrEmpty(Extra)) t += "  【" + Extra + "】";
                return t;
            }
        }
    }

    /// <summary>一条几何公差（一个尺寸可有多条）</summary>
    public class QcGtolLine
    {
        /// <summary>会话内唯一编号；同内容的两条公差也必须拥有不同编号。</summary>
        public string GtolId = "";
        public string Sym = "";      // 符号：— ⌖ ⊥ …
        public string Val = "";      // 公差值，如 0.02
        public string Dat1 = "";
        public string Dat2 = "";
        public string Dat3 = "";
        public string Mod = "";
        public bool Include;         // 是否计入最大作用边界（系统按规则判）
        public string Reason = "";   // 计入/不计入的原因（界面显示，便于解释）
        public double ValueMm;
        public string RawId = "";
        public string GroupId = "";
        public int FrameOrder = 1;
        public int FrameCount = 1;
        public string FrameType = "";          // 分类跟随几何公差实体，不跟随尺寸文本
        public string FrameReason = "";
        public string ZoneType = "";
        public string MeasuredFeature = "";       // 数值（mm）
        /// <summary>该条为人工指定归属（可再修改/撤销）</summary>
        public bool Manual = false;
    }

    public class QcDatum
    {
        public string Letter = "";
        public string View = "";
        public string Target = "";   // 自动推断的指向要素
        public string Near = "";     // 关联尺寸（推断）
        public string NearManual = "";   // 人工指定的关联尺寸
        public string Coord = "";    // 指向坐标
        public string Manual = "";   // 人工指定
    }

    public class QcReport
    {
        public string FileName = "";
        public string Preset = "";
        public string PartType = "";
        public string DrawingScale = "";
        public int Critical, Major, Minor;
        public double ElapsedSec;
        public List<QcItem> Items = new List<QcItem>();
        /// <summary>未关联几何公差（用户确认归属用）</summary>
        public List<QcUnlinkedGtol> UnlinkedGtols = new List<QcUnlinkedGtol>();
        public List<string> Warnings = new List<string>();
        public Dictionary<string, string> Header = new Dictionary<string, string>();
        public int DimCount;
        public int UntolerancedCount;
        public bool NotesHas1804;
        public bool AiEnabled;
        public bool HeaderOnly;
        // 尺寸/公差分析行（结构化）
        public List<QcDimRow> DimRows = new List<QcDimRow>();
        // 兼容旧代码：类别|内容|公差|IT等级|常规加工|备注 的字符串行
        public List<string[]> ToleranceRows = new List<string[]>();
        public List<QcPassed> Passed = new List<QcPassed>();
        public List<QcDatum> Datums = new List<QcDatum>();
        public string DwgNo
        {
            get
            {
                string v;
                return (Header != null && Header.TryGetValue("图号", out v)) ? v : "";
            }
        }
        public string AiStatus = "";
        public int AiCount;
        public double AiElapsed;
        public string AiNote = "";
        public string PreviewPath = "";
        public string ReportDir = "";
        public string JsonPath = "";
        public string RawError = "";
    }
}
