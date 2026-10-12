# Changelog

## [4.0.0] - 2026-05-09

### Fixed
- **随机崩溃**：Waitress 替代 Flask dev server，消除线程安全问题
- **X-UI 数据缺失**：完整转发 Subscription-Userinfo、Profile-Title 等头
- **SSH 密码丢失**：改为 `username:password` 持久化存储

### Added
- `/Ay_Custom_Configs` 公开订阅端点
- `/health` 健康检查端点
- Client Sync 管理（systemd timer）
- Live Logs 实时日志查看
- Side Collections 独立 JSON + Base64 解码 + rename/visibility
- 单页 Login+Dashboard 合并

### Changed
- systemd unit 增加 `Restart=always`、`RestartSec=3`、`StartLimitIntervalSec=0`
- SSH 用户不再强制 `ayuser`

### Removed
- `login.html` 独立页面（合并到 dashboard）
- `Ay-Custom-Sub-Restarter` 默认禁用（面板控制）

### Migration
- `side_collection_urls` 自动迁移到 `Side_Collections.json`
- SSH 用户文件自动转换为新格式