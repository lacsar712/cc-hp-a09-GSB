<script>
  let username = 'processor'
  let password = 'herb123456'
  let token = localStorage.getItem('herb_token') || ''
  let role = localStorage.getItem('herb_role') || ''
  let rows = []
  let herb = '白芍'
  let tempC = 110
  let minutes = 10
  let inputGrams = 100
  let residueGrams = 5
  let limitPercent = 10
  let percentDraft = 10
  let records = []
  let corrections = {}
  let error = ''
  let percentMsg = ''

  async function api(path, options = {}) {
    const res = await fetch(path, {
      ...options,
      headers: {
        'Content-Type': 'application/json',
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
      },
    })
    const data = await res.json().catch(() => ({}))
    if (!res.ok) throw new Error(data.detail || '请求失败')
    return data
  }

  async function enter() {
    error = ''
    try {
      const data = await api('/api/auth/login', {
        method: 'POST',
        body: JSON.stringify({ username, password }),
      })
      token = data.access_token
      role = data.role
      localStorage.setItem('herb_token', token)
      localStorage.setItem('herb_role', role)
      await load()
    } catch (err) {
      error = err.message
    }
  }

  async function load() {
    const [batchData, settingData, recordData] = await Promise.all([
      api('/api/batches'),
      api('/api/residue/settings'),
      api('/api/residue/records'),
    ])
    rows = batchData
    limitPercent = settingData.limit_percent
    percentDraft = settingData.limit_percent
    records = recordData
  }

  async function savePercent() {
    percentMsg = ''
    error = ''
    try {
      const data = await api('/api/residue/settings', {
        method: 'PUT',
        body: JSON.stringify({ limit_percent: Number(percentDraft) }),
      })
      limitPercent = data.limit_percent
      percentDraft = data.limit_percent
      percentMsg = `残渣上限已改为 ${data.limit_percent}%`
      await load()
    } catch (err) {
      error = err.message
    }
  }

  async function save() {
    error = ''
    try {
      await api('/api/batches', {
        method: 'POST',
        body: JSON.stringify({
          herb,
          steps: [{ name: '清炒', temp_c: Number(tempC), minutes: Number(minutes) }],
          input_grams: Number(inputGrams),
          residue_grams: Number(residueGrams),
        }),
      })
      residueGrams = ''
      await load()
    } catch (err) {
      error = err.message
    }
  }

  async function correctInput(row) {
    error = ''
    const value = Number(corrections[row.id])
    if (!value || value <= 0) {
      error = '改正后的投料克数须大于 0'
      return
    }
    try {
      await api(`/api/batches/${row.id}/correct-input`, {
        method: 'POST',
        body: JSON.stringify({ input_grams: value }),
      })
      corrections = { ...corrections, [row.id]: '' }
      await load()
    } catch (err) {
      error = err.message
    }
  }

  function jump(id) {
    document.getElementById(id)?.scrollIntoView({ behavior: 'smooth' })
  }

  function fmtTime(iso) {
    return iso ? new Date(iso).toLocaleString('zh-CN', { hour12: false }) : ''
  }

  function num(v) {
    return v === null || v === undefined ? '—' : Number(v)
  }

  function leave() {
    localStorage.clear()
    token = ''
    role = ''
  }

  if (token) load()
</script>

{#if !token}
  <main class="narrow">
    <h1>饮片炮制记录台</h1>
    <p>炮制记录整包保存。清炒温度须在 80 到 150，时长须在 5 到 30 分钟。残渣称重闭环：残渣不得超过炮制员设定的投料百分比。</p>
    <input bind:value={username} />
    <input type="password" bind:value={password} />
    <button on:click={enter}>登录</button>
    {#if error}<p class="err">{error}</p>{/if}
    <p>processor / herb123456 可写；checker / check123456 只读</p>
  </main>
{:else}
  <header class="topbar">
    <span class="brand">饮片炮制记录台</span>
    <nav class="chain">
      <span class="chain-title">🔗 残渣门禁链：</span>
      <button on:click={() => jump('settings')}>百分比设置</button>
      <button on:click={() => jump('weigh')}>投料残渣栏</button>
      <button on:click={() => jump('history')}>残渣履历</button>
      <button on:click={() => jump('overview')}>总表</button>
    </nav>
    <span class="session">{role === 'writer' ? '炮制员' : '质检员'} <button on:click={leave}>退出</button></span>
  </header>

  <main>
    {#if error}<p class="err">{error}</p>{/if}

    <section id="settings">
      <h2>百分比设置 <span class="badge">当前残渣上限 {limitPercent}%</span></h2>
      {#if role === 'writer'}
        <p>残渣克数超过投料克数 × 该百分比时，整笔记录拒写。百分比可随时修改，修改后只约束新写入。</p>
        <input type="number" min="0" max="100" step="0.1" bind:value={percentDraft} />
        <button on:click={savePercent}>修改百分比</button>
        {#if percentMsg}<p class="ok">{percentMsg}</p>{/if}
      {:else}
        <p>当前残渣上限为 <strong>{limitPercent}%</strong>。质检员可查看百分比与履历，不能修改百分比。</p>
      {/if}
    </section>

    <section id="weigh">
      <h2>投料残渣栏</h2>
      {#if role === 'writer'}
        <p>每笔写入同时声明投料克与残渣克，残渣须 ≤ 投料 × {limitPercent}%（即 {inputGrams ? Math.round(Number(inputGrams) * Number(limitPercent)) / 100 : 0} 克以内），超限拒写。</p>
        <div class="formline">
          <label>饮片 <input bind:value={herb} /></label>
          <label>清炒温度 <input type="number" bind:value={tempC} /></label>
          <label>时长(分) <input type="number" bind:value={minutes} /></label>
          <label>投料(克) <input type="number" min="0" step="0.1" bind:value={inputGrams} /></label>
          <label>残渣(克) <input type="number" min="0" step="0.1" bind:value={residueGrams} /></label>
          <button on:click={save}>写入记录</button>
        </div>
      {:else}
        <p>质检员只读，不可写入投料与残渣。</p>
      {/if}
    </section>

    <section id="history">
      <h2>残渣履历 <span class="badge">只追加，不改写</span></h2>
      <table>
        <thead>
          <tr>
            <th>#</th><th>批次</th><th>饮片</th><th>类型</th>
            <th>投料(克)</th><th>残渣(克)</th><th>当时上限</th><th>说明</th><th>操作人</th><th>时间</th>
          </tr>
        </thead>
        <tbody>
          {#each records as rec}
            <tr>
              <td>{rec.id}</td>
              <td>{rec.batch_id}</td>
              <td>{rec.herb}</td>
              <td>{rec.kind === 'correct_input' ? '投料改正' : '残渣称重'}</td>
              <td class="num">{num(rec.input_grams)}</td>
              <td class="num">{num(rec.residue_grams)}</td>
              <td class="num">{rec.limit_percent}%</td>
              <td>{rec.note}</td>
              <td>{rec.created_by}</td>
              <td>{fmtTime(rec.created_at)}</td>
            </tr>
          {/each}
        </tbody>
      </table>
    </section>

    <section id="overview">
      <h2>总表</h2>
      <table>
        <thead>
          <tr>
            <th>批次</th><th>饮片</th><th>结论</th><th>原因</th><th>温度</th>
            <th>投料(克)</th><th>残渣(克)</th>
            {#if role === 'writer'}<th>事后改正投料</th>{/if}
          </tr>
        </thead>
        <tbody>
          {#each rows as row}
            <tr>
              <td>{row.id}</td>
              <td>{row.herb}</td>
              <td>{row.verdict}</td>
              <td>{row.reason}</td>
              <td class="num">{row.doc.steps[0].temp_c}</td>
              <td class="num">{num(row.doc.input_grams)}</td>
              <td class="num">{num(row.doc.residue_grams)}</td>
              {#if role === 'writer'}
                <td>
                  {#if row.doc.residue_grams !== undefined && row.doc.residue_grams !== null}
                    <input type="number" min="0" step="0.1" placeholder="新投料克数" bind:value={corrections[row.id]} />
                    <button on:click={() => correctInput(row)}>改正</button>
                  {:else}
                    —
                  {/if}
                </td>
              {/if}
            </tr>
          {/each}
        </tbody>
      </table>
    </section>
  </main>
{/if}

<style>
  :global(body) { margin: 0; background: #faf6f0; }
  main { font-family: sans-serif; max-width: 1080px; margin: 24px auto; color: #3f2f1f; padding: 0 16px; }
  main.narrow { max-width: 720px; }
  h1 { color: #7c2d12; }
  h2 { color: #7c2d12; margin-top: 32px; }
  .topbar {
    position: sticky; top: 0; z-index: 10;
    display: flex; align-items: center; gap: 16px;
    background: #7c2d12; color: #fff7ed; padding: 10px 20px;
    font-family: sans-serif;
  }
  .brand { font-weight: bold; }
  .chain { display: flex; align-items: center; gap: 8px; flex: 1; flex-wrap: wrap; }
  .chain-title { font-size: 13px; opacity: 0.85; }
  .chain button { background: #fed7aa; color: #7c2d12; border: none; border-radius: 4px; padding: 4px 10px; cursor: pointer; }
  .chain button:hover { background: #fdba74; }
  .session { font-size: 13px; }
  .session button { margin-left: 8px; }
  section { background: #fff; border: 1px solid #e7d9c8; border-radius: 8px; padding: 12px 16px; scroll-margin-top: 64px; }
  .badge { font-size: 13px; font-weight: normal; background: #ffedd5; color: #9a3412; border-radius: 999px; padding: 2px 10px; }
  .formline { display: flex; flex-wrap: wrap; gap: 12px; align-items: flex-end; }
  .formline label { font-size: 13px; display: flex; flex-direction: column; gap: 4px; }
  input { padding: 6px; width: 110px; box-sizing: border-box; }
  .formline input { width: 120px; }
  main.narrow input { width: auto; margin-right: 8px; }
  button { padding: 6px 12px; cursor: pointer; }
  table { border-collapse: collapse; width: 100%; font-size: 14px; }
  th, td { border: 1px solid #e7d9c8; padding: 6px 8px; text-align: left; }
  th { background: #ffedd5; }
  td.num, th:nth-child(5), th:nth-child(6), th:nth-child(7) { text-align: right; }
  .err { color: #b91c1c; font-weight: bold; }
  .ok { color: #166534; }
</style>
