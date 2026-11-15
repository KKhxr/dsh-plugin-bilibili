# 使用文档

插件向 DSH agent 注册 4 个工具。安装与注册见 [install.md](install.md)。

## 1. 工具一览

| 工具 | 用途 | 主要返回 |
|---|---|---|
| `bili_user_stat` | 账号概览 | 昵称、粉丝、关注、投稿数 |
| `bili_user_videos` | UP 主投稿列表（分页） | bvid、标题、时长、发布时间、播放、评论、弹幕 |
| `bili_video_info` | 单视频详情 | 播放、点赞、投币、收藏、分享、弹幕、评论 |
| `bili_search_user` | 关键词搜用户 | mid、昵称、签名、粉丝、投稿数、等级、直播间 |

所有工具返回一段 JSON 文本，agent 会读取它再汇总。

## 2. 在 Web GUI 里调用

直接用自然语言：

```text
调用 bili_user_stat 查询 uid 2 的粉丝数
调用 bili_video_info 查询 bvid BV1GJ411x7h7 的播放量
调用 bili_user_videos 查询 uid 2 的第一页投稿，按 pubdate 排序
调用 bili_search_user 搜索关键词「老番茄」
```

也可以一次完成完整任务：

```text
读取 B 站账号 uid 2 的全部公开信息：
先 bili_user_stat，再用 bili_user_videos 翻完所有页，最后逐条 bili_video_info，
汇总粉丝、播放、互动和内容主题。
```

```text
对比 BV1GJ411x7h7 和 BV1JWW36fEvQ 的播放、点赞、收藏、投币、分享，做成表格。
```

建议在 prompt 里明确：

- 「只调用一次」避免 agent 重试。
- 「不要改写工具返回的数据」拿原始 JSON。

## 3. 工具参数

### 3.1 `bili_user_stat`

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `uid` | string | 是 | B 站数字 UID，例如 `"2"` |

返回示例：

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

### 3.2 `bili_user_videos`

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `uid` | string | 是 | B 站数字 UID |
| `page` | number | 否 | 页码 1–100，默认 1 |
| `order` | string | 否 | `pubdate`（默认）/ `click` / `stow` |
| `ps` | number | 否 | 每页条数 1–50，默认 30 |

返回示例（节选）：

```json
{
  "mid": 2,
  "pn": 1,
  "ps": 50,
  "order": "pubdate",
  "total": 43,
  "items": [
    {
      "bvid": "BV1ujNV6qEXg",
      "aid": 116908221270151,
      "title": "帮不帮？",
      "author": "碧诗",
      "length": "00:22",
      "created": 1783878096,
      "play": 809513,
      "comment": 9542,
      "danmaku": 2852
    }
  ]
}
```

翻页：`total` 是总稿件数，按 `ps=50` 计算页数 `ceil(total / 50)`，页码最大 100。

### 3.3 `bili_video_info`

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `bvid` | string | 是 | 形如 `BV1GJ411x7h7` |

返回示例：

```json
{
  "aid": 80433022,
  "bvid": "BV1GJ411x7h7",
  "title": "【官方 MV】Never Gonna Give You Up - Rick Astley",
  "pubdate": 1577835803,
  "duration": 213,
  "owner": { "mid": 486906719, "name": "索尼音乐中国" },
  "stat": {
    "view": 108396203,
    "like": 2976635,
    "coin": 1293287,
    "favorite": 1566199,
    "share": 506800,
    "danmaku": 151896,
    "reply": 240928
  }
}
```

`duration` 单位为秒，`pubdate` 为 Unix 时间戳。

### 3.4 `bili_search_user`

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `keyword` | string | 是 | 搜索词，最多 100 字 |

返回示例（节选）：

```json
{
  "keyword": "老番茄",
  "page": 1,
  "total": 1000,
  "items": [
    {
      "mid": 546195,
      "uname": "老番茄",
      "usign": "新浪微博：_老番茄_",
      "fans": 20880297,
      "videos": 683,
      "level": 6,
      "room_id": 21852
    }
  ]
}
```

## 4. 常见任务模板

| 目标 | Prompt |
|---|---|
| 账号体检 | `读取 B 站账号 uid 2 的粉丝、投稿数、总播放和互动数据，给出结论` |
| 全部投稿导出 | `用 bili_user_videos 翻完 uid 2 的所有页，按发布时间列出全部稿件` |
| 单视频对比 | `对比两个 bvid 的播放、点赞、收藏、投币、分享，做成 Markdown 表格` |
| 找同类账号 | `用 bili_search_user 搜索「AI短剧」，列出粉丝最多的 5 个账号` |
| 选题分析 | `把 uid 2 的全部视频按播放排序，总结高播放标题的共同点` |

## 5. CLI / headless

未安装进 profile 时，用 `--patch` 临时加载：

```bash
dsh --profile headless --patch ./cordis.yml "调用 bili_user_stat 查询 uid 2 的粉丝数"
```

`--json` 输出机器可读事件流：

```bash
dsh --profile headless --patch ./cordis.yml --json "读取 uid 2 的全部投稿"
```

`cordis.yml` 里的 `name: './index.mjs'` 相对 patch 文件定位，所以不需要先安装成包。

## 6. 错误处理

| 返回/现象 | 原因 | 处理 |
|---|---|---|
| `未在 .env 中配置 BILI_COOKIE` | 没有 `.env` 或 Cookie 为空 | 复制 `.env.example` 并填写 |
| `Wbi 签名异常或 Cookie 失效...` | Cookie 失效 / 风控 | 换 Cookie；投稿接口建议用账号 Cookie |
| `TLS 指纹拦截（412）...` | TLS / 来源页风控 | 插件自动重试 2 次；仍失败换 Cookie 或稍后再试 |
| `请求频率过高（-799）` | 请求太快 | 等 10 秒自动重试一次 |
| `资源不存在（-404）...` | uid / bvid 不存在 | 检查参数 |
| `uid 必须是纯数字` 等 | 参数校验失败 | 按提示修正 |
| 120 秒工具超时 | 首次自动安装依赖或网络卡住 | 先 `pip install -r scripts/requirements.txt` |
| `找不到可用的 Python` | PATH 无 Python | 安装 3.10.11 或设置 `BILI_PYTHON` |

## 7. 注意事项

- 限速：相邻 B 站请求间隔 ≥ 2 秒，宿主端串行化所有工具调用；批量查 10 条视频详情约 25–30 秒。
- Cookie 只保存在插件根目录 `.env`；日志只输出项数和是否存在 `SESSDATA` / `buvid3`。
- 数据范围：只查公开数据；标签、简介、审核状态、私密视频不在当前 4 个工具范围内。
- 数据时效：每次实时请求，播放/点赞/粉丝数会变化。
- 改 `.env` 立即生效（每次调用新起 Python 进程）；改代码需重启 DSH。
