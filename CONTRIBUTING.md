# 贡献指南

## 环境与验证

使用Python 3.10+与项目开发依赖；原生桌面构建需Windows x64和.NET SDK 8。

```powershell
python -m pip install -r requirements-dev.txt
python -m pytest -m "not live" --basetemp=_scratch/pytest
python tools/scan_release.py
dotnet run --project tools/winui-diagnostics/ApiContractCheck/ApiContractCheck.csproj -c Release
dotnet build desktop/MusicBox.Desktop/MusicBox.Desktop.csproj -c Release
```

CI覆盖离线测试和Windows构建。真实平台测试是显式操作，不应让CI使用个人账号、定时刷接口或消耗免费听歌时长。

## 改动约定

- 保留C# WinUI 3原生桌面，不把浏览器兼容页替代为主桌面。
- 测试凭据必须固定、合成、可辨认；不要复制真实响应、截图或登录profile作为fixture。
- 会员状态与播放权限独立。普通账号可能有免费听时长；平台未返回的余额/权益来源保持未知。
- 不能静默改平台、改歌曲版本、把试听当整首、或用本地标签伪造成功。
- 改Cookie、OAuth跳转、媒体取消/释放、缓存绑定时必须补对应安全/行为回归。
- 新依赖或参考代码须记录来源、版本、许可及必要通知。源码可读不等于可重许可；GPL依赖不能无说明复制到MIT文件中。
- 文档写实际验证范围。网络URL成功、编译成功、用户手机扫码和完整会员播放是不同验收层级。

## Issue / PR

附系统版本、应用版本、复现步骤和脱敏错误。不要贴真实Cookie、OAuth参数、数据库、密钥或崩溃dump。截图先隐藏昵称、头像、账号ID和二维码。

PR说明最终行为、原因、测试和已知限制。没有远端仓库地址前不要填虚构的issue链接或邮箱；维护者创建仓库后补充实际联系和安全报告渠道。
