<script>
  let username = 'processor'
  let password = 'herb123456'
  let token = localStorage.getItem('herb_token') || ''
  let role = localStorage.getItem('herb_role') || ''
  let view = 'gate'

  let rows = []
  let ledger = []
  let percentInfo = null
  let percentDraft = 10
  let percentError = ''

  let herb = '白芍'
  let tempC = 110
  let minutes = 10
  let inputG = 100
  let residueG = 5
  let error = ''
  let notice = ''

  const corrInput = {}

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
      view = 'gate'
      await load()
    } catch (err) {
      error = err.message
    }
  }

  async function load() {
    const [batchData, ledgerData, percentData] = await Promise.all([
      api('/api/batches'),
      api('/api/residue-ledger'),
      api('/api/settings/residue-percent'),
    ])
    rows = batchData
    ledger = ledgerData
    percentInfo = percentData
    percentDraft = percentData.residue_percent
  }

  async function savePercent() {
    percentError = ''
    try {
      percentInfo = await api('/api/settings/residue-percent', {
        method: 'PUT',
        body: JSON.stringify({ residue_percent: Number(percentDraft) }),
      })
      percentDraft = percentInfo.residue_percent
    } catch (err) {
      percentError = err.message
    }
  }

  async function saveBatch() {
    error = ''
    notice = ''
    try {
      await api('/api/batches', {
        method: 'POST',
        body: JSON.stringify({
          herb,
          steps: [{ name: '清炒', temp_c: Number(tempC), minutes: Number(minutes) }],
          input_g: Number(inputG),
          residue_g: Number(residueG),
        }),
      })
      notice = '残渣门禁通过，记录已写入'
      await load()
    } catch (err) {
      error = err.message
    }
  }

  async function correctInput(row) {
    error = ''
    notice = ''
    const value = Number(corrInput[row.id])
    if (!value || value <= 0) {
      error = '请输入大于 0 的投料克数'
      return
    }
    try {
      await api(`/api/batches/${row.id}/input`, {
        method: 'PUT',
        body: JSON.stringify({ input_g: value }),
      })
      notice = `#${row.id} 投料已改正，旧残渣 ${row.residue_g} 克保留在履历中`
      corrInput[row.id] = ''
      await load()
    } catch (err) {
      error = err.message
    }
  }

  function leave() {
    localStorage.clear()
    token = ''
    role = ''
    rows = []
    ledger = []
    percentInfo = null
  }

  function fmt(ts) {
    return ts ? new Date(ts).toLocaleString('zh-CN', { hour12: false }) : ''
  }

  $: limitHint = percentInfo && Number(inputG) > 0
    ? (Number(inputG) * percentInfo.residue_percent / 100)
    : null
  $: overLimit = limitHint !== null && Number(residueG) > limitHint

  if (token) load()
</script>

<main>
  {#if !token}
    <h1>饮片炮制记录台</h1>
    <p>炮制记录整包保存。清炒温度须在 80 到 150，时长须在 5 到 30 分钟。</p>
    <input bind:value={username} />
    <input type="password" bind:value={password} />
    <button on:click={enter}>登录</button>
    {#if error}<p class="err">{error}</p>{/if}
    <p>processor / herb123456 可写；checker / check123456 只读</p>
  {:else}
    <header class="topbar">
      <span class="brand">饮片炮制记录台</span>
      <nav>
        <button class="navlink" class:active={view === 'gate'} on:click={() => (view = 'gate')}>残渣门禁链</button>
        <button class="navlink" class:active={view === 'batches'} on:click={() => (view = 'batches')}>总表</button>
      </nav>
      <span class="user">{role === 'writer' ? '炮制员' : '质检员'} · <button class="link" on:click={leave}>退出</button></span>
    </header>

    {#if view === 'gate'}
      <section class="card">
        <h2>残渣百分比设置</h2>
        {#if percentInfo}
          {#if role === 'writer'}
            <label>残渣占投料上限
              <input type="number" min="0" max="100" step="0.1" bind:value={percentDraft} /> %
            </label>
            <button on:click={savePercent}>保存百分比</button>
            {#if percentError}<p class="err">{percentError}</p>{/if}
          {:else}
            <p class="readonly">当前残渣上限：<strong>{percentInfo.residue_percent}%</strong>（质检员仅可查看，不能修改）</p>
          {/if}
          <p class="meta">最近由 {percentInfo.updated_by} 于 {fmt(percentInfo.updated_at)} 设定</p>
        {/if}
      </section>

      <section class="card">
        <h2>投料残渣栏</h2>
        {#if role === 'writer'}
          <div class="form-grid">
            <label>饮片<input bind:value={herb} /></label>
            <label>清炒温度℃<input type="number" bind:value={tempC} /></label>
            <label>时长（分）<input type="number" bind:value={minutes} /></label>
            <label>投料（克）<input type="number" min="0" step="0.01" bind:value={inputG} /></label>
            <label>残渣（克）<input type="number" min="0" step="0.01" bind:value={residueG} /></label>
          </div>
          {#if limitHint !== null}
            <p class="meta">
              当前百分比 {percentInfo.residue_percent}%，本笔残渣上限 {limitHint} 克。
              {#if overLimit}<span class="err">残渣超限，提交将被拒绝。</span>{/if}
            </p>
          {/if}
          <button on:click={saveBatch}>写入记录</button>
          {#if error}<p class="err">{error}</p>{/if}
          {#if notice}<p class="ok">{notice}</p>{/if}
        {:else}
          <p class="readonly">质检员只读：投料与残渣由炮制员写入。</p>
        {/if}
      </section>

      <section class="card">
        <h2>残渣履历</h2>
        <table>
          <thead>
            <tr>
              <th>时间</th><th>记录</th><th>投料（克）</th><th>残渣（克）</th><th>动作</th><th>操作人</th>
            </tr>
          </thead>
          <tbody>
            {#each ledger as l}
              <tr>
                <td>{fmt(l.created_at)}</td>
                <td>#{l.batch_id} {l.herb}</td>
                <td class="num">{l.input_g}</td>
                <td class="num">{l.residue_g}</td>
                <td>{l.action}</td>
                <td>{l.created_by}</td>
              </tr>
            {/each}
          </tbody>
        </table>
      </section>
    {:else}
      <section class="card">
        <h2>炮制总表</h2>
        {#if error}<p class="err">{error}</p>{/if}
        {#if notice}<p class="ok">{notice}</p>{/if}
        <table>
          <thead>
            <tr>
              <th>饮片</th><th>判定</th><th>原因</th><th>温度℃</th><th>时长（分）</th>
              <th>投料（克）</th><th>残渣（克）</th><th>操作人</th>
              {#if role === 'writer'}<th>改正投料</th>{/if}
            </tr>
          </thead>
          <tbody>
            {#each rows as row}
              <tr>
                <td>{row.herb}</td>
                <td>{row.verdict}</td>
                <td>{row.reason}</td>
                <td class="num">{row.doc.steps[0].temp_c}</td>
                <td class="num">{row.doc.steps[0].minutes}</td>
                <td class="num">{row.input_g}</td>
                <td class="num">{row.residue_g}</td>
                <td>{row.created_by}</td>
                {#if role === 'writer'}
                  <td>
                    <input class="corr" type="number" min="0" step="0.01" placeholder="新投料克数" bind:value={corrInput[row.id]} />
                    <button on:click={() => correctInput(row)}>改正</button>
                  </td>
                {/if}
              </tr>
            {/each}
          </tbody>
        </table>
      </section>
    {/if}
  {/if}
</main>

<style>
  main { font-family: sans-serif; max-width: 960px; margin: 0 auto 32px; color: #3f2f1f; }
  h1 { color: #7c2d12; margin: 24px 16px; }
  h2 { margin-top: 0; font-size: 1.1rem; color: #7c2d12; }
  .topbar {
    display: flex; align-items: center; gap: 20px;
    background: #7c2d12; color: #fff7ed; padding: 12px 20px;
  }
  .topbar .brand { font-weight: bold; font-size: 1.05rem; }
  .topbar nav { display: flex; gap: 14px; flex: 1; }
  .topbar .navlink {
    background: none; border: none; color: #fed7aa; font: inherit;
    cursor: pointer; padding: 4px 8px; border-radius: 4px;
  }
  .topbar .navlink.active { background: #9a3412; color: #fff; }
  .topbar .user { font-size: 0.9rem; }
  .link { background: none; border: none; color: #fed7aa; text-decoration: underline; cursor: pointer; font: inherit; }
  .card { border: 1px solid #e7d8c8; border-radius: 8px; padding: 16px 20px; margin: 16px; background: #fff; }
  .form-grid { display: flex; flex-wrap: wrap; gap: 12px; margin-bottom: 10px; }
  .form-grid label { display: flex; flex-direction: column; font-size: 0.85rem; gap: 4px; }
  input { padding: 6px; }
  .corr { width: 110px; margin-right: 6px; }
  button { padding: 6px 14px; background: #7c2d12; color: #fff; border: none; border-radius: 4px; cursor: pointer; }
  button:hover { background: #9a3412; }
  table { border-collapse: collapse; width: 100%; font-size: 0.9rem; }
  th, td { border: 1px solid #e7d8c8; padding: 6px 8px; text-align: left; }
  th { background: #fdf3e7; }
  .num { text-align: right; font-variant-numeric: tabular-nums; }
  .err { color: #b91c1c; }
  .ok { color: #15803d; }
  .meta { color: #78716c; font-size: 0.85rem; }
  .readonly { color: #57534e; }
</style>
