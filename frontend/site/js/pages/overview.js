import { api } from '../api.js';
import { ENUMS, batchTone, can, releaseTone } from '../config.js';
import { clusterStatusBadge, fmtMoney, fmtPct, sectionHead } from '../components.js';
import { badge, barList, emptyState, errorBox, errorMessage, esc, fmtTime, kv, statStrip, table, trendChart } from '../ui.js';

function latestRunBlock(latest) {
  if (!latest) {
    return emptyState({ title: '还没有归并运行', hint: '去「归并结果」发起一次归并，或等 07:40 的定时任务。' });
  }
  const metrics = latest.metrics || {};
  return `${kv([
    ['运行编号', `<span class="mono">${esc(latest.run_no)}</span>`],
    ['状态', `<span class="badge ${latest.status === 'success' ? 'ok' : 'warn'}">${esc(ENUMS.runStatus[latest.status] || latest.status)}</span>`],
    [
      '发布版本',
      latest.publish_complete
        ? badge('ok', '完整发布版')
        : badge('high', `过渡版（暂缓 ${latest.withheld_clusters ?? 0} 簇）`),
    ],
    ['相似度阈值', `<span class="mono">${Number(latest.threshold).toFixed(2)}</span>`],
    ['输入 / 输出', `${latest.posts_in} 条 → ${latest.clusters_out} 簇`],
    ['可发布 / 暂缓', `<b>${latest.releasable_clusters ?? latest.clusters_out}</b> / ${latest.withheld_clusters ?? 0} 簇`],
    ['消重条数', `<b class="tabular">${latest.dupes_removed}</b>`],
    ['两两比较', `${metrics.pairs ?? '—'} 对 / 连边 ${metrics.edges ?? '—'} 条`],
    ['不可比占比', fmtPct(metrics.noncomparable_rate || 0)],
    ['开始时间', fmtTime(latest.started_at)],
  ])}
  <div class="dotted"></div>
  <div class="ledger">
    ${(metrics.unparsed_samples || [])
      .map(
        (item) =>
          `<div class="row"><span class="mono">${esc(item.raw)}</span><b>不可比</b></div>`,
      )
      .join('')}
  </div>`;
}

export const overviewPage = {
  async render(view, ctx) {
    view.innerHTML = '<div class="empty"><div class="mark">排版中…</div></div>';
    let data;
    try {
      data = await api.get('/api/overview');
    } catch (err) {
      view.innerHTML = errorBox(errorMessage(err));
      view.querySelector('#retry-btn')?.addEventListener('click', () => overviewPage.render(view, ctx));
      return;
    }
    const salary = data.salary;
    const latest = data.latest_run;
    const ingest = data.ingest || null;
    const noncomparable = salary.negotiable + salary.unparsed;
    view.innerHTML = `
      <section class="lead-story">
        <div class="kicker">头版 · 归并综述</div>
        <h2>${data.channels_enabled} 个在用渠道来稿 ${data.posts_total} 条，归并后 ${data.clusters_total} 条岗位进入检索</h2>
        <p class="deck">
          同期消重 <b>${data.dupes_removed}</b> 条重复挂载，多成员簇 <b>${data.clusters_multi}</b> 个；
          ${noncomparable} 条岗位薪资不可比（占 <b>${fmtPct(salary.noncomparable_rate)}</b>），其中 ${salary.unparsed} 条无法解析已进不可比清单；
          ${data.clusters_expired} 个簇因岗位过期下线。
        </p>
        ${
          ingest
            ? `<div class="edition-banner ${ingest.publish_complete ? 'is-complete' : 'is-partial'}">
                ${
                  ingest.publish_complete
                    ? badge('ok', '完整发布版')
                    : badge('high', '过渡版 · 不发布成完整版本')
                }
                <span>本批应有 <b>${ingest.expected}</b> 条、实到 <b>${ingest.arrived}</b> 条、缺量 <b class="red">${ingest.missing}</b> 条、重复回执 <b>${ingest.duplicate}</b> 条；
                收齐渠道 ${ingest.complete_channels}/${ingest.complete_channels + ingest.partial_channels + ingest.failed_channels}，
                旧稿沿用 <b>${ingest.carried_posts}</b> 条、隔离 <b>${ingest.isolated_posts}</b> 条。
                缺量渠道的旧岗位只作沿用，不冒充新稿。</span>
              </div>`
            : ''
        }
        <div class="lead-figures">
          <div class="fig"><b>${data.posts_total}</b><span>原始来稿</span></div>
          <div class="fig"><b>${data.clusters_total}</b><span>归并后岗位</span></div>
          <div class="fig"><b>${data.dupes_removed}</b><span>消重条数</span></div>
          <div class="fig"><b class="red">${fmtPct(salary.noncomparable_rate)}</b><span>薪资不可比占比</span></div>
        </div>
      </section>

      ${statStrip([
        { label: '在用渠道', value: `${data.channels_enabled}/${data.channels_total}`, hint: '采集计划已登记' },
        { label: '原始岗位', value: data.posts_total, hint: `已过期 ${data.posts_expired} 条` },
        { label: '归并后', value: data.clusters_total, hint: `多成员簇 ${data.clusters_multi} 个` },
        { label: '待复核', value: data.pending_total, hint: `历史结论 ${data.reviews_total} 条` },
      ])}

      <div class="grid-2">
        <section class="panel">
          ${sectionHead('02', '薪资口径分布', '算法 A')}
          <div class="panel-body">
            ${barList(
              data.salary_mix.map((item) => ({
                label: item.label,
                count: item.count,
                tone: { parsed: 'ok', negotiable: 'warn', unparsed: 'accent' }[item.key] || '',
              })),
            )}
            <div class="panel-note">
              已归一 ${salary.parsed} 条 · 面议 ${salary.negotiable} 条 · 不可比 ${salary.unparsed} 条；解析失败率 ${fmtPct(salary.fail_rate)}。
            </div>
          </div>
        </section>
        <section class="panel">
          ${sectionHead('03', '渠道来稿量', '原始计数')}
          <div class="panel-body">
            ${barList(data.channel_mix.map((item) => ({ label: item.name, count: item.count })))}
            <div class="panel-note">渠道配置与采集计划在「渠道与采集」版面维护，仅管理员可改。</div>
          </div>
        </section>
      </div>

      <div class="grid-2">
        <section class="panel">
          ${sectionHead('04', '近 7 日归并次数', 'runs / day')}
          <div class="panel-body">${trendChart(data.trend)}</div>
        </section>
        <section class="panel">
          ${sectionHead('05', '最新归并运行', latest ? latest.run_no : '暂无')}
          <div class="panel-body">${latestRunBlock(latest)}</div>
        </section>
      </div>

      ${
        ingest && ingest.channels.length
          ? `<section class="panel">
        ${sectionHead('07', '渠道来稿批次口径', '应有 / 实到 / 缺量 / 沿用')}
        ${table({
          rows: ingest.channels,
          columns: [
            { key: 'channel_name', label: '渠道', render: (row) => `<b>${esc(row.channel_name)}</b><div class="cell-sub mono">${esc(row.channel_code)}</div>` },
            {
              key: 'status',
              label: '本批状态',
              render: (row) => badge(batchTone(row.status), ENUMS.batchStatus[row.status] || row.status),
            },
            { key: 'expected_count', label: '应有', className: 'num' },
            { key: 'arrived_count', label: '实到', className: 'num' },
            { key: 'missing_count', label: '缺量', className: 'num', render: (row) => (row.missing_count ? `<span class="red">${row.missing_count}</span>` : '0') },
            { key: 'carried_count', label: '沿用', className: 'num', render: (row) => (row.carried_count ? `<span class="warn">${row.carried_count}</span>` : '0') },
            { key: 'isolated_count', label: '隔离', className: 'num' },
            {
              key: 'release_state',
              label: '发布口径',
              render: (row) => badge(releaseTone(row.release_state), ENUMS.releaseState[row.release_state] || row.release_state),
            },
          ],
        })}
        <div class="panel-note">完整发布版只包含「可发布」渠道对应的簇；缺量渠道先出过渡版，补齐后下一版恢复完整。</div>
      </section>`
          : ''
      }

      <section class="panel">
        ${sectionHead('06', '成员最多的归并簇', 'golden record 已就位')}        ${
          data.top_clusters.length
            ? table({
                rows: data.top_clusters,
                columns: [
                  {
                    key: 'company',
                    label: '公司 / 职位',
                    render: (row) => `<b>${esc(row.company)}</b><div class="cell-sub">${esc(row.title)}</div>`,
                  },
                  { key: 'city', label: '城市', className: 'tight' },
                  { key: 'monthly_median', label: '月薪中位', className: 'num', render: (row) => fmtMoney(row.monthly_median) },
                  { key: 'member_count', label: '成员 / 渠道', className: 'num', render: (row) => `${row.member_count} / ${row.channel_count}` },
                  { key: 'confidence', label: '置信度', className: 'num', render: (row) => Number(row.confidence).toFixed(3) },
                  { key: 'status_label', label: '状态', render: (row) => clusterStatusBadge(row) },
                ],
              })
            : emptyState({ title: '还没有归并结果', hint: '先发起一次归并。' })
        }
        ${
          can(ctx.user.role, 'cluster:read')
            ? `<div class="panel-note">点击任一行可跳到「归并结果」查看成员对照与复核。</div>`
            : ''
        }
      </section>`;
    view.querySelectorAll('table.data tbody tr').forEach((row) => {
      row.addEventListener('click', () => {
        location.hash = '#/clusters';
      });
    });
  },
};

export default overviewPage;
