# Ay v2Ray Subscription v4.0.0

## ✨ 新特性

- **Waitress 生产服务器**：替代 Flask dev server，解决随机崩溃
- **X-UI 头完整传递**：Subscription-Userinfo、Profile-Title 等
- **Client Sync Timer**：通过 systemd timer 管理 syncClients
- **Live Logs**：实时查看服务日志
- **Side Collections 独立管理**：支持 rename、visibility、Base64 解码
- **SSH 密码持久化**：`username:password` 格式，重启不丢失
- **单页登录+仪表盘**：消除加载 overlay

## 🔗 新端点

| 端点 | 说明 |
|------|------|
| `https://<domain>:<port>/Ay_Custom_Configs` | 手动配置公开订阅 |
| `https://<domain>:<port>/health` | 健康检查 |

## 📦 迁移 v3 → v4

1. 备份 `config.json` 和 `Ay_SSH_Users`
2. 运行 `bash setup.sh`
3. 首次启动自动迁移：
   - `side_collection_urls` → `Side_Collections.json`
   - SSH 用户格式自动转换
4. 旧 `Ay-Custom-Sub-Restarter.timer` 默认禁用

## 🛠 菜单

| 选项 | 功能 |
|------|------|
| 1 | 安装 |
| 2 | 更新面板 |
| 3 | 重启主服务 |
| 4 | 配置 |
| 5 | 管理 Restarter |
| 6 | 管理 Client Sync |
| 7 | 查看实时日志 |
| 8 | 卸载 |