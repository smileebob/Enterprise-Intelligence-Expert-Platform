const { chromium } = require('playwright');

const BASE = 'http://127.0.0.1:5173';

async function fillDate(page, placeholder, value) {
  const input = page.getByPlaceholder(placeholder);
  await input.click();
  await page.waitForTimeout(300);
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
  await page.waitForTimeout(1500);
}

async function openGov(page) {
  await page.getByText('政务办公工作台').first().click();
  await page.getByRole('tab', { name: '政策解读' }).waitFor({ timeout: 20000 });
  await page.waitForTimeout(800);
}

(async () => {
  const record = process.env.RECORD === '1';
  const browser = await chromium.launch({ channel: 'msedge', headless: true });
  const context = await browser.newContext({
    viewport: { width: 1280, height: 720 },
    ...(record ? { recordVideo: { dir: 'videos', size: { width: 1280, height: 720 } } } : {}),
  });
  const page = await context.newPage();
  page.setDefaultTimeout(20000);
  try {
    await login(page, 'expert', 'Expert@12345');
    await openGov(page);
    await page.waitForTimeout(4000);

    await page.getByPlaceholder('例如：本区企业申请高技能人才培训补贴需要哪些条件和材料？').fill('政府信息公开申请不能当场答复的，答复期限是多少个工作日？');
    await page.getByPlaceholder('例如：杭州市余杭区', { exact: true }).fill('全国');
    await fillDate(page, '选择事项发生或适用日期', '2026-10-27');
    await page.getByRole('button', { name: '开始解读' }).click();
    await page.getByText('20个工作日内予以答复').first().waitFor({ timeout: 40000 });
    await page.waitForTimeout(6000);
    await page.mouse.wheel(0, 600);
    await page.waitForTimeout(4000);

    await page.getByRole('tab', { name: '公文起草' }).click();
    await page.waitForTimeout(2500);
    await page.getByPlaceholder('例如：关于开展安全生产检查的通知').fill('关于开展政务公开培训的通知');
    await page.getByPlaceholder('例如：各街道办事处、区直属各单位').fill('各科室');
    await page.getByPlaceholder('例如：杭州市余杭区人民政府办公室').fill('示例机关办公室');
    await fillDate(page, '选择成文日期', '2026-10-27');
    await page.getByPlaceholder('说明发文依据、目标与需要部署的事项').fill('为提升政务公开工作能力，现组织开展一次业务培训。');
    await page.locator('textarea[placeholder="填写事实或素材原文（可追责、可核对）"]:visible').first().fill('2026年10月开展一次政务公开培训，参加人数50人。');
    await page.locator('input[placeholder="来源引用（文件名/页码/链接/会议记录编号）"]:visible').first().fill('演示材料-01');
    await page.getByRole('button', { name: '生成待审草稿' }).click();
    await page.getByText('待审核').first().waitFor({ timeout: 30000 });
    await page.waitForTimeout(6500);
    await page.mouse.wheel(0, 500);
    await page.waitForTimeout(4000);

    await page.getByRole('button', { name: '退出' }).click();
    await page.waitForTimeout(800);
    await login(page, 'kbadmin', 'Kb@12345');
    await openGov(page);
    await page.getByRole('tab', { name: '待审草稿' }).click();
    await page.getByText('待审公文').first().waitFor({ timeout: 20000 });
    await page.waitForTimeout(1000);
    await page.getByRole('button', { name: '通过' }).first().click();
    await page.getByText('公文已通过审核').waitFor({ timeout: 15000 });
    await page.waitForTimeout(5000);

    await page.getByRole('tab', { name: '政策版本对照' }).click();
    await page.waitForTimeout(600);
    await page.getByRole('button', { name: '开始对照' }).click();
    await page.getByText('新增条款').first().waitFor({ timeout: 30000 });
    await page.waitForTimeout(8000);
    await page.waitForTimeout(3000);
    console.log('FLOW_OK');
  } catch (err) {
    console.error('FLOW_FAIL', err.message);
    await page.screenshot({ path: 'flow-fail.png', fullPage: true }).catch(() => {});
    process.exitCode = 1;
  } finally {
    await context.close();
    await browser.close();
  }
})();
