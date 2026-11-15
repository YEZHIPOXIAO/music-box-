# 来源与许可证边界

此登记区分“协议资料”“适配代码”和“独立第三方服务”。它是工程审计记录，不是对所有地区法律、平台服务条款或代码原创性的法律意见。

## QQ ptlogin

`musicbox/login.py` 的凭证交换路径与 MIT 包 `@yakult-green-tea/qq-music-api@3.3.6` 有明确参考/适配关系：check_sig、graph login_jump、OAuth authorize、state校验、QQLogin交换。已保留原许可和上游贡献说明，参见THIRD_PARTY_NOTICES。

## 酷狗/酷我网页签名

开发时参考的公开资源：

- https://staticssl.kugou.com/common/js/min/infSign.min.js
- https://staticssl.kugou.com/common/js/min/npm/getBaseInfo.min.js
- https://h5s.kuwo.cn/www/kw-www/dcc6cd7.js

公开可下载并不代表获得重许可权。酷我Secret计算与互联网早期流传的XOR/LCG字符串例程结构一致，但没有核实到原作者的许可。酷狗签名使用Python标准库MD5并未复制JS MD5函数体，但原本的具体移植实现也在本公开导出中排除，以免将不确定性包装成MIT授权。

`musicbox/web_auth.py` 现只提供调用接口。若要启用对应网页认证播放：

1. 自行取得你有权使用的独立适配实现，或部署有权使用的备用服务。
2. 本机Python模块应实现 `kugou_params(values, fields, now_ms=None)` 和/或 `kuwo_secret(value, seed=None)`。
3. 用 `MUSICBOX_WEB_AUTH_MODULE` 显式设置模块名。模块代码会在本机执行，应像其他依赖一样审查。项目不自动下载安装模块。

无模块时返回可理解的“不含该实现”错误；官方网页登录、队列、会员查询等仍保留，涉及网页签名的酷狗/酷我登录播放不能承诺开箱可用。这项功能差异是发布前必须阅读的限制。

## 独立备用服务

- https://github.com/L-1124/QQMusicApi ：Python库，核实发布版GPLv3+。本项目只提供链接，不包含其代码。需要你自己的本机HTTP桥接，接口路径不虚构。
- https://github.com/MakcRe/KuGouMusicApi ：独立Node服务，未打包其代码。
- https://github.com/qyhqiu/kuwoMusicApi ：独立服务，未打包其代码。

本项目不以“独立服务”作为自动免除许可证义务的保证。修改/分发服务或桥接代码时必须自行核对实际许可证和组合方式。

## 公开常量和合成测试

平台客户端ID、Cookie字段名称、官方HTTPS回调路径是业务协议标识，不是个人账号密钥。测试中的SESSION、WEB_TOKEN、SYNTHETIC等均为合成输入。发布扫描不把所有名字含token的变量当成泄漏，也不因为是公开常量就宣称其使用获得平台许可。

## 不收录的材料

真实账号数据库和密钥、浏览器profile、带授权参数日志、HAR、原生dump、官方JS参考文件、封面/音频/歌词文件、用户扫码截图均不进入此导出。历史对话、机器用户名与工作区绝对路径也不进入公开说明。
