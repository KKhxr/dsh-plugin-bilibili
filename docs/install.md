# 安装文档

## 0. 前置条件

1. DSH 0.2.x（验证版本 `0.2.1-rc.5`），Node.js >= 20。
2. Python **3.10.11**：

   ```bash
   python --version
   python -m pip install -r scripts/requirements.txt
   ```

   如果 `python` 不在 PATH，设置 `BILI_PYTHON` 指向 `python.exe`。
3. 插件根目录有 `.env`（见 `.env.example`）：

   ```dotenv
   BILI_COOKIE="SESSDATA=YOUR_SESSDATA; bili_jct=YOUR_BILI_JCT; buvid3=YOUR_BUVID3"
   ```

## 1. 推荐：`plugin_manager` 的 `install_bundle`

在 DSH 会话里让 agent 调用：

```json
{
  "action": "install_bundle",
  "target": "/absolute/path/to/dsh-plugin-bilibili"
}
```

`target` 可以是绝对路径、npm 包名、git 地址或 tarball。该调用需要 danger-full-access 或单次审批。

`install_bundle` 会：

1. 把包安装进当前 profile 的 `node_modules`；
2. 把 `dsh-plugin-bilibili` 写入 profile `package.json` 的 `dsh.profile.bundles`；
3. 组合 `cordis.patch.yml`，插入 `bilibili` 插件行。

读返回值：

- `application: applied`：已激活，可直接调用工具；
- `restart-required`：重启 DSH；
- `failed`：按 `nextStep` 排查。

> 如果当前 profile 的 `tool-plugin-manager` 被禁用，可以改用 GUI 的「设置 → 插件」页面安装，
> 或在 profile `cordis.patch.yml` 里启用该行后重启。

## 2. CLI 安装

```bash
dsh plugin --profile <profile> add /absolute/path/to/dsh-plugin-bilibili
```

`dsh plugin` 只是把参数转发给 profile 目录里的 pnpm，**不会**自动把包加进 bundles。还需要在
`~/.dsh/profiles/<profile>/package.json` 的 `dsh.profile.bundles` 数组里追加：

```json
"dsh-plugin-bilibili"
```

然后重启 DSH。

> **非 ASCII 路径**：pnpm 在部分 Windows 环境会把含中文/非 ASCII 的 `link:` 路径写成乱码。
> 如果安装后 profile 里出现 `宸ヤ綔鍖?` 之类的路径，把插件放到纯 ASCII 目录（例如
> `C:/dsh-plugins/dsh-plugin-bilibili`）再安装，或用 ASCII junction 指向真实源码目录。

## 3. 临时验证：`--patch`

不需要安装，也不会改 profile：

```bash
dsh --profile headless --patch ./cordis.yml "调用 bili_user_stat 查询 uid 2 的粉丝数"
```

`cordis.yml` 的 `name` 是 `./index.mjs`，相对 patch 文件定位。需要另起 Web 实例时把 `headless`
换成 `web`，并指定不冲突的端口。

## 4. 验证安装

在 GUI 或 CLI 里调用：

```text
调用 bili_user_stat 查询 uid 2 的粉丝数
```

预期返回：

```json
{
  "uid": 2,
  "mid": 2,
  "name": "碧诗",
  "follower": 1437776,
  "following": 430,
  "archive_count": 44
}
```

再用 `bili_video_info`、`bili_user_videos`、`bili_search_user` 各调一次，全部成功即安装完成。

## 5. 卸载

```json
{ "action": "remove_bundle", "target": "dsh-plugin-bilibili" }
```

或 CLI：

```bash
dsh plugin --profile <profile> remove dsh-plugin-bilibili
```

再从 `dsh.profile.bundles` 删除该项并重启。

## 6. 常见问题

| 现象 | 处理 |
|---|---|
| `找不到可用的 Python（需要 3.10.11）` | 安装 Python 3.10.11 并加入 PATH，或设 `BILI_PYTHON` |
| `未在 .env 中配置 BILI_COOKIE` | 在插件根目录创建 `.env` |
| `Wbi 签名异常或 Cookie 失效` | 换 `.env` 的 `BILI_COOKIE`，投稿接口建议用账号 Cookie |
| `TLS 指纹拦截（412）` | 确认 `curl_cffi` 已安装；插件会自动重试 |
| 工具调用 120 秒超时 | 先 `python -m pip install -r scripts/requirements.txt` |
| 安装后看不到工具 | 看 `install_bundle` 的 `application`；`restart-required` 就重启；确认 bundles 有 `dsh-plugin-bilibili` |
| 安装路径乱码 | 改用纯 ASCII 目录或 ASCII junction |
