# 安装说明

## 一键安装包（推荐）

### 准备条件

- Windows 10/11 64 位。
- 已安装 64 位 SOLIDWORKS。
- 具备本机管理员权限。
- 首次安装或升级前关闭 SOLIDWORKS。

### 安装步骤

1. 完整解压收到的安装包，不要直接在压缩包内运行。
2. 关闭 SOLIDWORKS。
3. 右键运行 `安装.bat`（或 `install.bat`），选择“以管理员身份运行”。
4. 等待安装程序显示注册成功。
5. 打开 SOLIDWORKS，在 `工具 → 插件` 中确认 `工程图 QC` 已加载。
6. 打开工程图，通过 `工具 → 工程图QC → 审图` 进入主界面。

安装程序应保留已存在的 AI/API 配置，只更新程序文件和运行路径。升级后仍建议先在“AI 设置”中检查模型、地址和密钥是否显示正常。

### 卸载

关闭 SOLIDWORKS 后，以管理员身份运行安装目录中的 `卸载_工程图QC.bat`。卸载前如需保留配置，请备份：

`%LOCALAPPDATA%\SwQcAddin\qc_addin_config.ini`

## AI 设置

- 通用审核通道的设置位于主审图窗口“标准库”之后的“AI 设置”页。
- API Key 由用户向所选模型提供商申请，项目不附带开发者密钥。
- PDF 通道使用独立的视觉模型配置，因为它必须调用支持图片输入的模型。
- 提供商产生的模型调用费用由用户自行承担。

## 从源码构建

### 需要的软件

- Windows 10/11 64 位。
- .NET Framework 4.x SDK/编译器。
- 64 位 SOLIDWORKS 安装及其 API Interop 程序集。
- Python 3.11（用于运行/测试 Python 引擎）。

### SOLIDWORKS Interop

从你有合法授权的 SOLIDWORKS 安装目录中复制以下文件到仓库的 `vendor\solidworks`：

- `SolidWorks.Interop.sldworks.dll`
- `SolidWorks.Interop.swconst.dll`
- `SolidWorks.Interop.swpublished.dll`

这些文件通常可在 SOLIDWORKS 的 `api\redist` 目录找到。本仓库不分发它们。

### Python 依赖

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

### 构建 C# 插件

```powershell
powershell -ExecutionPolicy Bypass -File .\build.ps1
```

构建产物输出到 `build`。源码构建不等同于完整的一键安装包；注册、运行目录、Python 环境和第三方许可证仍需按发行包结构组装。

## 常见问题

- **菜单中没有插件**：先确认安装时 SOLIDWORKS 已关闭，并以管理员身份重新运行安装程序。
- **AI 连接失败**：检查 API 地址、模型名、Key、代理和提供商余额；通用模型与视觉模型不是同一个概念。
- **PDF 审核超时或 429**：通常是模型服务拥塞、限流或网络问题，不代表图纸一定有错误。
- **YAML 规则不可用**：检查 PyYAML 是否已安装；发行包会随附所需依赖。

