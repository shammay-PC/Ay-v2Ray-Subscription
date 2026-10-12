# Ay v2Ray Subscription v4.0.0

[完整 README 内容见项目根目录]

## 新特性
- 移除 Restarter timer，用 Waitress + 健康检查替代
- 转发 X-UI Subscription-Userinfo 等响应头
- `/Ay_Custom_Configs` 公开端点
- Client Sync 标签页（systemd timer）
- Live Logs 标签页
- Side Collections 独立 JSON + 缓存 + Rename/Visibility
- 持久化 SSH 密码（`username:password` 格式）
- 单页登录 + Dashboard
- Tailwind CSS + 3X-UI 风格

## 迁移 v3 → v4
1. 备份 `/root/ay-custom-sub/`
2. 运行 `aysub` → Update Panel
3. 自动迁移 `side_collection_urls` → `Side_Collections.json`
4. 自动迁移 `Ay_SSH_Users` 格式