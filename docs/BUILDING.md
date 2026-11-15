# 构建与验证

## 支持基线

- Windows 10/11 x64，.NET SDK 8（本机验证8.0.428）。WinUI项目固定Windows App SDK 1.8.260921001和SDK BuildTools 10.0.26100.4654。
- Python 3.10+；嵌入式Windows发行包固定Python3.12.10，所以**构建便携包时依赖环境必须使用CPython3.12/x64**，避免复制其他PythonABI的pyd。
- WebView2 Runtime需在目标机安装，不随源码或便携包伪装分发。
- 本项目不使用WPF或WebView伪装主UI；WebView只承载官方网页登录。

## 初始化Python环境

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
```

`requirements.txt`是兼容范围；`requirements-lock.txt`/`requirements-dev-lock.txt`记录本次验证环境精确版本，适用于Python3.12。不同平台或Python次版本可能需要重新解析兼容依赖，不能宣称同一锁覆盖所有ABI。用锁复现：

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev-lock.txt
```

不要把依赖装进系统Python后再盲目复制全局site-packages。源码构建只读取显式解释器的运行依赖及其传递依赖，并排除脚本目录和用户数据。

## 源码验证

```powershell
.\.venv\Scripts\python.exe -m pytest -m "not live" --basetemp=_scratch/pytest
.\.venv\Scripts\python.exe tools/scan_release.py
dotnet run --project tools/winui-diagnostics/ApiContractCheck/ApiContractCheck.csproj -c Release
dotnet build desktop/MusicBox.Desktop/MusicBox.Desktop.csproj -c Release
```

`live`只用于显式联网测试，不向CI注入真实账号。原生窗口/音频和官方手机授权验收不能仅靠编译通过推断。

## 开发运行

```powershell
$env:MUSICBOX_PYTHON = (Resolve-Path .\.venv\Scripts\python.exe).Path
$env:MUSICBOX_HOME = Join-Path $env:LOCALAPPDATA 'MusicBox-dev'
& ./desktop/MusicBox.Desktop/bin/Release/net8.0-windows10.0.19041.0/win-x64/MusicBox.Desktop.exe
```

后端默认私有回环动态端口；桌面退出回收自己启动的后端。显式设置MUSICBOX_API时连接对应服务，该服务不是由桌面拥有，不应代杀。

## 干净便携包

```powershell
.\.venv\Scripts\python.exe packaging/build_portable.py --python .\.venv\Scripts\python.exe
```

脚本将：

1. Release构建自包含WinUI应用。
2. 创建全新目录，过滤profile、数据库、密钥、日志和pdb，不合并运行过的目录。
3. 从python.org取固定版本嵌入式归档，使用HTTPS，记录SHA256；额外检查归档路径。
4. 从明确Python3.12/x64环境复制运行依赖的发行记录和许可证。
5. 从实际NuGet还原图收集license/notice及nuspec，保留CPython归档自带许可。
6. 写入无本机绝对路径的bundle-manifest与portable-mode，生成ZIP和SHA256。

输出已存在时脚本拒绝；用 `--output`/`--archive`选择全新路径，而不是覆盖真实用户data。当前归档摘要是传输后记录，尚未预置独立发布方哈希/签名验证，发布者应额外核对Python官方下载签名。源码里不含私钥或签名证书。

构建包没有许可未核实的网页签名模块，见PROVENANCE；不要把私有开发版能播放的所有曲目当作公开源码功能保证。

## 安装器（可选）

先构建便携目录，再用安装好的Inno Setup6：

```powershell
ISCC.exe packaging/MusicBox.iss
```

安装器只复制dist/MusicBox，默认用户目录，不捆绑额外登录数据。不要将portable-mode打进安装器；本脚本模板排除它。安装/卸载需要目标用户的文件及注册表权限，本次只验证编译，不承诺所有系统安装成功。

## 正式签名

```powershell
./packaging/Sign-Release.ps1 -Thumbprint YOUR_CERT_THUMBPRINT -SignTool PATH_TO_SIGNTOOL -Files ./dist/MusicBox/MusicBox.Desktop.exe
# 重新编译包含已签应用的安装器，然后签最终安装器
```

持有证书及其私钥需由发布者自行安排，脚本检查用途、有效期、证书链和时间戳。不要创建自签根后宣称公众可信，不将证书私钥上传GitHub。重新签名后重新生成SHA256和做实际窗口/播放/卸载验收。
