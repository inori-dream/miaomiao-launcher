# 开放空间私服启动器

Windows 单机 / 局域网服务端管理启动器，支持本地 UID 登录、独立存档、公服账号数据采集与导入、房间和玩家管理。界面为原创天空蓝／海洋青动漫海边插画，不嵌入游戏截图或玩家数据。

当前版本 **v1.4.9**（2026-11-26）。历史变更见 [更新记录](RELEASE-NOTES.md)。

## 下载

所有版本都在 [Releases](https://github.com/inori-dream/miaomiao-launcher/releases/latest) 页面。**请下载 `.zip` 附件，不要用 Code → Download ZIP**——那个下的是源码，不能直接运行。

| 下载项 | 用途 |
| --- | --- |
| [OverFieldLauncher-CN-portable.zip](https://github.com/inori-dream/miaomiao-launcher/releases/latest/download/OverFieldLauncher-CN-portable.zip) | 中文国服启动器，约 154 MB |
| [OverFieldLauncher-EN-portable.zip](https://github.com/inori-dream/miaomiao-launcher/releases/latest/download/OverFieldLauncher-EN-portable.zip) | 英文 Steam 国际服启动器，约 154 MB |
| [OverField-Android-Backup-v3.1-third-party.apk](https://github.com/inori-dream/miaomiao-launcher/releases/latest/download/OverField-Android-Backup-v3.1-third-party.apk) | 安卓手机采集工具，约 3.9 MB |

两个 Windows 包**各自独立**，请分别解压到不同目录，不要互相覆盖。

PC 采集工具（账号备份／抓包解码）已经包含在 Windows 便携包的 `backup-tool/` 目录里，不需要另外下载。

发布页里还会看到 `launcher-update.json`、`launcher-update.sig` 和两个 `*-update.zip`。这些是启动器**自动更新用的**，不用手动下载。

## 如何使用

### 第一步：解压

右键「全部解压」到任意可写目录（例如 `D:\OverField-CN`）。**不要在压缩包里直接双击运行**，也不要解压到 `C:\Program Files` 这类需要管理员权限的位置。

包内已自带 Python 运行时、服务端程序和服务资源，不需要另行安装 Python。

### 第二步：启动

双击 `OverFieldLauncher.exe`。

首次启动会让选择游戏目录——点「更改目录」，选中你自己电脑上的游戏客户端安装目录。**游戏本体不随包分发，也不自动下载**，需要你自己从合法来源准备好。

### 第三步：本地 UID 登录（日常单机玩法）

不需要官服账号，不联网认证，直接在本机进游戏：

1. 在「单机模式」里填一个本地 UID，任意数字都可以。
2. 确认游戏目录已选好。
3. 点「启动单机游戏」。首次会复制一份独立客户端副本，需要等一会儿。
4. 游戏出现原渠道账号弹窗时**不用登录，直接关掉**，或点「进入游戏」即可进入本机服务。

### 公服账号采集（备份自己的官服数据）

1. 点左侧「公服采集」进入独立页面。
2. 点「开始采集」——PktMon 抓包需要管理员权限，会弹 UAC 确认。
3. 退出并重新打开你自己的原版官方客户端，正常登录进入场景，把需要备份的界面都打开一遍：角色、背包、装备、宠物、邮件、家园。
4. 点「结束采集」，等待解包。核对采集到的 UID 和存档冲突提示，再决定是否入库。
5. 采集期间**不要**启动私服副本或转发工具。

详细的运行依赖、产物说明和排查步骤见 [公服采集说明](docs/official-capture.md)。

### 局域网联机

局域网模式下 HTTP／UID 网关／日志端口分别是 19080／19100／19200。房间管理接口只监听本机，首次运行会生成管理密钥。

Windows 防火墙规则只放行同一局域网。**本项目是本地 UID 模式，不具备公网身份认证，不要暴露到互联网。**

### 自动更新

启动器启动时会在后台检查对应版本的正式版更新，自动下载并验签小型核心更新包；当游戏、服务和采集都空闲时倒计时安全切换并重启。

存档、配置、运行数据、游戏本体和安卓 APK 不在自动更新范围内。本地受管文件如果有改动，会暂停更新而不是覆盖。详见 [自动更新说明](docs/auto-update.md)。

> 旧版本首次需要先手动升级到 v1.4.0（第一个带更新模块的版本），之后才能由启动器自动获取更新。

## 主要事项

**数据与隐私**

- 存档、导入报告、管理密钥和日志都在 `profiles/solo` 或 `profiles/host`，运行状态在 `runtime/`。**不要上传这些目录，也不要把账号 ZIP、原始抓包或凭据日志公开分享。**
- 采集只处理你自己账号的数据。不需要向服主提供公服密码，也不需要提供 QQBot 的 token。
- 本项目不收集遥测，不把路径或账号信息发到线上。

**安全边界**

- 只在本机和局域网使用。没有公网身份认证，公网暴露等于把服务端交给任何人。
- 便携包里的 `server-kit/` 已包含服务端程序（`Lolo.exe`）、`Resource/`、`data/` 和数据库模板，开箱可用。请保持它原样，不要把它改造成对外公开的服务模板。
- 不要把他人的数据库当作公开模板。新建存档只复制数据库架构，不复制账号或进度。

**兼容性**

- 已验证 B 站 PC 版，以及 TapTap 下载的特定版本 QuickSDK PC 包（限定已验证版本与 GameAssembly 哈希）。其他渠道包或版本不保证兼容。
- 仅含 TapTap DLL 的未知布局不做适配。
- 安卓 APK 是**第三方制作的原包**，非本项目开发或修改，本仓库只做分发。它目前不能直接导入默认 Lolo 服务端。

**关闭启动器不等于关闭服务**

关闭窗口不会停止已经启动的服务端。请用界面上的停止按钮管理，或退出前确认服务已停。

**其他**

- 管理多个版本时，请把不同版本放进不同目录，避免更新互相干扰。
- 遇到「Profile operation failed」这类提示，通常是游戏目录选错、路径含特殊字符，或端口被占用。

## 致谢与声明

感谢各位大佬对服务端部分功能的实现，以及对账号数据采集、备份和解析工具的开发与分享。本项目的相关工作参考了 [BanterSR/Lolo](https://github.com/BanterSR/Lolo) 与 [byzp/of-ps](https://github.com/byzp/of-ps)，具体来源、版本与第三方许可证见 [组件声明](backup-tool/THIRD-PARTY.txt)。

特别感谢 **水音** 提供的采集工具，本项目的公服账号采集与数据解析工作从中受益。

**最终解释权归四川星期零信息科技有限公司。**

本项目为非官方社区工具，与上述公司不存在已确认的授权、合作或背书关系。以上声明不代表公司已授权本项目发布，不替代原游戏的用户协议、知识产权要求或各第三方组件的许可证；本工具自身的维护与缺陷处理由项目维护者负责。

---

开发者请看 [构建说明](docs/editions.md) 与 [发布复核结论](docs/release-audit.md)。测试位于 `tests/`，运行方法见仓库中的构建脚本与 `RELEASE-CHECKLIST.md`。
