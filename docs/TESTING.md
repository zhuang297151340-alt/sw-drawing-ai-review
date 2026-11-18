# 测试说明

## 发布前已执行

- 使用清理后的 `build.ps1` 和 SOLIDWORKS 2016 Interop 成功编译 64 位 `SwQcAddin.dll`。
- 运行 PDF 公差框分类、确认项变更、Kimi 请求参数、流式响应和过载重试回归脚本，均通过。
- 检查仓库未包含真实 API Key、用户配置、客户图纸、日志、Python 运行环境和 SOLIDWORKS 专有 Interop DLL。

## 自动测试

无需真实 PDF 的回归脚本可直接运行：

```powershell
python .\src\sw-qc-ai\tests\test_pdf_confirmation_mutations.py
python .\src\sw-qc-ai\tests\test_pdf_gtol_classification.py
python .\src\sw-qc-ai\tests\test_pdf_provider_kimi.py
```

以下脚本需要传入已获授权且可用于测试的 PDF：

```powershell
python .\src\sw-qc-ai\tests\test_pdf_two_phase_cli.py <测试图纸.pdf>
python .\src\sw-qc-ai\tests\test_pdf_whole_audit.py <测试图纸.pdf>
```

## 尚未完成

- SOLIDWORKS 2017–2024 的逐版本、逐 Service Pack 真实图纸全流程回归。
- 不同企业模板、字体、语言和 PDM/网络环境的覆盖。
- 所有 AI 提供商、模型版本和限流策略的长期稳定性验证。

因此兼容性文档使用“目标支持/待验证”，不作未经验证的全版本承诺。

