# 架构与数据流

## 主通道：SOLIDWORKS 原生工程图审核

```text
SOLIDWORKS 工程图
  → C# 插件读取原生对象/属性
  → 尺寸/公差统一数据表
  → 本地 Python 规则引擎
  → 通用 AI 语义复核（可选）
  → 简明审核结果与报告
```

尺寸/公差统一数据表是后续窗口、报告和 AI 输入的唯一事实来源。人工编辑与归属修正必须先写回该表，避免重复识别和不同窗口之间自相矛盾。

## 补充通道：PDF 视觉审核

```text
PDF
  → 本地按页渲染
  → 视觉模型识别与审核
  → 风险项 + 不确定性备注
```

PDF 通道无法获得 SOLIDWORKS 原生语义对象，判断能力受图像质量和视觉模型影响，因此定位为补充能力。

## 目录

- `src/SwQcAddin`：C# SOLIDWORKS 插件和 WinForms 界面。
- `src/sw-qc-ai`：Python 规则、解析、报告及 PDF 视觉审核引擎。
- `src/sw-qc-ai/rules`：YAML 规则与 AI 输出约束。
- `installer`：发行包安装脚本。
- `docs`：用户和开发文档。

