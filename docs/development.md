# 目录与构建说明

仓库按源码、运行文件、构建工具和文档分组：

| 目录 | 内容 |
| --- | --- |
| `src/launcher/` | WinForms 启动器、界面、房间管理与更新界面的 C# 源码 |
| `src/sdk/` | 本地 UID 登录 SDK 的 C++ 源码和导出定义 |
| `src/runtime/` | 运行脚本、协议数据、图标、manifest 与 `backup-tool/` 采集工具 |
| `build/` | 编译、运行目录组装、更新包构建及构建配置 |
| `tests/` | 既有回归测试与统一测试入口 |
| `docs/` | 使用说明、更新记录和发布检查 |
| `tools/` | 需要手动运行的维护脚本 |
| `dist/` | 本地构建产物，分为 `cn/`、`en/`、`backup-tool/`、`bin/` 与 `reports/`；不提交到 Git |

## 构建

在仓库根目录运行：

```powershell
.\build.cmd
```

需要 Windows x64、Python 3.12 或以上、.NET Framework 编译器，以及带 C++ x64 工具链的 Visual Studio Build Tools。Python 依赖可通过根目录的 `requirements.txt` 安装。

默认产物按用途整理到 `dist/` 下（不堆在产物根目录）：

- `dist/cn/`：中文国服启动器完整运行目录（根目录保留 `OverFieldLauncher.exe`、`README.md`、`app/`、`backup-tool/`、`docs/`，内部脚本与 `LocalUidSDK.dll` 放在 `app/`）
- `dist/en/`：英文 Steam 国际服启动器完整运行目录（根目录保留 `OverFieldLauncher.exe`、英文 `README.md`、`app/`、`backup-tool/`、`docs/`，内部脚本与 `LocalUidSDK.dll` 放在 `app/`）
- `dist/backup-tool/`：独立 PC 采集工具目录
- `dist/bin/`：原始编译产物（`OverFieldLauncher.exe`、`OverFieldLauncher-EN.exe`、`LocalUidSDK.dll`、`SHA256SUMS.txt`）
- `dist/reports/`：构建报告 `build-report.json`、日志 `logs/` 与可复现快照 `intermediate/`

该入口编译中英文启动器和 SDK、组装运行目录、重复构建并检查哈希一致性，再执行构建验证。也可以指定输出目录、日志目录和 Python：

```powershell
.\build.cmd 'C:\OverFieldLab\build' 'C:\OverFieldLab\logs' python
```

`dist/cn/` 与 `dist/en/` 是开发用组件目录；完整便携包还需要原有的运行环境及服务资源模板。源码构建不会自动下载游戏或服务资源，也不会修改现有安装。

## 测试

```powershell
python -B -X utf8 tests/run_tests.py
python -B -X utf8 tests/run_tests.py --system-only
```

第一条运行主测试及采集工具测试，需要 `cramjam`、`protobuf` 等解码依赖。若本地已有采集工具的便携 Python，可使用 `src/runtime/backup-tool/tools/python/python.exe` 运行第一条。第二条运行需要 `mitmproxy` 的启动器测试，使用安装了该依赖的系统 Python。测试入口会显式加入源码路径，兼容便携 Python 的隔离导入模式。

## 发布目录

组装后的运行目录（如 `dist/cn/`、`dist/en/`）仅在根目录保留 `OverFieldLauncher.exe`、`README.md`、`app/`、`backup-tool/`、`docs/`（用户安装后会额外存在 `profiles/`、`runtime/`、`server-kit/`、`runtime-python/` 等运行/用户目录）；内部脚本、`LocalUidSDK.dll` 及更新元数据收敛在 `app/`，兼容旧版扁平更新包结构。

签名打包入口为 `build/build_update_assets.py`，支持分组后的仓库或已组装的运行目录。发布前按 [检查清单](RELEASE-CHECKLIST.md) 核对。当前准备发布版本为 v1.5.0。
