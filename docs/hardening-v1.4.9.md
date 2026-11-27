# 本地加固版 v1.4.9

本页描述 `lab/v1.3.0-hardening` 分支在官方 v1.4.9 基础上的本地加固。它不是上游已合并版本，也不代表已经发布。

## 客户端隔离

- 源游戏目录只读，私有副本位于 `profiles/<mode>/client`。
- `client-ready.json` 使用 schema 2，记录源指纹、副本哈希、适配器和 Junction 目标。
- marker 上限 16 MB，适配约 9184 个文件的真实客户端。
- 只有白名单资源目录可 Junction；未知可写目录复制到私有副本。
- QuickSDK 只接受已验证的 GameAssembly 哈希、私网地址和本地 UID。

## 进程与状态

- 服务身份使用 PID、可执行路径和 Windows FILETIME。
- 主状态损坏时保留 `*.corrupt-*.json`；移动失败时复制证据后再删除不可信主文件。
- QuickSDK 全量缓存校验约 68 秒，外层启动等待有界 180 秒。
- 服务停止只终止身份记录匹配的进程树。

## 导入与备份

- 新 profile 只复制 schema，不继承账号。
- SQLite 导入使用临时库、完整性检查、事务和原子发布。
- 家园、家具数量、角色、装备、外观、队伍和基础信息可恢复。
- `verify_backup.py` 提供只读完整性报告；不完整包明确标记，不静默补假数据。
- 物品数量拒绝负数、未知配置、溢出和错误 tag；重复装备/宠物实例、of-ps 备份与邮件附件在写入前共用同一校验。
- 数据库提交后崩溃与外源发布失败通过 journal 恢复；失败不会覆盖已有账号目录。
- QuickSDK、marker/Junction、ZIP/SQLite 模糊、更新器原子失败和进程树清理均有离线失败矩阵。

## 采集与更新

- 保留分段采集，检测 TCP 缺口和未解析尾部。
- 摘要和 GUI 提示备份可能不完整，仍允许保存已解码 ZIP。
- 更新器校验签名、资产哈希、版本、edition 和 staging 边界。
- CN/EN 启动器与 LocalUidSDK.dll 使用可复现构建。

## 验证

- `build_all.bat`：CN/EN/SDK 构建报告 `passed=true`。
- 产物哈希：`C:\OverFieldLab\runtime\launcher-build-final\SHA256SUMS.txt`；构建报告 `passed=true`，可复现构建 PASS。
- Python 全量回归：`394 tests OK (skipped=3)`；导入经济恢复另有 `13 tests OK`，失败矩阵 `18 tests OK`。联合证据见 `C:\OverFieldLab\docs\VERIFICATION.md`。

## 限制

- 二进制未签名。
- Defender 实时保护未启用，未进行有效恶意软件扫描。
- 不公开账号 ZIP、PCAP、凭据日志或数据库。
- 上游 PR #8 已于 2026-11-27 合并；本页记录的是后续本地加固，不代表上游已包含这些改动。
