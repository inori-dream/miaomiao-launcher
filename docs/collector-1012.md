# Windows 1.0.12 增强集成 / Collector integration

统一启动器内置中文（简体/繁体）、English、日本語、한국어四语界面，并支持 B站 / TapTap / Steam 全区服公服采集与私服导入。采集组件标记为 `1.0.12-launcher-integration`：择优融合新手体验和探索报告，保留现有采集底层，而不是直接覆盖原工具。

## 新手流程

1. 完整解压便携包，配置独立的公服启动器路径。
2. 点击“预检查 / Preflight”查看依赖、权限及诊断。它不安装软件，不开始捕获，不清理其他会话。
3. 点击开始采集；如缺管理员权限，会申请 UAC 并重新启动本版启动器。取消或检查失败不会启动游戏。
4. 捕获真正就绪后打开官方客户端。已经打开的游戏应退出后重新登录，以取得登录和主数据响应。
5. 按清单加载角色、穿搭、背包、装备、宠物、家园、邮件、图鉴和探索等界面；结束采集后等待导出并另存账号ZIP。

The preflight report includes English messages in the EN edition. Native diagnostic output keeps the system's original language. Capture is blocked on failed/unknown required dependencies; unknown output writability is a reminder, and actual writes are checked at startup.

## 完整性与探索报告

- `message_counts` / `total_packets` 表示见到的帧；`successful_decode_count` 及成功服务端消息计数表示解码成功，二者不能混用。
- `trailing_streams` / `trailing_bytes_count` 记录残缺流尾；单包和每方向解压预算限制内存膨胀。
- `exploration.json` / `exploration.txt` 放入账号ZIP。它们只记录成功服务端响应中的观测数据及来源，不假设所有地图已采全。
- 未采到的值为 null，不自动写0。场景快照为部分观测；跨流时间顺序未知时只做单调已观测合并，不宣称最新全量状态。
- 参考目录中的“地图3：声明水晶总数264、目录ID269”保留为 mismatch；总量未验证时不计算完成百分比。
- 完整探索证据留在报告，GUI仅传小型摘要，避免状态轮询重复大量ID。
- `database_imported=false`：本轮没有把探索直接写入Lolo存档，原始证据仍可供后续模型适配。不要把报告生成当成游戏内进度已恢复。

## 保留的接口

现有 GUI StopFlag / ReadyPath / 外部Python和TShark参数 / ServerIps 以及PID就绪校验保持。Steam地址及方向试探、混合Wi-Fi/RAW修正、原始包保存和已知登录凭据过滤保持。原有协议文件没有替换。

停止超时或进程状态未知时，保留停止信号和 stopping/error，不按PID强杀或虚报完成。关闭启动器只给本安装的采集工作进程发送协作停止；工作进程负责落盘、清理与导出。重新打开后可确认已完成产物。外部强杀、断电及所有实网环境仍不能由自动测试保证。

## 不同导入目标

默认启动器使用Lolo的 `of_user / of_game / of_game_basic` 与gzip JSON；独立 `import_to_ofps.py` 面向of-ps，不能互换SQL或直接覆盖数据库。

安卓第三方原包是另一种主动协议查询工具，不是Windows采集底层。其输出没有直接接入当前Lolo；见 [安卓原包说明](android-backup.md)。

## 发布与来源

便携包包含经校验的运行时，不自动下载或安装游戏和新依赖。保留原有第三方组件许可证及采集工具致谢。APK附件为用户提供的第三方原包，未修改或重签名。

自动化回归、界面与便携包验证用于检查已知契约，不是“全部无损备份”或对所有区服/客户端/代理环境的保证。
