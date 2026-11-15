# 发布到 GitHub

## 只使用这份干净导出

不要在包含真实账号的原工作区直接执行 `git add .`。仅将此目录作为新仓库根目录。它不包含旧Git历史；原目录不是Git仓库，因此本次没有可验证的历史提交清理。如果你另有旧远端仓库，必须单独扫描其全部历史；从工作树删除不能撤回已公开密钥。

上传前检查：

```powershell
python tools/scan_release.py
python -m pytest -m "not live" --basetemp=_scratch/pytest
```

`docs/PROVENANCE.md`说明了许可未核实的两个签名实现已排除，公开版不能假称它们开箱可用。MIT许可不能涵盖你无权许可的第三方代码或平台内容。

## 初始化新仓库

```powershell
git init -b main
git add .
git status --short
git diff --cached --stat
# 再审查暂存内容，确认没有data、profile、key、日志或真实截图。
git commit -m "Initial public source release"
```

然后在GitHub网页创建空仓库，再按GitHub给出的**真实仓库地址**配置remote并push。本次没有替你创建仓库、生成token、commit或上传；仓库拥有者、名称、联系渠道均未虚构。

建议创建后开启：Secret scanning/Push protection（可用时）、Private vulnerability reporting、分支保护和CI。GitHub的扫描不是本地凭据检查的替代。

## GitHub Actions

工作流默认离线Python测试、源码扫描、C#契约检查和Windows Release构建。不包含定时任务、不提交真实账号，不自动执行联网平台测试或自动发布签名二进制。

本机验证不代表托管Runner已运行；首次push后检查Actions实际结果。不要使用含个人缓存的自托管Runner直接发布未经检查的bin目录。

## Release附件

推荐：干净源代码ZIP、SHA256、公开的构建/隐私审计说明。二进制必须从干净源码按BUILDING重建，并保留依赖许可、记录SDK/运行时版本、确认未含任何data目录。不要把原开发版运行文件夹、测试版、官网截图、音频、抓包或dump当Release附件。

发布者代码签名证书必须自行取得，不上传PFX或密码。不加“已验证零误报”“VIP全解锁”等无法成立的声明。

## 若曾泄漏

1. 撤销对应平台会话/凭据及任何代码托管或云token。
2. 从发布附件、issue截图和Git历史移除，协调fork/cache副本。
3. 重新生成本地密钥不能自动撤销平台Cookie；两者必须分别处理。
4. 保留不含秘密值的事件记录。
