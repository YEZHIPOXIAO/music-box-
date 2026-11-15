# MusicBox

![Windows](https://img.shields.io/badge/Windows-10%2F11-0078D4)
![WinUI](https://img.shields.io/badge/UI-WinUI%203-5C2D91)
![Python](https://img.shields.io/badge/Python-3.10%2B-3776AB)
![License](https://img.shields.io/badge/license-MIT-green)

本地运行的聚合音乐客户端：**C# / WinUI 3 原生桌面界面 + Python / FastAPI 后端**，支持 QQ音乐、酷狗、酷我搜索、官方网页登录、音频代理、本地歌单及歌词。

> 非平台官方客户端，无平台合作或服务保证。仅使用账号本人获得授权的内容；会员、免费听时长、活动、地区和单曲权益均由平台决定。接口可能变更，登录成功不等于所有曲目可播放。

## 开源导出的功能差异

**此导出不附带许可未核实的酷狗/酷我网页签名函数。** 为避免把来源不明实现直接标为 MIT，保留了可选本机适配模块和独立备用 API 接口。未安装你有权使用的适配时，相关登录播放会明确提示缺实现；不能承诺酷狗/酷我会员播放开箱可用。你本机已验证的完整版本未被修改。见 [来源登记](docs/PROVENANCE.md)。

## 功能

- 三平台搜索，结果保留平台和曲目身份；失败不静默跨平台换歌。
- 播放/暂停、停止、重播、上下曲、进度拖动。
- 顺序、列表循环、单曲循环、随机模式；快捷队列可选曲、移除和保留当前曲目。
- 音质请求和实际返回音质提示；音质不可用时明确显示结果。
- 可置顶、可关闭的独立桌面歌词窗口；音量和静音。
- 各平台官方网页展示登录选项；在官方页面完成手机授权后手动“完成并绑定”。
- 本地加密保存账号凭据，本地 SQLite 保存歌单和历史。
- 可按平台独立选择自己运行的回环 HTTP 备用服务。第三方仓库地址不是服务地址，备用服务自行管理其账号。

Python 包中也保留较早的浏览器/Tk 兼容界面；本项目主桌面界面为 WinUI 3，兼容界面不保证与原生桌面功能完全相同。

## 环境

| 用途 | 依赖 |
|---|---|
| 原生桌面构建 | Windows 10/11 x64、.NET SDK 8（验证基线 8.0.428） |
| 官方网页登录 | Microsoft Edge WebView2 Runtime |
| 后端/测试 | Python 3.10+（本地验证 3.12）、项目依赖 |
| 可选安装器 | Inno Setup 6，命令行编译器 ISCC |

NuGet 固定 Windows App SDK `1.8.260921001` 与 Windows SDK BuildTools `10.0.26100.4654`，随自包含输出部署。不要降回旧运行时：此前资源管理组件在特定长路径下出现过原生堆错误。

## 开发启动

以下命令在仓库根目录执行。普通终端示例，不需要把任何真实 Cookie 放入源码。

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt

# 后端可单独运行（兼容浏览器界面）
.\.venv\Scripts\python.exe -m musicbox --ui browser --no-browser
```

启动 WinUI 桌面程序：

```powershell
$env:MUSICBOX_PYTHON = (Resolve-Path .\.venv\Scripts\python.exe).Path
# 推荐开发数据与源码分开；此目录绝不能提交到 GitHub。
$env:MUSICBOX_HOME = Join-Path $env:LOCALAPPDATA 'MusicBox-dev'
dotnet build desktop/MusicBox.Desktop/MusicBox.Desktop.csproj -c Release
& ./desktop/MusicBox.Desktop/bin/Release/net8.0-windows10.0.19041.0/win-x64/MusicBox.Desktop.exe
```

桌面会启动自己拥有的回环后端，默认动态端口，关闭时回收。`MUSICBOX_API` 可显式连接已启动的本机后端。Linux/macOS 可运行后端和离线测试，不能构建/运行 WinUI 3。

## 构建可交付包

见 [构建说明](docs/BUILDING.md)。发布脚本接收显式 Python 路径，将应用、单独的 CPython 嵌入式运行时和运行依赖放入干净暂存目录，再生成 ZIP；源码包本身不包含运行时二进制。

```powershell
.\.venv\Scripts\python.exe packaging/build_portable.py --python .\.venv\Scripts\python.exe
```

生成 `dist/MusicBox-portable-unsigned.zip`。完整解压后运行其中的桌面 exe；ZIP 的 `portable-mode` 标记使数据位于应用旁 `data/`。安装器模式默认使用用户 LocalAppData。签名需你自己的有效代码签名证书；源码公开和 SHA-256 校验不等于可信发布者签名，也不保证杀软零误报。

## 测试

```powershell
.\.venv\Scripts\python.exe -m pytest -m "not live" --basetemp=_scratch/pytest

dotnet run --project tools/winui-diagnostics/ApiContractCheck/ApiContractCheck.csproj -c Release
python tools/scan_release.py
```

当前开源导出保留 196 个离线测试及 4 个显式联网测试（包含发布扫描自测）。CI 默认只运行离线测试及 Windows 构建；联网测试需自行执行 `pytest -m live`，不依赖真实账号。测试中的 Cookie/会话值都是固定合成样本。

本次本机验证记录见 [发布审计](docs/RELEASE_AUDIT.md)。仓库不收录真实账号验收报告、音频、官网脚本、浏览器 profile 或扫码截图；不能把一次本地验收扩展成所有会员曲目/机器可用的保证。

## 登录、会员及免费时长

在“账号”打开平台官网，按网页提供的方式登录，手机确认后点击“完成并绑定”。Cookie 是账号凭据，请不要粘贴到 issue、聊天、工作流配置或示例。

“会员检测”只反映已查询产品。会员未激活不会阻止向平台请求播放；免费听歌时长、购买单曲、活动等也可能授权。平台没有明确返回的余额和本次权益来源显示未知，程序不估算、不领取或购买权益。

平台可能要求客户端、单独购买或限制地区/音质；只允许试听的响应不作为整首成功。无权限时不会伪造会员或播放其他录音替代。

## 数据与安全

- 程序本身没有遥测上报；搜索、登录、封面、歌词、权益请求和音频传输会访问相应平台及其 CDN，平台网页可能自带统计，不能声称“所有数据不出本机”。
- Fernet 为默认账号存储方案；密钥和数据库在同一数据目录。它不能抵御能读取同一用户全部文件的恶意进程。Windows 上 `0600` 不等于完整 ACL 隔离。
- Cookie 按平台域/路径安装，仅向白名单 HTTPS API 发送；音频代理不向 CDN 转发账号 Cookie。
- 默认只监听回环地址。Host/Origin 校验不能代替完整本地身份认证；其他本机进程可能访问 API。**不要对公网或局域网暴露已绑定账号的服务。**
- 本地删除不是 SSD、备份或云同步副本的可靠擦除承诺。
- HTTP 音频地址和封面等来自上游；完整 SSRF 防护、所有输入面和依赖并未经独立安全审计，见 [安全说明](SECURITY.md)。

数据目录包含 `musicbox.sqlite3`、`.secret.key`、`WebViewLogin/`、播放来源配置等。迁移需关闭应用后成对处理数据库和密钥；不要提交整个运行目录。

## 仓库结构

```text
musicbox/                    Python 后端、provider、兼容网页/Tk界面
 desktop/MusicBox.Desktop/    WinUI 3 界面、播放队列和媒体服务
 tests/                      合成样本离线回归及显式联网测试
 tools/                      C# 契约检查、发布隐私扫描
 packaging/                  便携包构建、安装器与显式签名入口
 docs/                       构建、隐私、来源和发布记录
 examples/                   无凭据配置样例
 LICENSES/                   需保留的第三方许可文本
```

## 贡献与许可

阅读 [贡献指南](CONTRIBUTING.md)、[第三方声明](THIRD_PARTY_NOTICES.md)、[来源登记](docs/PROVENANCE.md) 和 [MIT 许可证](LICENSE)。MIT 只覆盖有权授予许可的 MusicBox 代码，不授予平台商标、音频/歌词版权、服务访问权或第三方组件权利。发布二进制前仍需随包保留第三方许可并审查平台服务条款。

该目录是一份不含旧 Git 历史的新源码导出，未自动创建远端仓库、commit 或上传。如何推送见 [GitHub 发布步骤](docs/GITHUB_RELEASE.md)。
