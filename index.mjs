// dsh-plugin-bilibili：B 站公开数据查询工具（宿主端）。
// 架构固定：Node 宿主端只负责注册工具与 spawn Python；所有 HTTP 请求在 scripts/bili.py。
import { spawn } from 'node:child_process'
import { existsSync } from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { defineTool } from '@deepseek-ai/dsh-tools'

export const name = 'bilibili'
export const inject = ['tools']

const PLUGIN_ROOT = path.dirname(fileURLToPath(import.meta.url))
const SCRIPT_PATH = path.join(PLUGIN_ROOT, 'scripts', 'bili.py')
const REQUIREMENTS_PATH = path.join(PLUGIN_ROOT, 'scripts', 'requirements.txt')

const PROBE_TIMEOUT_MS = 15_000
const ACTION_TIMEOUT_MS = 90_000
const PIP_TIMEOUT_MS = 90_000
const MAX_STDOUT_BYTES = 8 * 1024 * 1024
const MAX_STDERR_BYTES = 1024 * 1024

// Windows 上 python3 可能是应用商店占位符，python3.10 也不一定在 PATH，按顺序实测版本。
const PYTHON_CANDIDATES = [
  ...(process.env.BILI_PYTHON ? [{ command: process.env.BILI_PYTHON, prefix: [] }] : []),
  { command: 'python3.10', prefix: [] },
  { command: 'python3', prefix: [] },
  { command: 'python', prefix: [] },
  { command: 'py', prefix: ['-3.10'] },
  // 桌面应用常从 Explorer 启动，PATH 可能落后于 Python 安装，补上 Windows 默认安装位置。
  ...(process.env.LOCALAPPDATA
    ? [{ command: path.join(process.env.LOCALAPPDATA, 'Programs', 'Python', 'Python310', 'python.exe'), prefix: [] }]
    : []),
  { command: 'C:\\Python310\\python.exe', prefix: [] },
  { command: 'C:\\Program Files\\Python310\\python.exe', prefix: [] },
].filter((candidate) => !path.isAbsolute(candidate.command) || existsSync(candidate.command))

const RENDER = (_args, value) => [{ type: 'text', text: JSON.stringify(value, null, 2) }]

const USER_STAT_SCHEMA = {
  type: 'object',
  additionalProperties: false,
  properties: {
    uid: { type: 'integer', required: true },
    mid: { type: 'integer', required: true },
    name: { oneOf: [{ type: 'string' }, { type: 'null' }] },
    follower: { type: 'integer', required: true },
    following: { type: 'integer' },
    archive_count: { oneOf: [{ type: 'integer' }, { type: 'null' }] },
  },
}

const USER_VIDEOS_SCHEMA = {
  type: 'object',
  additionalProperties: false,
  properties: {
    mid: { type: 'integer', required: true },
    pn: { type: 'integer', required: true },
    ps: { type: 'integer', required: true },
    order: { type: 'string', required: true },
    total: { type: 'integer', required: true },
    items: {
      type: 'array',
      required: true,
      items: {
        type: 'object',
        additionalProperties: false,
        properties: {
          bvid: { type: 'string', required: true },
          aid: { type: 'integer' },
          title: { type: 'string' },
          author: { type: 'string' },
          length: { type: 'string' },
          created: { type: 'integer' },
          play: { type: 'integer' },
          comment: { type: 'integer' },
          danmaku: { type: 'integer' },
        },
      },
    },
  },
}

const VIDEO_INFO_SCHEMA = {
  type: 'object',
  additionalProperties: false,
  properties: {
    aid: { type: 'integer', required: true },
    bvid: { type: 'string', required: true },
    title: { type: 'string' },
    pubdate: { type: 'integer' },
    duration: { type: 'integer' },
    owner: {
      type: 'object',
      additionalProperties: false,
      properties: {
        mid: { type: 'integer' },
        name: { type: 'string' },
      },
    },
    stat: {
      type: 'object',
      additionalProperties: false,
      properties: {
        view: { type: 'integer' },
        like: { type: 'integer' },
        coin: { type: 'integer' },
        favorite: { type: 'integer' },
        share: { type: 'integer' },
        danmaku: { type: 'integer' },
        reply: { type: 'integer' },
      },
    },
  },
}

const SEARCH_USER_SCHEMA = {
  type: 'object',
  additionalProperties: false,
  properties: {
    keyword: { type: 'string', required: true },
    page: { type: 'integer', required: true },
    total: { type: 'integer', required: true },
    items: {
      type: 'array',
      required: true,
      items: {
        type: 'object',
        additionalProperties: false,
        properties: {
          mid: { type: 'integer', required: true },
          uname: { type: 'string' },
          usign: { type: 'string' },
          fans: { type: 'integer' },
          videos: { type: 'integer' },
          level: { type: 'integer' },
          room_id: { type: 'integer' },
        },
      },
    },
  },
}

/**
 * Spawn one process and collect bounded stdout/stderr.
 * @returns {Promise<{ code: number, stdout: string, stderr: string }>}
 */
function runProcess(command, args, options = {}) {
  const { input, timeoutMs = ACTION_TIMEOUT_MS, cwd = PLUGIN_ROOT } = options
  return new Promise((resolve, reject) => {
    let child
    // 不要给子进程设 PYTHONUTF8：curl_cffi 的 libcurl 在 UTF-8 模式下打开非 ASCII 路径的
    // certifi CA 文件会报 curl(77)。中文输出由 bili.py 自己 reconfigure stdout 解决。
    const childEnv = { ...process.env, PYTHONIOENCODING: 'utf-8' }
    delete childEnv.PYTHONUTF8
    try {
      child = spawn(command, args, {
        cwd,
        stdio: ['pipe', 'pipe', 'pipe'],
        windowsHide: true,
        env: childEnv,
      })
    } catch (error) {
      reject(new Error(`无法启动 ${command}：${error.message}`))
      return
    }

    let stdout = ''
    let stderr = ''
    let stdoutBytes = 0
    let stderrBytes = 0
    let settled = false
    let killedByTimeout = false

    const finish = (result) => {
      if (settled) return
      settled = true
      clearTimeout(timer)
      resolve(result)
    }
    const fail = (error) => {
      if (settled) return
      settled = true
      clearTimeout(timer)
      reject(error)
    }

    const timer = setTimeout(() => {
      killedByTimeout = true
      try { child.kill('SIGKILL') } catch { /* already exited */ }
    }, timeoutMs)
    if (typeof timer.unref === 'function') timer.unref()

    child.stdout.setEncoding('utf8')
    child.stderr.setEncoding('utf8')
    child.stdout.on('data', (chunk) => {
      if (stdoutBytes >= MAX_STDOUT_BYTES) return
      stdoutBytes += Buffer.byteLength(chunk)
      stdout += chunk
    })
    child.stderr.on('data', (chunk) => {
      if (stderrBytes >= MAX_STDERR_BYTES) return
      stderrBytes += Buffer.byteLength(chunk)
      stderr += chunk
    })
    child.stdout.on('error', () => {})
    child.stderr.on('error', () => {})

    child.on('error', (error) => {
      if (error.code === 'ENOENT') {
        fail(new Error(`找不到 Python 解释器「${command}」，请安装 Python 3.10.11 并加入 PATH`))
      } else {
        fail(new Error(`Python 子进程错误：${error.message}`))
      }
    })
    child.on('close', (code, signal) => {
      if (killedByTimeout) {
        fail(new Error(`Python 子进程超过 ${Math.round(timeoutMs / 1000)} 秒未结束，已终止`))
        return
      }
      finish({
        code: code ?? (signal ? 1 : 0),
        signal,
        stdout,
        stderr,
      })
    })

    if (input !== undefined) {
      child.stdin.on('error', () => {})
      child.stdin.end(typeof input === 'string' ? input : JSON.stringify(input))
    } else {
      child.stdin.end()
    }
  })
}

/** Probe one interpreter candidate; resolve the first Python >= 3.10. */
async function probePython(candidate) {
  try {
    const result = await runProcess(
      candidate.command,
      [...candidate.prefix, '-c', 'import sys; print(".".join(map(str, sys.version_info[:3])))'],
      { timeoutMs: PROBE_TIMEOUT_MS },
    )
    if (result.code !== 0) return null
    const version = result.stdout.trim()
    const [major, minor] = version.split('.').map(Number)
    if (major !== 3 || !(minor >= 10)) return null
    return { ...candidate, version }
  } catch {
    return null
  }
}

export function createBilibiliRuntime(ctx) {
  const log = (level, message) => {
    const text = `[bilibili] ${message}`
    try {
      const logger = ctx?.logger
      if (logger && typeof logger[level] === 'function') logger[level](text)
      else if (level === 'error' || level === 'warn') process.stderr.write(text + '\n')
    } catch {
      // 日志失败不能影响工具调用
    }
  }

  let pythonPromise
  let dependencyPromise
  let queue = Promise.resolve()

  const resolvePython = () => {
    if (pythonPromise === undefined) {
      pythonPromise = (async () => {
        const failures = []
        for (const candidate of PYTHON_CANDIDATES) {
          const resolved = await probePython(candidate)
          if (resolved) {
            log('info', `Python 解释器：${resolved.command}（${resolved.version}）`)
            return resolved
          }
          failures.push(candidate.command)
          log('warn', `解释器候选 ${candidate.command} 不可用（需要 Python >= 3.10）`)
        }
        throw new Error(
          '找不到可用的 Python（需要 3.10.11）。请安装后确认 `python --version` 可运行，'
          + '或设置环境变量 BILI_PYTHON 指向 python.exe。'
          + `已尝试：${failures.join('、')}`,
        )
      })()
      pythonPromise.catch(() => { pythonPromise = undefined })
    }
    return pythonPromise
  }

  const checkDependencies = async (python) => {
    const result = await runProcess(
      python.command,
      [...python.prefix, '-c', 'import curl_cffi, dotenv'],
      { timeoutMs: PROBE_TIMEOUT_MS },
    )
    return result.code === 0
  }

  const installDependencies = async (python) => {
    const attempts = [
      [...python.prefix, '-m', 'pip', 'install', '-q', '-r', REQUIREMENTS_PATH],
      [...python.prefix, '-m', 'pip', 'install', '-q', '--user', '-r', REQUIREMENTS_PATH],
    ]
    let lastStderr = ''
    for (const args of attempts) {
      log('warn', `缺少 Python 依赖，执行：pip ${args.slice(2).join(' ')}`)
      const result = await runProcess(python.command, args, { timeoutMs: PIP_TIMEOUT_MS })
      lastStderr = result.stderr || lastStderr
      if (result.code === 0 && (await checkDependencies(python))) return
    }
    throw new Error(
      '缺少 Python 依赖 curl_cffi / python-dotenv，自动安装失败。'
      + `请在插件目录执行：python -m pip install -r scripts/requirements.txt\n${lastStderr.slice(-800)}`,
    )
  }

  const ensureDependencies = () => {
    if (dependencyPromise === undefined) {
      dependencyPromise = (async () => {
        const python = await resolvePython()
        if (await checkDependencies(python)) return python
        await installDependencies(python)
        if (!(await checkDependencies(python))) {
          throw new Error('Python 依赖安装后仍无法导入 curl_cffi/dotenv')
        }
        return python
      })()
      dependencyPromise.catch(() => { dependencyPromise = undefined })
    }
    return dependencyPromise
  }

  /** Serialize calls: each Python process has its own rate limiter, so the host enforces one at a time. */
  const enqueue = (task) => {
    const run = queue.then(task, task)
    queue = run.then(() => {}, () => {})
    return run
  }

  const callPython = (action, args) => enqueue(async () => {
    const python = await ensureDependencies()
    const result = await runProcess(
      python.command,
      [...python.prefix, SCRIPT_PATH, '--action', action],
      { input: JSON.stringify(args ?? {}), timeoutMs: ACTION_TIMEOUT_MS, cwd: PLUGIN_ROOT },
    )

    const stderrTail = result.stderr.trim().slice(-1200)
    if (result.code !== 0) {
      throw new Error(
        `bili ${action} 的 Python 进程退出码 ${result.code}`
        + (stderrTail ? `：${stderrTail}` : ''),
      )
    }

    let payload
    try {
      payload = JSON.parse(result.stdout.trim())
    } catch {
      throw new Error(
        `bili ${action} 的输出不是 JSON：${result.stdout.slice(0, 500) || '(空)'}`
        + (stderrTail ? `\nstderr：${stderrTail}` : ''),
      )
    }
    if (payload === null || typeof payload !== 'object') {
      throw new Error(`bili ${action} 的 Python 输出不是 JSON 对象`)
    }
    if (payload.code !== 0) {
      const message = typeof payload.message === 'string' && payload.message ? payload.message : `B 站错误码 ${payload.code}`
      throw new Error(message)
    }
    if (payload.data === undefined) {
      throw new Error(`bili ${action} 的 Python 输出缺少 data 字段`)
    }
    return payload.data
  })

  return { callPython, log, resolvePython }
}

export function apply(ctx) {
  const runtime = createBilibiliRuntime(ctx)
  const { callPython, log } = runtime

  log('info', `bilibili 插件已加载：${PLUGIN_ROOT}`)

  ctx.tools.register(defineTool({
    name: 'bili_user_stat',
    description:
      '查询 B 站账号的粉丝数、关注数和投稿数（公开数据）。'
      + 'uid 是 B 站用户数字 ID，例如 2 是 bilibili 官方账号。'
      + '返回 uid、mid、昵称 name、粉丝数 follower、关注数 following、投稿数 archive_count。',
    parameters: {
      uid: {
        type: 'string',
        required: true,
        description: 'B 站用户数字 ID，例如 "2"（也接受纯数字的 number 形式）。',
      },
    },
    output: { schema: USER_STAT_SCHEMA, render: RENDER },
    timeoutMs: 120_000,
    isConcurrencySafe: () => false,
    execute: (args) => callPython('bili_user_stat', args),
  }))

  ctx.tools.register(defineTool({
    name: 'bili_user_videos',
    description:
      '查询 B 站 UP 主的投稿列表（一页）。需要 .env 中的 BILI_COOKIE。'
      + '可按发布时间 pubdate、播放量 click、收藏量 stow 排序，page 从 1 开始（最大 100），ps 默认 30、最大 50。'
      + '返回该账号总稿件数 total 与本页 items（bvid、aid、标题、时长、发布时间、播放、评论、弹幕）。',
    parameters: {
      uid: {
        type: 'string',
        required: true,
        description: 'B 站用户数字 ID，例如 "2"。',
      },
      page: {
        type: 'number',
        description: '页码，从 1 开始，最大 100。默认 1。',
      },
      order: {
        type: 'string',
        enum: ['pubdate', 'click', 'stow'],
        description: '排序：pubdate 按发布时间，click 按播放量，stow 按收藏量。默认 pubdate。',
      },
      ps: {
        type: 'number',
        description: '每页条数，1 到 50，默认 30。',
      },
    },
    output: { schema: USER_VIDEOS_SCHEMA, render: RENDER },
    timeoutMs: 120_000,
    isConcurrencySafe: () => false,
    execute: (args) => callPython('bili_user_videos', args),
  }))

  ctx.tools.register(defineTool({
    name: 'bili_video_info',
    description:
      '查询 B 站单条视频的详情（公开数据）。bvid 形如 BV1GJ411x7h7。'
      + '返回标题、UP 主、发布时间、时长，以及播放、点赞、投币、收藏、分享、弹幕、评论数据。',
    parameters: {
      bvid: {
        type: 'string',
        required: true,
        description: '视频 BV 号，例如 "BV1GJ411x7h7"。',
      },
    },
    output: { schema: VIDEO_INFO_SCHEMA, render: RENDER },
    timeoutMs: 120_000,
    isConcurrencySafe: () => false,
    execute: (args) => callPython('bili_video_info', args),
  }))

  ctx.tools.register(defineTool({
    name: 'bili_search_user',
    description:
      '按关键词搜索 B 站用户（第一页）。需要 .env 中的 BILI_COOKIE。'
      + '返回匹配账号的 mid、昵称 uname、签名 usign、粉丝数 fans、投稿数 videos、等级 level、直播间 room_id。',
    parameters: {
      keyword: {
        type: 'string',
        required: true,
        description: '搜索关键词，例如 "老番茄"，最多 100 字。',
      },
    },
    output: { schema: SEARCH_USER_SCHEMA, render: RENDER },
    timeoutMs: 120_000,
    isConcurrencySafe: () => false,
    execute: (args) => callPython('bili_search_user', args),
  }))
}

export default { name, inject, apply }
