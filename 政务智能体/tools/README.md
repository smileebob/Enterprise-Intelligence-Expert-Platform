# 政务实机录屏工具

`gov_record_flow.js` 用 Playwright 驱动真实 `/gov` 工作台，完成：

1. expert 登录并进入政务工作台；
2. 政策解读：填写问题、地区、事项日期，展示条款结果；
3. 公文起草：填写要素和事实材料，生成待审草稿；
4. 切换 kbadmin，进入待审草稿并通过；
5. 政策版本对照。

脚本只用于本地演示录制，不修改业务数据以外的内容；录制的数据库建议使用独立文件。

## 本地准备

```bash
# 1. 录制工具（放在 gitignore 的 _tmp 下，不提交 node_modules）
mkdir -p _tmp/gov_record && cd _tmp/gov_record
npm init -y
PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1 npm i playwright
export PLAYWRIGHT_BROWSERS_PATH="<仓库绝对路径>/_tmp/ms-playwright"
npx playwright install ffmpeg

# 2. 独立演示库
cd <仓库>/platform
export DATABASE_URL="sqlite:///./var/gov_record.db"
./.venv/Scripts/python.exe -c "import core.models; import domains.gov_office.models; from app.db import Base, engine; Base.metadata.create_all(engine)"
./.venv/Scripts/python.exe scripts/init_gov_demo.py

# 3. 后端 8000（另开终端）
export DATABASE_URL="sqlite:///./var/gov_record.db"
./.venv/Scripts/python.exe -m uvicorn main:app --host 127.0.0.1 --port 8000 --workers 1

# 4. 前端 5173（另开终端）
cd <仓库>/platform-web && npm run dev

# 5. 录制（把脚本复制到有 playwright 依赖的目录）
cp <仓库>/政务智能体/tools/gov_record_flow.js <仓库>/_tmp/gov_record/
cd <仓库>/_tmp/gov_record
RECORD=1 node gov_record_flow.js
```

视频输出在 `_tmp/gov_record/videos/*.webm`。当前脚本产出约 54.8 秒、1280×720 的连续录屏；已另存到 `申报材料/实机录屏-原始素材-54s.webm`，并合成为 `申报材料/1分钟方案演示视频-实机录屏版.mp4`（59.8 秒、1080p、带旁白/字幕/结束卡）。

## 注意

- 脚本会写入演示草稿并执行一次审核，只应指向独立演示库。
- 不在录制中展示国家秘密、个人信息、内部材料或真实密钥。
- 录屏是真实运行画面，但不是生产环境或机关试点证明。
