# Third-party notices

MusicBox 的 MIT 许可证不替代第三方许可。此源码导出不包含 Python/.NET/Windows App SDK/WebView2 二进制、官网资源、音频、封面、歌词、用户头像或网页抓取。

## QQ 登录参考适配

`musicbox/login.py` 的 QQ 授权交换参考/适配了 `@yakult-green-tea/qq-music-api@3.3.6` 的 `dist/src/services/auth/ptlogin.js`。原项目采用 MIT。

- 固定版本来源：https://unpkg.com/@yakult-green-tea/qq-music-api@3.3.6/dist/src/services/auth/ptlogin.js
- 许可原文：[LICENSES/qq-music-api-MIT.txt](LICENSES/qq-music-api-MIT.txt)
- 上游署名：https://unpkg.com/@yakult-green-tea/qq-music-api@3.3.6/ATTRIBUTION.md
- 作者/维护信息：Rain120（原项目），yakult-green-tea（维护/发布），chthollyphile、lantudou（上游列出的贡献者）。

原许可的版权行字面为 `Copyright (c) 2019 `，未填持有人；本导出原样保留，不擅自补名。上游ATTRIBUTION亦说明保留该行。本文署名是补充信息，不修改原许可。

## 排除的实现

开发版本曾包含按酷狗官网 infSign、酷我官网 Secret 函数移植的签名实现。未核实其可再分发授权，所以本MIT源码导出**不收录这些函数体或其参考脚本**，改为显式可选本机适配模块接口。见 [来源登记](docs/PROVENANCE.md)。缺模块时明确失败，不伪造签名或悄悄降级。

L-1124/QQMusicApi（已核实发布版使用GPLv3+）仅在备用配置中作为项目链接；本导出不复制、安装、打包或导入其代码。MakcRe/KuGouMusicApi、qyhqiu/kuwoMusicApi同为可选独立本机服务参考，其仓库链接不是本软件代码许可。使用者部署这些项目时自行遵守其当前许可证；不能据此把第三方服务代码改称MIT。

## 运行依赖（不随源码复制）

| 组件 | 上游许可概览 |
|---|---|
| FastAPI、Pydantic、Starlette等 | MIT / BSD（按锁定版本元数据） |
| requests | Apache-2.0 |
| uvicorn | BSD-3-Clause |
| cryptography | Apache-2.0 OR BSD-3-Clause（含其依赖的独立通知） |
| certifi | MPL-2.0 |
| CPython嵌入运行时 | Python/PSF许可及捆绑第三方通知 |
| .NET runtime | Microsoft/.NET MIT及其第三方通知 |
| Windows App SDK / WinUI 3 | NuGet包与微软SDK许可条款，不能只看源码仓库许可证 |
| WebView2 Runtime | Microsoft WebView2分发许可，需单独安装 |
| Inno Setup | 上游许可；本导出只提供安装器脚本，不捆绑编译器 |

构建二进制时，脚本会从精确安装的Python发行包复制许可证/元数据，从NuGet包收集license/notice，并保留官方CPython归档自带许可证。维护者仍需在实际分发前检查 `runtime-licenses/`、`nuget-licenses/` 和 `bundle-manifest.json` 的覆盖；表格是概览，不替代每个版本的原文。
