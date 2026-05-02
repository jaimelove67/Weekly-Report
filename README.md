# Weekly GitHub Digest Workflow

这套工作流会每周自动抓取 GitHub 近 7 天里升温最快的一批项目，生成中文精炼说明，并把周报发到你的 Gmail。

## 当前能力

1. 用 GitHub Search API 抓取近 7 天的新热项目。
2. 读取仓库描述、主题、语言、星标和 README。
3. 生成周报文件：
   `output/weekly-github-digest.md`
   `output/weekly-github-digest.json`
4. 如果配置了 `OPENAI_API_KEY`，会生成更自然的中文精炼说明。
5. 如果配置了 Gmail 相关 Secrets，会在每周任务跑完后自动把周报发到邮箱。

## 关键文件

- `.github/workflows/weekly-github-digest.yml`
- `scripts/weekly_github_digest.py`
- `scripts/send_gmail_digest.py`

## 需要配置的 GitHub Secrets

### 生成摘要

- `OPENAI_API_KEY`
  可选。用于生成更自然的中文项目说明。

### 发送 Gmail

- `GMAIL_SMTP_USER`
  你的 Gmail 地址，例如 `yourname@gmail.com`
- `GMAIL_APP_PASSWORD`
  Gmail 应用专用密码，不是普通登录密码
- `GMAIL_TO`
  可选。收件邮箱地址；不填时默认发到 `GMAIL_SMTP_USER`

`GITHUB_TOKEN` 在 GitHub Actions 中默认可用，不需要手动配置。

## Gmail 设置方式

1. 给你的 Google 账号开启两步验证。
2. 在 Google 账号安全设置里创建一个 App Password。
3. 把这个 App Password 填进仓库的 `GMAIL_APP_PASSWORD` Secret。

说明：
Gmail SMTP 使用的是 `smtp.gmail.com:465`，脚本通过 SSL 登录发送。

## 工作流执行时间

工作流当前的 cron 是 `0 1 * * 1`，也就是：

- UTC 时间：每周一 01:00
- 中国标准时间：每周一 09:00

## 手动本地生成周报

```powershell
python scripts/weekly_github_digest.py `
  --days 7 `
  --top 10 `
  --min-stars 150 `
  --output output/weekly-github-digest.md `
  --json-output output/weekly-github-digest.json
```

## 手动本地预览邮件内容

```powershell
$env:GMAIL_SMTP_USER="yourname@gmail.com"
$env:GMAIL_TO="yourname@gmail.com"
python scripts/send_gmail_digest.py `
  --markdown output/weekly-github-digest.md `
  --json output/weekly-github-digest.json `
  --dry-run
```

## 实际发送逻辑

每次工作流运行时，会按下面顺序执行：

1. 生成周报 Markdown 和 JSON。
2. 如果 Gmail Secrets 已配置，就自动发邮件。
3. 把最新周报提交回仓库。

## 可调参数

- 想筛选更热门的项目：提高 `--min-stars`
- 想覆盖更多项目：提高 `--top`
- 想只看某个方向：追加 `--query-extra "language:TypeScript"` 或 `--query-extra "topic:ai"`
