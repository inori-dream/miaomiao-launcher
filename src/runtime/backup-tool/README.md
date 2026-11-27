# 玩家数据备份工具

由用户提供的 Windows 备份工具包纳入本仓库。源码目录保留账号抓包、家园抓包、PCAP 解码脚本和协议定义，未复制便携 Python、TShark、VC 运行库，也不包含个人输出。

## 两种使用方式

- 便携版：使用单独发布的 `OverField-Backup-Windows.zip`，完整解压后按 `使用说明.txt` 操作。该附件保留原始上游工具和许可证。
- 源码版：提供 Python 3.13 x64、安装本目录 `requirements.txt` 的依赖，并安装 TShark。生成的协议代码与 Protobuf 7.35.1 配套；不要任意降级。可将便携版的 `tools/` 放到本目录，或显式指定依赖路径。

管理员 PowerShell 中运行（路径为示例，需要替换为本机实际路径）：

```powershell
.\capture.ps1 -Mode Account -PythonExe 'D:\Tools\Python\python.exe' -TSharkExe 'D:\Tools\Wireshark\tshark.exe'
.\capture.ps1 -Mode Gardens -PythonExe 'D:\Tools\Python\python.exe' -TSharkExe 'D:\Tools\Wireshark\tshark.exe'
```

拖放解码脚本也支持 `OVERFIELD_BACKUP_PYTHON`；解码器支持 `OVERFIELD_TSHARK`。不设置时仍使用原便携版的 `tools/` 路径。

## 归档与启动器衔接

账号模式完成后得到 `output/account_时间/export/`。启动器导入要求 ZIP 内为 `export/json/文件名_s2c_消息编号_消息名.json`，必须从 `export` 的上一级压缩 `export` 文件夹，而不是仅压缩里面的 JSON，也不要压缩整个 `output`。

推荐 PowerShell 命令（执行前将路径替换为本次输出位置）：

```powershell
Compress-Archive -LiteralPath '.\output\account_时间\export' -DestinationPath '.\output\account_时间\account-import.zip'
```

不要把 `capture.etl` 或 `capture.pcapng` 放入导入 ZIP。ZIP 仍包含个人数据，只用于本人本地导入，不能上传仓库。

家园模式导出 `SceneGardenData` 与索引，不是启动器账号导入格式；当前启动器没有独立家园导入接口，不应宣称能一键导入。

完整流程见仓库 `docs/login-and-data.md`。

## 安全与许可

仅备份本人账号，或经屋主许可、通过正常游戏拜访可见的家园资料。工具不会替用户登录，也不会绕过官服认证。不要公开 UID、好友资料、家园索引或原始抓包。

原始抓包保留登录流量。解码器仅跳过已知登录请求（1001、1003、2201），移除登录响应（1004）的 `client_log_server_token`；其他字段、未知消息和原始 PCAP 不保证脱敏。输出不能视为匿名数据。

第三方来源及许可见 `THIRD-PARTY.txt` 和 `proto_gen/`。其中存在 GPL 组件，保留许可证不等于所有公开发行义务已完成。原便携包的版本标注来自所附说明，未在线核实来源签名或下载校验和。

仓库修改：允许显式指定 Python/TShark；使用 UTF-8 BOM 以兼容 Windows PowerShell 5.1；补充使用、隐私和数据导入说明。未执行真实抓包、官服登录或下载行为。

测试：使用安装本目录依赖的 Python 执行仓库根目录 `test_backup_tool.py`，覆盖分帧、Snappy、登录请求剔除、登录响应令牌清理、双向流提取及家园导出。测试使用合成数据，不触发 PktMon。
