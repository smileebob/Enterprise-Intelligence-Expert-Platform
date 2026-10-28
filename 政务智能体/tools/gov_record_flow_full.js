const { chromium } = require('playwright');
const BASE = 'http://127.0.0.1:5173';

async function fillDate(page, placeholder, value) {
  const input = page.getByPlaceholder(placeholder);
  await input.click();
  await page.waitForTimeout(250);
  await input.fill(value);
  await page.keyboard.press('Enter');
  await page.waitForTimeout(300);
}

async function login(page, username, password) {
  await page.goto(BASE + '/login');
  await page.getByPlaceholder('username').fill(username);
  await page.getByPlaceholder('password').fill(password);
  await page.getByRole('button', { name: /登\s*录/ }).click();
  await page.getByText('新建会话').first().waitFor({ timeout: 20000 });
  await page.waitForTimeout(800);
}

async function loginAuditor(page) {
  await page.goto(BASE + '/login');
  await page.getByPlaceholder('username').fill('auditor');
  await page.getByPlaceholder('password').fill('Audit@12345');
  await page.getByRole('button', { name: /登\s*录/ }).click();
  await page.getByText('安全审计台').first().waitFor({ timeout: 20000 });
  await page.waitForTimeout(600);
}

async function openGov(page) {
  await page.getByText('政务办公工作台').first().click();
  await page.getByRole('tab', { name: '政策解读' }).waitFor({ timeout: 20000 });
  await page.waitForTimeout(600);
}

async function logout(page) {
  await page.getByRole('button', { name: '退出' }).click();
  await page.getByPlaceholder('username').waitFor({ timeout: 15000 });
  await page.waitForTimeout(500);
}

(async () => {
  const record = process.env.RECORD === '1';
  const browser = await chromium.launch({ channel: 'msedge', headless: true });
  const context = await browser.newContext({
    viewport: { width: 1280, height: 720 },
    ...(record ? { recordVideo: { dir: 'videos_full', size: { width: 1280, height: 720 } } } : {}),
  });
  const page = await context.newPage();
  page.setDefaultTimeout(30000);
  try {
    // 1 登录进入工作台
    await login(page, 'expert', 'Expert@12345');
    await openGov(page);
    await page.waitForTimeout(3000);

    // 2 历史日期：2010 应选 2007 版
    const question = '政府信息公开申请不能当场答复的，答复期限是多少个工作日？';
    await page.getByPlaceholder('例如：本区企业申请高技能人才培训补贴需要哪些条件和材料？').fill(question);
    await page.getByPlaceholder('例如：杭州市余杭区', { exact: true }).fill('全国');
    await fillDate(page, '选择事项发生或适用日期', '2010-01-01');
    await page.getByRole('button', { name: '开始解读' }).click();
    await page.getByText('15个工作日内予以答复').first().waitFor({ timeout: 40000 });
    await page.waitForTimeout(4000);

    // 3 缺地区追问后恢复
    await page.getByPlaceholder('例如：杭州市余杭区', { exact: true }).fill('');
    await page.getByRole('button', { name: '开始解读' }).click();
    await page.getByText('缺少必要条件，暂不生成结论').waitFor({ timeout: 20000 });
    await page.waitForTimeout(3000);
    await page.getByPlaceholder('例如：杭州市余杭区', { exact: true }).fill('全国');
    await page.getByRole('button', { name: '开始解读' }).click();
    await page.getByText('15个工作日内予以答复').first().waitFor({ timeout: 30000 });
    await page.waitForTimeout(3000);

    // 4 早于旧版生效日：拒答
    await fillDate(page, '选择事项发生或适用日期', '2007-01-01');
    await page.getByRole('button', { name: '开始解读' }).click();
    await page.getByText('证据不足').first().waitFor({ timeout: 30000 });
    await page.waitForTimeout(3500);

    // 5 通知起草
    await page.getByRole('tab', { name: '公文起草' }).click();
    await page.waitForTimeout(1200);
    await page.getByPlaceholder('例如：关于开展安全生产检查的通知').fill('关于开展政务公开培训的通知');
    await page.getByPlaceholder('例如：各街道办事处、区直属各单位').fill('各科室');
    await page.getByPlaceholder('例如：杭州市余杭区人民政府办公室').fill('示例机关办公室');
    await fillDate(page, '选择成文日期', '2026-10-27');
    await page.getByPlaceholder('说明发文依据、目标与需要部署的事项').fill('为提升政务公开工作能力，现组织开展一次业务培训。');
    await page.locator('textarea[placeholder="填写事实或素材原文（可追责、可核对）"]:visible').first().fill('2026年10月开展一次政务公开培训，参加人数50人。');
    await page.locator('input[placeholder="来源引用（文件名/页码/链接/会议记录编号）"]:visible').first().fill('演示材料-01');
    await page.getByRole('button', { name: '生成待审草稿' }).click();
    await page.getByText('待审核').first().waitFor({ timeout: 30000 });
    await page.waitForTimeout(3500);

    // 6 报告夹带请示：规则阻断
    await page.locator('.el-select:visible').first().click();
    await page.getByRole('option', { name: '报告' }).click();
    await page.waitForTimeout(500);
    await page.getByPlaceholder('例如：关于开展安全生产检查的通知').fill('关于政务公开工作情况的报告');
    await page.getByPlaceholder('例如：各街道办事处、区直属各单位').fill('上级机关');
    await page.getByPlaceholder('说明发文依据、目标与需要部署的事项').fill('现将政务公开工作情况报告如下。请批准增加培训经费。');
    await page.getByRole('button', { name: '生成待审草稿' }).click();
    await page.getByText('待修改').first().waitFor({ timeout: 30000 });
    await page.waitForTimeout(4000);

    // 7 审核人通过通知
    await logout(page);
    await login(page, 'kbadmin', 'Kb@12345');
    await openGov(page);
    await page.getByRole('tab', { name: '待审草稿' }).click();
    await page.getByText('待审公文').first().waitFor({ timeout: 20000 });
    await page.waitForTimeout(3500);
    await page.getByRole('button', { name: '通过' }).first().click();
    await page.getByText('公文已通过审核').waitFor({ timeout: 15000 });
    await page.waitForTimeout(3000);

    // 8 审计台
    await logout(page);
    await loginAuditor(page);
    await page.getByText('安全审计台').first().click();
    await page.getByText('审计查询').first().waitFor({ timeout: 20000 });
    await page.getByRole('button', { name: '查询' }).click();
    await page.waitForTimeout(4000);
    console.log('FLOW_FULL_OK');
  } catch (err) {
    console.error('FLOW_FULL_FAIL', err.message);
    await page.screenshot({ path: 'flow-full-fail.png', fullPage: true }).catch(() => {});
    process.exitCode = 1;
  } finally {
    await context.close();
    await browser.close();
  }
})();
