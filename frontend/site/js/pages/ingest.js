import { api } from '../api.js';
import { ENUMS, batchTone, can, ingestTone, releaseTone } from '../config.js';
import { sectionHead } from '../components.js';
import { badge, drawer, emptyState, errorBox, errorMessage, esc, field, fmtTime, kv, table, toast } from '../ui.js';

function statusBadge(row) {
  return badge(batchTone(row.status), ENUMS.batchStatus[row.status] || row.status);
}

function releaseBadge(state) {
  return badge(releaseTone(state), ENUMS.releaseState[state] || state);
}

function ingestBadge(state) {
  return badge(ingestTone(state), ENUMS.ingestState[state] || state);
}

/** 批次详情抽屉：应有/实到/重复/无效/缺量逐项可对，逐条回执与原始记录不丢。 */
function openBatch(batchId) {
  api
    .get(`/api/ingest/batches/${batchId}`)
    .then((detail) => {
      const itemTable = detail.items.length
        ? table({
            rows: detail.items,
            columns: [
              { key: 'seq', label: '#', className: 'num' },
              { key: 'external_id', label: '外部ID', className: 'mono' },
              {
                key: 'outcome',
                label: '核对结果',
                render: (row) =>
                  badge(
                    { inserted: 'ok', duplicate: 'warn', invalid: 'danger' }[row.outcome] || 'info',
                    row.outcome_label,
                  ),
              },
              { key: 'post_id', label: '岗位ID', className: 'num', render: (row) => row.post_id || '—' },
              { key: 'reason', label: '说明', render: (row) => esc(row.reason || '—') },
            ],
          })
        : emptyState({ title: '本批没有逐条回执', hint: '失败批次可能只回传了状态，没有岗位明细。' });

      drawer({
        title: `批次 ${detail.batch_no}`,
        subtitle: `${detail.channel_name} · ${detail.channel_code} · 接收于 ${fmtTime(detail.received_at)}`,
        body: `${kv([
          ['核对状态', statusBadge(detail)],
          ['采集器原报', `<span class="mono">${esc(detail.source_status)}</span>`],
          ['应有数', `<b class="tabular">${detail.expected_count}</b>`],
          ['实到数', `<b class="tabular">${detail.arrived_count}</b>`],
          ['新稿入库', `<span class="tabular">${detail.inserted_count}</span>`],
          ['重复数（未新增）', `<span class="tabular">${detail.duplicate_count}</span>`],
          ['无效隔离', `<span class="tabular">${detail.invalid_count}</span>`],
          ['缺量数', `<b class="tabular red">${detail.missing_count}</b>`],
          ['旧稿沿用', `<span class="tabular">${detail.carried_count}</span>`],
          [
            '缺量外部ID',
            detail.missing_external_ids.length
              ? detail.missing_external_ids.map((id) => `<span class="mono">${esc(id)}</span>`).join('、')
              : '—',
          ],
          ['重试次数', `<span class="tabular">${detail.retries}</span>`],
          ['接收时间', fmtTime(detail.received_at)],
          ['备注', esc(detail.note || '—')],
        ])}
        <h3 style="margin:12px 0 6px;font-size:0.98rem">逐条回执（原始记录）</h3>
        ${itemTable}
        <div class="panel-note">重试同一批次不会重复新增岗位；重复回执按自然键识别并计入「重复数」。</div>`,
      });
    })
    .catch((err) => toast(errorMessage(err), 'err'));
}

/** 模拟采集器回执：选场景 → 自动填好 JSON → 提交，便于端到端演示缺量/失败/重试。 */
const SCENARIOS = [
  {
    key: 'complete',
    label: '① 完整批次（3 条全到）',
    build: (code, no) => ({
      channel_code: code,
      batch_no: no,
      source_status: 'success',
      expected_external_ids: ['j1', 'j2', 'j3'],
      posts: [
        { external_id: 'j1', company_raw: '云起数智科技有限公司', title_raw: 'Java开发工程师', city: '北京', salary_raw: '10-15K', degree: '本科', experience: '3-5年' },
        { external_id: 'j2', company_raw: '云起数智科技有限公司', title_raw: '前端开发工程师', city: '北京', salary_raw: '8-12K', degree: '本科', experience: '1-3年' },
        { external_id: 'j3', company_raw: '远山网络科技有限公司', title_raw: '测试工程师', city: '上海', salary_raw: '面议', degree: '大专', experience: '不限' },
      ],
    }),
  },
  {
    key: 'partial',
    label: '② 部分成功/超时（应到 3 缺 1）',
    build: (code, no) => ({
      channel_code: code,
      batch_no: no,
      source_status: 'partial',
      expected_external_ids: ['j1', 'j2', 'j3'],
      posts: [
        { external_id: 'j1', company_raw: '云起数智科技有限公司', title_raw: 'Java开发工程师', city: '北京', salary_raw: '10-15K', degree: '本科', experience: '3-5年' },
        { external_id: 'j2', company_raw: '云起数智科技有限公司', title_raw: '前端开发工程师', city: '北京', salary_raw: '8-12K', degree: '本科', experience: '1-3年' },
      ],
      note: '采集器拉取 j3 时超时，本批缺量',
    }),
  },
  {
    key: 'failed',
    label: '③ 批次失败（0 条，隔离旧稿）',
    build: (code, no) => ({
      channel_code: code,
      batch_no: no,
      source_status: 'failed',
      expected_external_ids: ['j1', 'j2'],
      posts: [],
      note: '渠道接口 500，本批完全失败',
    }),
  },
  {
    key: 'retry',
    label: '④ 重试同一批（同 batch_no，不重复新增）',
    build: (code, no) => ({
      channel_code: code,
      batch_no: no,
      source_status: 'success',
      expected_external_ids: ['j1', 'j2', 'j3'],
      posts: [
        { external_id: 'j1', company_raw: '云起数智科技有限公司', title_raw: 'Java开发工程师', city: '北京', salary_raw: '10-15K', degree: '本科', experience: '3-5年' },
        { external_id: 'j2', company_raw: '云起数智科技有限公司', title_raw: '前端开发工程师', city: '北京', salary_raw: '8-12K', degree: '本科', experience: '1-3年' },
        { external_id: 'j3', company_raw: '远山网络科技有限公司', title_raw: '测试工程师', city: '上海', salary_raw: '面议', degree: '大专', experience: '不限' },
      ],
    }),
  },
];

function openSimulator(ctx, channels, reload) {
  const writable = channels.filter((c) => c.enabled);
  const defaultCode = writable[0]?.channel_code || '';
  const defaultBatch = `manual-${new Date().toISOString().slice(0, 10)}-01`;
  const { root, close } = drawer({
    title: '模拟采集器回执',
    subtitle: '选场景自动生成回执，提交后按批次核对应有/实到/重复/缺量',
    body: `<form id="ingest-form" style="display:grid;gap:12px">
      ${field({
        label: '渠道',
        name: 'channel_code',
        value: defaultCode,
        options: writable.map((c) => ({ value: c.channel_code, label: `${c.channel_name}（${c.channel_code}）` })),
      })}
      ${field({ label: '批次号（同号再提交 = 重试）', name: 'batch_no', value: defaultBatch })}
      <div class="field">
        <label>回执场景</label>
        <div style="display:flex;flex-wrap:wrap;gap:6px">
          ${SCENARIOS.map((s) => `<button type="button" class="btn sm ghost" data-scenario="${s.key}">${esc(s.label)}</button>`).join('')}
        </div>
      </div>
      <div class="field">
        <label for="receipt">回执 JSON（可手工编辑）</label>
        <textarea id="receipt" name="receipt" rows="14" spellcheck="false"
          style="width:100%;font-family:var(--mono,monospace);font-size:0.82rem;line-height:1.5;padding:8px;border:1px solid var(--line,#999)"></textarea>
      </div>
      <div class="form-error" id="ingest-error"></div>
      <button class="btn accent" type="submit">接收并核对本批</button>
    </form>`,
  });

  const receipt = root.querySelector('#receipt');
  const fill = (scenarioKey) => {
    const scenario = SCENARIOS.find((s) => s.key === scenarioKey);
    const code = root.querySelector('[name=channel_code]').value;
    const no = root.querySelector('[name=batch_no]').value.trim() || defaultBatch;
    receipt.value = JSON.stringify(scenario.build(code, no), null, 2);
  };
  fill('complete');
  root.querySelectorAll('[data-scenario]').forEach((button) => {
    button.addEventListener('click', () => fill(button.dataset.scenario));
  });
  root.querySelector('[name=channel_code]').addEventListener('change', () => fill('complete'));

  root.querySelector('#ingest-form').addEventListener('submit', async (event) => {
    event.preventDefault();
    const button = event.target.querySelector('button[type=submit]');
    let payload;
    try {
      payload = JSON.parse(receipt.value);
    } catch {
      root.querySelector('#ingest-error').textContent = '回执不是合法 JSON，请检查后再提交';
      return;
    }
    button.disabled = true;
    button.textContent = '核对中…';
    try {
      const result = await api.post('/api/ingest/batches', payload);
      const retryWord = result.retries ? `（第 ${result.retries} 次重试，未重复新增）` : '';
      toast(`批次 ${result.batch_no} 核对完成：${result.status_label}，实到 ${result.arrived_count}/${result.expected_count}${retryWord}`);
      close();
      await reload();
    } catch (err) {
      root.querySelector('#ingest-error').textContent = errorMessage(err);
      button.disabled = false;
      button.textContent = '接收并核对本批';
    }
  });
}

export const ingestPage = {
  async render(view, ctx) {
    const render = () => ingestPage.render(view, ctx);
    let ledger;
    let batches;
    try {
      [ledger, batches] = await Promise.all([
        api.get('/api/ingest/ledger'),
        api.get('/api/ingest/batches', { limit: 40 }),
      ]);
    } catch (err) {
      view.innerHTML = errorBox(errorMessage(err));
      view.querySelector('#retry-btn')?.addEventListener('click', render);
      return;
    }
    const totals = ledger.totals;
    const canWrite = can(ctx.user.role, 'ingest:write');

    view.innerHTML = `
      <section class="panel">
        ${sectionHead('03', '来稿批次核对台账', `${totals.channels} 个渠道 · 收齐 ${totals.complete} / 缺量 ${totals.partial} / 失败 ${totals.failed}`)}
        <div class="panel-body">
          <div class="stat-strip">
            <div class="stat"><span class="k">应有 / 实到</span><span class="v">${totals.expected} / ${totals.arrived}</span><span class="hint">按批次核对</span></div>
            <div class="stat"><span class="k">本批缺量</span><span class="v red">${totals.missing}</span><span class="hint">缺的外部ID见行内</span></div>
            <div class="stat"><span class="k">重复回执</span><span class="v">${totals.duplicate}</span><span class="hint">均未重复新增</span></div>
            <div class="stat"><span class="k">旧稿沿用 / 隔离</span><span class="v">${totals.carried_posts} / ${totals.isolated_posts}</span><span class="hint">不冒充新稿</span></div>
          </div>
          <div class="panel-note" style="margin-top:8px">
            ${
              totals.publish_complete
                ? '<span class="badge ok">所有渠道本批收齐，可出完整发布版</span>'
                : '<span class="badge high">有渠道本批未收齐：其他渠道可先处理，但本次只出过渡版，不发布成完整版本</span>'
            }
            ${canWrite ? '<button class="btn sm accent" id="open-simulator" style="float:right">模拟采集器回执</button>' : ''}
          </div>
        </div>
        ${
          ledger.items.length
            ? table({
                rows: ledger.items,
                columns: [
                  {
                    key: 'channel_name',
                    label: '渠道',
                    render: (row) => `<b>${esc(row.channel_name)}</b><div class="cell-sub mono">${esc(row.channel_code)}</div>`,
                  },
                  { key: 'status', label: '本批状态', render: (row) => statusBadge(row) },
                  { key: 'latest_batch_no', label: '最新批次', className: 'mono', render: (row) => esc(row.latest_batch_no || '—') },
                  {
                    key: 'counts',
                    label: '应有 / 实到 / 重复',
                    className: 'num',
                    render: (row) => `${row.expected_count} / <b>${row.arrived_count}</b> / ${row.duplicate_count}`,
                  },
                  {
                    key: 'missing_count',
                    label: '缺量',
                    className: 'num',
                    render: (row) =>
                      row.missing_count
                        ? `<span class="red tabular">${row.missing_count}</span><div class="cell-sub mono">${esc(row.missing_external_ids.slice(0, 3).join('、'))}${row.missing_external_ids.length > 3 ? '…' : ''}</div>`
                        : '0',
                  },
                  {
                    key: 'fresh_count',
                    label: '新稿 / 沿用 / 隔离',
                    className: 'num',
                    render: (row) => `${row.fresh_count} / <span class="warn">${row.carried_count}</span> / ${row.isolated_count}`,
                  },
                  { key: 'release_state', label: '发布口径', render: (row) => releaseBadge(row.release_state) },
                  { key: 'received_at', label: '接收时间', render: (row) => fmtTime(row.received_at) },
                ],
              })
            : emptyState({ title: '还没有任何来稿批次', hint: canWrite ? '用「模拟采集器回执」接收第一批岗位。' : '等采集器按批次回传。' })
        }
        <div class="panel-note">点击渠道行查看最新批次的逐条回执；旧稿沿用与隔离数量与「原始岗位」页的来稿状态逐项对应。</div>
      </section>

      <section class="panel">
        ${sectionHead('B', '批次接收历史', `${batches.total} 个批次 · 重试不重复新增`)}
        ${
          batches.items.length
            ? table({
                rows: batches.items,
                columns: [
                  { key: 'batch_no', label: '批次号', className: 'mono' },
                  {
                    key: 'channel_name',
                    label: '渠道',
                    render: (row) => `${esc(row.channel_name)}<div class="cell-sub mono">${esc(row.channel_code)}</div>`,
                  },
                  { key: 'status', label: '状态', render: (row) => statusBadge(row) },
                  { key: 'expected_count', label: '应有', className: 'num' },
                  { key: 'arrived_count', label: '实到', className: 'num' },
                  { key: 'duplicate_count', label: '重复', className: 'num' },
                  { key: 'missing_count', label: '缺量', className: 'num', render: (row) => (row.missing_count ? `<span class="red">${row.missing_count}</span>` : '0') },
                  { key: 'retries', label: '重试', className: 'num' },
                  { key: 'received_at', label: '接收时间', render: (row) => fmtTime(row.received_at) },
                ],
              })
            : emptyState({ title: '暂无批次记录', hint: '接收回执后这里会留下不可删除的核对痕迹。' })
        }
      </section>`;

    view.querySelectorAll('table.data tbody tr').forEach((row) => {
      row.addEventListener('click', () => {
        // 第一张表是渠道台账（带 latest_batch_id），第二张是批次历史
        const ledgerRow = ledger.items.find((item) => String(item.channel_id) === row.dataset.id);
        const batchRow = batches.items.find((item) => String(item.id) === row.dataset.id);
        if (ledgerRow?.latest_batch_id) openBatch(ledgerRow.latest_batch_id);
        else if (batchRow) openBatch(batchRow.id);
      });
    });
    view.querySelector('#open-simulator')?.addEventListener('click', () => openSimulator(ctx, ledger.items, render));
  },
};

export default ingestPage;
