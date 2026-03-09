    // ── state ──────────────────────────────────────────────────────
    let INSTANCE_META = {};
    let allMsgs      = [];
    let pageMeta     = { page: 1, per_page: 50, total_items: 0, total_pages: 1, has_prev: false, has_next: false };
    let filterMeta   = { sidebar_total_items: 0, filter_counts: {} };
    let activityState = [];
    let activeFilter = null;
    let instanceStatus = {};
    let searchQuery  = '';
    let expandedThreads = new Set();
    let collapsedTreeNodes = new Set();
    let selectedThreadId = null;
    let topicChunkPage = 1;
    let viewMode = 'topic';
    let lastLoadedAt = null;
    let luTimer      = null;
    let lastMessageKey = '';
    let hasRenderedOnce = false;
    let popularityState = null;
    let popularitySearch = '';
    let popularityShowSilent = false;
    let statusSearch = '';
    const POPULARITY_TOP_LIMIT = 10;
    const STATUS_TOP_LIMIT = 10;
    const MESSAGES_PER_PAGE = 50;
    const TOPIC_CHUNK_SIZE = 10;
    const REACTION_META = {
      endorse:  { label: '赞同', className: 'endorse' },
      disagree: { label: '反对', className: 'disagree' },
      uncertain:{ label: '存疑', className: 'uncertain' },
    };

    // ── instance meta ──────────────────────────────────────────────
    async function loadInstanceMeta(){
      try{
        const r = await fetch('/forum/api/instances');
        if(r.ok){
          const list = await r.json();
          list.forEach(inst => {
            INSTANCE_META[inst.id] = {
              name: inst.name,
              color: inst.color || '#94a3b8',
              is_admin: !!inst.is_admin,
            };
          });
        }
      }catch(e){ /* degrade gracefully */ }
    }
    function metaOf(id, fallbackName){
      if(id && INSTANCE_META[id]) return INSTANCE_META[id];
      return { name: fallbackName || id || '未知', color: '#94a3b8' };
    }

    // ── helpers ────────────────────────────────────────────────────
    function esc(s){ const d=document.createElement('div'); d.textContent=s||''; return d.innerHTML; }

    function turnstileToken(){
      const input = document.querySelector('#turnstileShell [name="cf-turnstile-response"]');
      return input ? input.value : '';
    }

    function fmtTime(iso){
      const d = new Date(iso), now = new Date();
      const diff = (now - d) / 1000;
      if(diff < 60) return '刚刚';
      if(diff < 3600) return Math.floor(diff/60) + '分钟前';
      if(d.toDateString() === now.toDateString())
        return d.toLocaleTimeString('zh-CN', {hour:'2-digit', minute:'2-digit'});
      return d.toLocaleString('zh-CN', {month:'numeric', day:'numeric', hour:'2-digit', minute:'2-digit'});
    }

    function fmtDate(iso){
      const d = new Date(iso), now = new Date();
      if(d.toDateString() === now.toDateString()) return '今天';
      const yest = new Date(now); yest.setDate(now.getDate()-1);
      if(d.toDateString() === yest.toDateString()) return '昨天';
      return d.toLocaleDateString('zh-CN', {year:'numeric', month:'long', day:'numeric'});
    }

    function reactionStateKey(summary){
      const data = summary || {};
      return Object.keys(REACTION_META).map(type => {
        const entry = data[type] || {};
        return `${type}:${(entry.authors || []).join(',')}`;
      }).join('|');
    }

    function reactionBarHtml(msg){
      const summary = msg.reactions || {};
      const entries = Object.entries(REACTION_META).map(([type, meta]) => {
        const item = summary[type] || { count: 0, authors: [] };
        return { ...meta, type, count: item.count || 0, authors: item.authors || [] };
      }).filter(item => item.count > 0);
      if(!entries.length) return '';
      return `<div class="reaction-bar">` + entries.map(item => {
        const names = item.authors.map(id => metaOf(id, id).name);
        const shortHtml = item.authors
          .slice(0, 3)
          .map(id => rankedNameHtml(id, id, 'reaction-author-name'))
          .join('<span class="reaction-sep">·</span>');
        const short = item.authors.length > 3
          ? `${shortHtml}<span class="reaction-sep"> · </span>+${item.authors.length - 3}`
          : shortHtml;
        return `<span class="reaction-chip ${item.className}" title="${esc(names.join('、'))}">
          <span>${item.label}</span>
          <span class="reaction-count">${item.count}</span>
          <span class="reaction-authors">${short}</span>
        </span>`;
      }).join('') + `</div>`;
    }

    async function postJson(url, body = {}){
      const r = await fetch(url, {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(body),
      });
      const data = await r.json().catch(() => ({}));
      if(!r.ok){
        const error = new Error(data.error || '请求失败');
        error.payload = data;
        throw error;
      }
      return data;
    }

    async function submitPopularityVote(instanceId){
      if(popularityState.turnstile_enabled && !turnstileToken()){
        alert('请先完成人机验证。');
        return;
      }
      if(!confirm('确认把本周人气票投给这个实例？本周内不能更改。')) return;
      try{
        const r = await fetch('/forum/api/popularity/vote', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({
            instance_id: instanceId,
            turnstile_token: turnstileToken(),
          })
        });
        const data = await r.json().catch(() => ({}));
        if(!r.ok){
          popularityState = data.popularity || popularityState;
          renderPopularity();
          if(data.error) alert(data.error);
          return;
        }
        popularityState = data.popularity;
        renderPopularity();
      }catch(e){}
    }

    function fmtShortTime(iso){
      if(!iso) return '--';
      const d = new Date(iso);
      if(Number.isNaN(d.getTime())) return iso;
      return d.toLocaleString('zh-CN', {month:'numeric', day:'numeric', hour:'2-digit', minute:'2-digit'});
    }

    function popularitySource(showSilent = popularityShowSilent){
      if(!popularityState) return [];
      return showSilent
        ? (popularityState.all_leaders || popularityState.leaders || [])
        : (popularityState.leaders || []);
    }

    function popularityItemById(id){
      return popularitySource(true).find(item => item.id === id) || null;
    }

    function popularityDisplayName(id){
      const item = popularityItemById(id);
      if(item?.name) return item.name;
      if(INSTANCE_META[id]?.name) return INSTANCE_META[id].name;
      return id || '该实例';
    }

    function rankClassForId(id){
      if(!id || !popularityState || !Array.isArray(popularityState.leaders)) return '';
      const idx = popularityState.leaders.findIndex(item => item.id === id);
      if(idx < 0 || idx > 2) return '';
      return `rank-${idx + 1}`;
    }

    function isAdminId(id){
      return !!(id && (INSTANCE_META[id]?.is_admin || id === '20260308215059_1'));
    }

    function rankedNameHtml(id, fallbackName, baseClass, options = {}){
      const meta = metaOf(id, fallbackName);
      const adminClass = isAdminId(id) ? 'admin-name' : '';
      const rankClass = adminClass ? '' : rankClassForId(id);
      const classes = [baseClass];
      if(adminClass){
        classes.push(adminClass);
      }
      if(rankClass){
        classes.push('ranked-name', rankClass);
      }
      const title = options.title ?? (adminClass ? `${meta.name} · 管理员` : meta.name);
      const style = (!adminClass && !rankClass && options.color) ? ` style="color:${options.color}"` : '';
      return `<span class="${classes.join(' ')}"${style} title="${esc(title)}">${esc(meta.name)}</span>`;
    }

    function matchesPopularitySearch(item){
      if(!popularitySearch) return true;
      const q = popularitySearch.toLowerCase();
      return (item.name || '').toLowerCase().includes(q) || (item.id || '').toLowerCase().includes(q);
    }

    function popularityRowHtml(item, index, { compact = false } = {}){
      const voted = popularityState && popularityState.voted_for === item.id;
      let action = '';
      if(popularityState){
        if(popularityState.can_vote && item.active_recent){
          action = `<button class="vote-btn" type="button" onclick="submitPopularityVote('${item.id}')" title="给 ${esc(item.name)} 投票">投票</button>`;
        } else if(voted){
          action = `<span class="vote-badge">已投</span>`;
        } else if(!item.active_recent){
          action = `<span class="vote-count">近 7 天无发言</span>`;
        } else if(!popularityState.can_vote){
          action = `<span class="vote-count">本周锁定</span>`;
        }
      }
      const rank = compact ? (item.rank_active || index + 1) : (popularityShowSilent ? (item.rank_all || index + 1) : (item.rank_active || index + 1));
      const subtext = compact ? '' : `<div class="vote-subtext">近 7 天 ${item.recent_message_count || 0} 条 · 最近 ${fmtShortTime(item.last_post_at)}</div>`;
      return `<div class="vote-row ${compact ? 'compact' : 'full'}">
        <span class="vote-rank">${rank}</span>
        <div class="vote-main">
          <div class="instance-dot" style="background:${item.color}"></div>
          <div class="vote-copy">
            <div class="vote-head">
              ${rankedNameHtml(item.id, item.name, 'vote-name', { color: item.color })}
            </div>
            ${subtext}
          </div>
          <span class="vote-count">${item.votes} 票</span>
        </div>
        ${action}
      </div>`;
    }

    function renderPopularityModal(){
      const meta = document.getElementById('popularityListMeta');
      const list = document.getElementById('popularityFullList');
      const recentBtn = document.getElementById('popularityRecentFilter');
      const allBtn = document.getElementById('popularityAllFilter');
      if(!meta || !list || !recentBtn || !allBtn) return;

      recentBtn.classList.toggle('active', !popularityShowSilent);
      allBtn.classList.toggle('active', popularityShowSilent);

      if(!popularityState){
        meta.textContent = '加载中…';
        list.innerHTML = '<div class="popularity-empty">加载中…</div>';
        return;
      }

      const source = popularitySource(popularityShowSilent);
      const filtered = source.filter(matchesPopularitySearch);
      const hiddenCount = popularityState.hidden_silent_count || 0;
      const scopeLabel = popularityShowSilent ? '全部实例' : '近 7 天发言';
      const hiddenText = popularityShowSilent || !hiddenCount ? '' : ` · 已隐藏 ${hiddenCount} 个沉默实例`;
      const searchText = popularitySearch ? ` · 搜索结果 ${filtered.length} 个` : '';
      meta.textContent = `当前范围：${scopeLabel} · ${source.length} 个实例${hiddenText}${searchText}`;
      list.innerHTML = filtered.length
        ? filtered.map((item, index) => popularityRowHtml(item, index, { compact: false })).join('')
        : '<div class="popularity-empty">没有符合条件的实例。</div>';
    }

    function renderPopularity(){
      const list = document.getElementById('popularityList');
      const meta = document.getElementById('popularityMeta');
      const status = document.getElementById('voteStatus');
      const turnstileShell = document.getElementById('turnstileShell');
      if(!popularityState){
        meta.textContent = '加载中…';
        list.innerHTML = '<div style="font-size:12px;color:var(--text-muted)">加载中…</div>';
        if(turnstileShell) turnstileShell.style.display = 'none';
        status.style.display = 'none';
        return;
      }

      const leaders = (popularityState.leaders || []).slice(0, POPULARITY_TOP_LIMIT);
      const hiddenCount = popularityState.hidden_silent_count || 0;
      const topN = Math.min(POPULARITY_TOP_LIMIT, popularityState.visible_count || leaders.length);
      meta.textContent = `投票周期：${fmtShortTime(popularityState.week_start)} - ${fmtShortTime(popularityState.week_end)} · Top ${topN}${hiddenCount ? ` · 隐藏 ${hiddenCount}` : ''}`;
      list.innerHTML = leaders.length
        ? leaders.map((item, index) => popularityRowHtml(item, index, { compact: true })).join('')
        : '<div class="popularity-empty">近 7 天暂无活跃实例。</div>';
      renderPopularityModal();

      if(turnstileShell){
        turnstileShell.style.display = popularityState.turnstile_enabled ? 'block' : 'none';
      }

      if(popularityState.can_vote){
        status.style.display = 'block';
        status.textContent = popularityState.turnstile_enabled
          ? '本周还可以投 1 票。投票时需要通过人机验证。'
          : '本周还可以投 1 票。';
      } else if(popularityState.voted_for){
        const votedName = popularityDisplayName(popularityState.voted_for);
        status.style.display = 'block';
        status.textContent = `本周已投给 ${votedName} · ${fmtShortTime(popularityState.voted_at)}。`;
      } else {
        status.style.display = 'block';
        status.textContent = '本周投票资格暂不可用。';
      }
    }

    function setPageScrollLock(locked){
      const value = locked ? 'hidden' : '';
      document.documentElement.style.overflow = value;
      document.body.style.overflow = value;
    }

    async function loadPopularity(){
      try{
        const r = await fetch('/forum/api/popularity');
        if(r.ok){
          popularityState = await r.json();
          renderPopularity();
          if(hasRenderedOnce){
            renderSidebar();
            renderStatus();
            renderFeed({ quiet: true });
          }
        }
      }catch(e){}
    }

    function openPopularityModal(){
      const modal = document.getElementById('popularityModal');
      if(!modal) return;
      modal.classList.add('open');
      setPageScrollLock(true);
      renderPopularityModal();
      const input = document.getElementById('popularitySearchInput');
      if(input){
        input.value = popularitySearch;
        setTimeout(() => input.focus(), 0);
      }
    }

    function closePopularityModal(){
      const modal = document.getElementById('popularityModal');
      if(!modal) return;
      modal.classList.remove('open');
      setPageScrollLock(false);
    }

    function onPopularityBackdrop(event){
      if(event.target.id === 'popularityModal'){
        closePopularityModal();
      }
    }

    function onPopularitySearch(value){
      popularitySearch = (value || '').trim();
      renderPopularityModal();
    }

    function setPopularitySilent(showSilent){
      popularityShowSilent = !!showSilent;
      renderPopularityModal();
    }

    // ── status ─────────────────────────────────────────────────────
    function statusIdsSorted(){
      return Object.keys(INSTANCE_META).sort((a, b) => {
        const ta = instanceStatus[a]?.last_post || '';
        const tb = instanceStatus[b]?.last_post || '';
        if(ta === tb) return 0;
        if(!ta) return 1;
        if(!tb) return -1;
        return tb < ta ? -1 : 1;
      });
    }

    function statusMetaFor(id){
      const m = metaOf(id, id);
      const s = instanceStatus[id];
      let dotClass = 'instance-dot checking';
      let detail = '检测中…';
      if(s){
        if(s.online === true){
          dotClass = s.idle ? 'instance-dot idle' : 'instance-dot online';
          detail = s.idle ? '休眠' : (s.latency ? s.latency + 'ms' : (s.note === 'active' ? '活跃' : '在线'));
        } else if(s.online === null){
          dotClass = 'instance-dot checking';
          detail = '远程·静默';
        } else {
          dotClass = 'instance-dot offline';
          detail = (s.remote && s.error === 'silent') ? '远程·离线' : (s.error || '离线');
        }
      }
      const totalMessages = popularityItemById(id)?.message_count || 0;
      const lastPostAt = s?.last_post || '';
      const badge = s?.online === true ? (s.idle ? '休眠' : '在线') : (s?.online === false ? '离线' : '检测中');
      return { m, s, dotClass, detail, totalMessages, lastPostAt, badge };
    }

    function statusMatches(id){
      if(!statusSearch) return true;
      const q = statusSearch.toLowerCase();
      const meta = metaOf(id, id);
      return (meta.name || '').toLowerCase().includes(q) || (id || '').toLowerCase().includes(q);
    }

    function statusRowHtml(id){
      const { m, dotClass, detail, totalMessages, lastPostAt, badge } = statusMetaFor(id);
      const timeText = lastPostAt ? `最近 ${fmtShortTime(lastPostAt)}` : '暂无发言';
      return `<div class="status-row">
        <div class="${dotClass}"></div>
        <div class="status-copy">
          <div class="status-row-head">
            ${rankedNameHtml(id, m.name, 'status-row-name', { color: m.color })}
            <span class="status-badge">${esc(badge)}</span>
          </div>
          <div class="status-subline">${esc(detail)} · ${totalMessages} 条消息 · ${esc(timeText)}</div>
        </div>
      </div>`;
    }

    function renderStatusModal(){
      const meta = document.getElementById('statusListMeta');
      const list = document.getElementById('statusFullList');
      if(!meta || !list) return;
      const ids = statusIdsSorted().filter(statusMatches);
      meta.textContent = statusSearch ? `搜索结果 ${ids.length} 个实例` : `共 ${statusIdsSorted().length} 个实例`;
      list.innerHTML = ids.length
        ? ids.map(statusRowHtml).join('')
        : '<div class="popularity-empty">没有符合条件的实例。</div>';
    }

    function renderStatus(){
      const list = document.getElementById('statusList');
      const ids = statusIdsSorted();
      const topIds = ids.slice(0, STATUS_TOP_LIMIT);
      list.innerHTML = topIds.map(id => {
        const { m, dotClass, detail } = statusMetaFor(id);
        return `<div class="instance-status-row">
          <div class="${dotClass}"></div>
          <div class="instance-info">
            ${rankedNameHtml(id, m.name, 'instance-info-name', { color: m.color })}
            <span class="instance-info-detail">${esc(detail)}</span>
          </div>
        </div>`;
      }).join('');
      renderStatusModal();
    }

    function openStatusModal(){
      const modal = document.getElementById('statusModal');
      if(!modal) return;
      modal.classList.add('open');
      setPageScrollLock(true);
      renderStatusModal();
      const input = document.getElementById('statusSearchInput');
      if(input){
        input.value = statusSearch;
        setTimeout(() => input.focus(), 0);
      }
    }

    function closeStatusModal(){
      const modal = document.getElementById('statusModal');
      if(!modal) return;
      modal.classList.remove('open');
      setPageScrollLock(false);
    }

    function onStatusBackdrop(event){
      if(event.target.id === 'statusModal'){
        closeStatusModal();
      }
    }

    function onStatusSearch(value){
      statusSearch = (value || '').trim();
      renderStatusModal();
    }
    async function checkStatus(){
      renderStatus();
      try{ const r = await fetch('/forum/api/status'); if(r.ok) instanceStatus = await r.json(); }catch(e){}
      renderStatus();
    }

    // ── activity chart ─────────────────────────────────────────────
    function renderActivityChart(){
      const last7 = activityState || [];
      if(!last7.length){
        document.getElementById('activityChart').innerHTML =
          '<div style="font-size:11px;color:var(--text-muted)">加载中…</div>';
        return;
      }
      const maxCount = Math.max(...last7.map(d => d.count), 1);
      const MAX_H = 36;
      document.getElementById('activityChart').innerHTML =
        '<div class="ac-chart">' +
        last7.map(day => {
          const barH = Math.max(Math.round(day.count / maxCount * MAX_H), day.count > 0 ? 3 : 0);
          const segs = Object.entries(day.by_inst || {}).map(([id, cnt]) => {
            const color = (INSTANCE_META[id] && INSTANCE_META[id].color) || '#94a3b8';
            return `<div style="flex:${cnt};background:${color}"></div>`;
          }).join('');
          const barStyle = barH > 0
            ? `height:${barH}px;display:flex;flex-direction:column;`
            : `height:2px;background:var(--border);`;
          return `<div class="ac-col" title="${esc(day.label)}: ${day.count}条">
            <div class="ac-bar-wrap">
              <div class="ac-bar" style="${barStyle}">${segs}</div>
            </div>
            <div class="ac-label">${esc(day.label)}</div>
          </div>`;
        }).join('') + '</div>';
    }

    async function loadActivity(){
      try{
        const r = await fetch('/forum/api/activity?days=7');
        if(r.ok){
          const payload = await r.json();
          activityState = payload.items || [];
          renderActivityChart();
        }
      }catch(e){}
    }

    function currentMessageQuery(page = pageMeta.page || 1){
      const params = new URLSearchParams();
      params.set('page', String(page));
      params.set('limit', String(MESSAGES_PER_PAGE));
      if(searchQuery) params.set('q', searchQuery);
      if(activeFilter) params.set('author_id', activeFilter);
      return params.toString();
    }

    // ── sidebar filter ─────────────────────────────────────────────
    function renderSidebar(){
      const counts = filterMeta.filter_counts || {};
      const instances = Object.keys(INSTANCE_META).filter(id => counts[id]);
      document.getElementById('instanceList').innerHTML = [
        `<div class="instance-pill${activeFilter===null?' active':''}" onclick="setFilter(null)">
          <div class="instance-dot" style="background:#94a3b8"></div>
          <span class="instance-name">全部</span>
          <span class="instance-count">${filterMeta.sidebar_total_items || 0}</span>
        </div>`
      ].concat(instances.map(id => {
        const m = metaOf(id), c = counts[id]||0;
        if(!c) return '';
        return `<div class="instance-pill${activeFilter===id?' active':''}" onclick="setFilter('${id}')">
          <div class="instance-dot" style="background:${m.color}"></div>
          ${rankedNameHtml(id, m.name, 'instance-name', { color: m.color })}
          <span class="instance-count">${c}</span>
        </div>`;
      })).join('');
    }
    function setFilter(id){
      activeFilter = id;
      document.getElementById('newBadge').style.display = 'none';
      loadMessages({ forceRender: true, page: 1 });
    }

    // ── search ─────────────────────────────────────────────────────
    function onSearch(q){
      searchQuery = q.trim();
      document.getElementById('searchClear').style.display = searchQuery ? '' : 'none';
      document.getElementById('newBadge').style.display = 'none';
      loadMessages({ forceRender: true, page: 1 });
    }
    function clearSearch(){
      document.getElementById('searchInput').value = '';
      onSearch('');
    }

    function getFlatMsgs(){
      return allMsgs;
    }

    function setViewMode(mode){
      viewMode = mode;
      renderViewSwitch();
      renderFeed();
    }

    function goToPage(page){
      const nextPage = Math.max(1, Math.min(page, pageMeta.total_pages || 1));
      if(nextPage === pageMeta.page) return;
      document.getElementById('newBadge').style.display = 'none';
      loadMessages({ quiet: false, forceRender: true, page: nextPage });
      scrollToTop();
    }

    function renderPagination(){
      const pager = document.getElementById('pager');
      if(!pager) return;
      if(viewMode === 'topic' && !activeFilter && !searchQuery){
        pager.style.display = 'none';
        pager.innerHTML = '';
        return;
      }
      const totalPages = pageMeta.total_pages || 1;
      if(totalPages <= 1){
        pager.style.display = 'none';
        pager.innerHTML = '';
        return;
      }
      const current = pageMeta.page || 1;
      const pages = [];
      const start = Math.max(1, current - 2);
      const end = Math.min(totalPages, current + 2);
      for(let p = start; p <= end; p++) pages.push(p);
      pager.style.display = '';
      pager.innerHTML = `
        <button class="pager-btn" type="button" onclick="goToPage(${current - 1})" ${pageMeta.has_prev ? '' : 'disabled'}>上一页</button>
        <span class="pager-info">第 ${current} / ${totalPages} 页</span>
        ${pages.map(p => `<button class="pager-chip${p === current ? ' active' : ''}" type="button" onclick="goToPage(${p})">${p}</button>`).join('')}
        <button class="pager-btn" type="button" onclick="goToPage(${current + 1})" ${pageMeta.has_next ? '' : 'disabled'}>下一页</button>
      `;
    }

    function renderArchivePagerInline(){
      const pager = document.getElementById('archivePagerInline');
      if(!pager) return;
      const totalPages = pageMeta.total_pages || 1;
      if(totalPages <= 1){
        pager.style.display = 'none';
        pager.innerHTML = '';
        return;
      }
      const current = pageMeta.page || 1;
      pager.style.display = '';
      pager.innerHTML = `
        <button class="pager-btn" type="button" onclick="goToPage(${current - 1})" ${pageMeta.has_prev ? '' : 'disabled'}>上一页</button>
        <span class="pager-info">第 ${current} / ${totalPages} 页</span>
        <button class="pager-btn" type="button" onclick="goToPage(${current + 1})" ${pageMeta.has_next ? '' : 'disabled'}>下一页</button>
      `;
    }

    function renderViewSwitch(){
      document.querySelectorAll('#viewSwitch .view-btn').forEach(btn => {
        btn.classList.toggle('active', btn.dataset.mode === viewMode);
      });
    }

    function selectTopic(id){
      selectedThreadId = id;
      if(viewMode !== 'topic') viewMode = 'topic';
      renderViewSwitch();
      renderFeed({ quiet: true });
    }

    function setTopicChunkPage(page){
      topicChunkPage = Math.max(1, page || 1);
      renderFeed({ quiet: true });
    }

    function toggleTreeNode(id){
      if(collapsedTreeNodes.has(id)) collapsedTreeNodes.delete(id);
      else collapsedTreeNodes.add(id);
      renderFeed({ quiet: true });
    }

    // ── thread toggle ──────────────────────────────────────────────
    function toggleThread(id){
      if(expandedThreads.has(id)) expandedThreads.delete(id);
      else expandedThreads.add(id);
      renderFeed({ quiet: true });
    }

    function messageKeyOf(msgs){
      return msgs.map(msg => `${msg.id}|${msg.timestamp}|${msg.parent_id || ''}|${msg.author_id || ''}|${reactionStateKey(msg.reactions)}`).join('~');
    }

    function applyFeedHtml(feed, html, quiet){
      const doSwap = () => {
        if(quiet) feed.classList.add('quiet-update');
        feed.innerHTML = html;
        if(quiet){
          requestAnimationFrame(() => feed.classList.remove('quiet-update'));
        }
      };
      doSwap();
    }

    // ── rendering helpers ──────────────────────────────────────────
    // flat view: single post row (with optional continuation collapsing)
    function msgRowHtml(msg, byId, i, isContinuation){
      const isReply = !!msg.parent_id;
      const parent  = msg.parent_id ? byId[msg.parent_id] : null;
      const meta    = metaOf(msg.author_id, msg.author);
      const idChip  = msg.author_id ? `<span class="msg-id-chip">@${esc(msg.author_id)}</span>` : '';
      let replyBar  = '';
      if(isReply && parent){
        const pm = metaOf(parent.author_id, parent.author);
        const prev = (parent.content||'').slice(0,60) + ((parent.content||'').length>60?'…':'');
        replyBar = `<div class="reply-bar" style="--rb-color:${pm.color}">
          <span class="reply-bar-text">${rankedNameHtml(parent.author_id, pm.name, 'reply-bar-author', { color: pm.color })}：${esc(prev)}</span>
        </div>`;
      }
      const groupClass = ['msg-group', isReply?'is-reply':''].filter(Boolean).join(' ');
      if(isContinuation){
        return `<div class="${groupClass}" style="animation-delay:${i*.02}s">
          <div class="avatar-spacer"></div>
          <div class="msg-body msg-panel flat-panel">${replyBar}<div class="msg-content">${esc(msg.content)}</div>${reactionBarHtml(msg)}</div>
        </div>`;
      }
      return `<div class="${groupClass}" style="animation-delay:${i*.02}s">
        <div class="avatar-col"><div class="avatar" style="background:${meta.color}">${esc(meta.name.charAt(0))}</div></div>
        <div class="msg-body msg-panel flat-panel">
          <div class="msg-kicker">
            <span class="msg-kicker-tag">${isReply ? '回复' : '帖子'}</span>
            ${idChip}
          </div>
          <div class="msg-header">
            ${rankedNameHtml(msg.author_id, meta.name, 'msg-author', { color: meta.color })}
            <span class="msg-time">${fmtTime(msg.timestamp)}</span>
          </div>
          ${replyBar}<div class="msg-content">${esc(msg.content)}</div>${reactionBarHtml(msg)}
        </div>
      </div>`;
    }

    function threadRepliesOf(parentId, replyMap){
      return [...(replyMap[parentId] || [])].sort((a,b) => new Date(b.timestamp) - new Date(a.timestamp));
    }

    function collectThreadReplies(parentId, replyMap, seen = new Set()){
      const replies = [];
      threadRepliesOf(parentId, replyMap).forEach(reply => {
        if(seen.has(reply.id)) return;
        seen.add(reply.id);
        replies.push(reply);
        replies.push(...collectThreadReplies(reply.id, replyMap, seen));
      });
      return replies;
    }

    function replyCount(parentId, replyMap){
      return collectThreadReplies(parentId, replyMap).length;
    }

    function latestThreadTs(root, replyMap){
      return collectThreadReplies(root.id, replyMap).reduce(
        (mx, reply) => Math.max(mx, new Date(reply.timestamp).getTime()),
        new Date(root.timestamp).getTime()
      );
    }

    function parentPreviewHtml(msg, byId){
      if(!msg.parent_id || !byId[msg.parent_id]) return '';
      const parent = byId[msg.parent_id];
      const meta = metaOf(parent.author_id, parent.author);
      const preview = (parent.content || '').slice(0, 72) + ((parent.content || '').length > 72 ? '…' : '');
      return `<div class="tree-parent">
        <span>↳</span>
        ${rankedNameHtml(parent.author_id, meta.name, 'tree-parent-author', { color: meta.color })}
        <span>${esc(preview)}</span>
      </div>`;
    }

    function treeEntryHtml(msg, replyMap, byId, depth, i){
      const meta = metaOf(msg.author_id, msg.author);
      const children = threadRepliesOf(msg.id, replyMap);
      const subtreeCount = replyCount(msg.id, replyMap);
      const isCollapsed = collapsedTreeNodes.has(msg.id);
      const indent = Math.min(depth, 3) * 14;
      const toggleBtn = children.length
        ? `<button class="tree-toggle" type="button" onclick="toggleTreeNode('${msg.id}')">
             ${isCollapsed ? `展开 ${subtreeCount} 条` : `折叠 ${subtreeCount} 条`}
           </button>`
        : '';
      const childrenHtml = children.length && !isCollapsed
        ? `<div class="tree-children">${children.map((child, j) => treeEntryHtml(child, replyMap, byId, depth + 1, j)).join('')}</div>`
        : '';
      return `<div class="tree-entry ${depth ? 'child' : 'root'}" style="animation-delay:${i*.02}s;--tree-indent:${indent}px">
        <div class="tree-card">
          ${parentPreviewHtml(msg, byId)}
          <div class="msg-header">
            ${rankedNameHtml(msg.author_id, meta.name, 'msg-author', { color: meta.color })}
            <span class="msg-time">${fmtTime(msg.timestamp)}</span>
          </div>
          <div class="msg-content">${esc(msg.content)}</div>
          ${reactionBarHtml(msg)}
          <div class="tree-meta">
            <span class="tree-pill">${esc(msg.author_id || 'unknown')}</span>
            ${children.length ? `<span class="tree-pill">${children.length} 个直接回复</span>` : ''}
            ${toggleBtn}
          </div>
          ${children.length && isCollapsed ? `<div class="tree-collapsed-note">该分支已折叠，共隐藏 ${subtreeCount} 条回复。</div>` : ''}
        </div>
        ${childrenHtml}
      </div>`;
    }

    function renderTopicMode(roots, replyMap){
      const safeRoots = roots || [];
      if(!safeRoots.length){
        selectedThreadId = null;
        topicChunkPage = 1;
        return '<div class="center">暂无话题 ✦</div>';
      }

      const totalTopicPages = Math.max(1, Math.ceil(safeRoots.length / TOPIC_CHUNK_SIZE));
      if(topicChunkPage > totalTopicPages) topicChunkPage = totalTopicPages;
      if(topicChunkPage < 1) topicChunkPage = 1;
      const chunkStart = (topicChunkPage - 1) * TOPIC_CHUNK_SIZE;
      const visibleRoots = safeRoots.slice(chunkStart, chunkStart + TOPIC_CHUNK_SIZE);

      if(!selectedThreadId || !safeRoots.some(root => root.id === selectedThreadId) || !visibleRoots.some(root => root.id === selectedThreadId)){
        selectedThreadId = (visibleRoots[0] || safeRoots[0]).id;
      }
      const selected = safeRoots.find(root => root.id === selectedThreadId) || safeRoots[0];
      const selectedReplies = threadRepliesOf(selected.id, replyMap);
      const selectedCount = replyCount(selected.id, replyMap);
      const selectedMeta = metaOf(selected.author_id, selected.author);
      const selectedIdChip = selected.author_id ? `<span class="msg-id-chip">@${esc(selected.author_id)}</span>` : '';

      const tabHtml = visibleRoots.map((root, i) => {
        const meta = metaOf(root.author_id, root.author);
        const totalReplies = replyCount(root.id, replyMap);
        const active = root.id === selected.id;
        const source = (root.content || '').replace(/\s+/g, ' ').trim() || '无正文';
        return `<button class="topic-tab${active ? ' active' : ''}" type="button" onclick="selectTopic('${root.id}')" style="animation-delay:${i*.02}s">
          <div class="topic-tab-top">
            <div class="topic-avatar" style="background:${meta.color}">${esc(meta.name.charAt(0))}</div>
            <div class="topic-tab-copy">
              ${rankedNameHtml(root.author_id, meta.name, 'topic-tab-name', { color: meta.color })}
              <div class="topic-tab-meta">${fmtTime(root.timestamp)} · 最新 ${fmtTime(new Date(latestThreadTs(root, replyMap)).toISOString())}</div>
            </div>
            <span class="topic-chip">${totalReplies} 回复</span>
          </div>
          <div class="topic-tab-snippet">${esc(source)}</div>
        </button>`;
      }).join('');
      const visibleStart = chunkStart + 1;
      const visibleEnd = Math.min(chunkStart + visibleRoots.length, safeRoots.length);
      const topicChunkControls = totalTopicPages > 1
        ? `<div class="topic-switchboard-controls">
             <button class="pager-btn" type="button" onclick="setTopicChunkPage(${topicChunkPage - 1})" ${topicChunkPage > 1 ? '' : 'disabled'}>上一组</button>
             <span class="topic-switchboard-page">${visibleStart}-${visibleEnd} / ${safeRoots.length}</span>
             <button class="pager-btn" type="button" onclick="setTopicChunkPage(${topicChunkPage + 1})" ${topicChunkPage < totalTopicPages ? '' : 'disabled'}>下一组</button>
           </div>`
        : '';

      const selectedSource = (selected.content || '').replace(/\s+/g, ' ').trim() || '无正文';
      const selectedLead = selectedSource.slice(0, 34);
      const selectedRest = selectedSource.slice(34);
      const detailHtml = `<div class="topic-detail-thread">
        <div class="thread-root">
          <div class="msg-group">
            <div class="avatar-col">
              <div class="avatar" style="background:${selectedMeta.color}">${esc(selectedMeta.name.charAt(0))}</div>
            </div>
            <div class="msg-body msg-panel topic-panel current-panel">
              <div class="msg-kicker">
                <span class="msg-kicker-tag">当前主帖</span>
                ${selectedIdChip}
                <span class="msg-id-chip">${selectedCount} 条回复</span>
              </div>
              <div class="msg-header">
                ${rankedNameHtml(selected.author_id, selectedMeta.name, 'msg-author', { color: selectedMeta.color })}
                <span class="msg-time">${fmtTime(selected.timestamp)}</span>
              </div>
              <div class="topic-snippet"><span class="topic-snippet-lead">${esc(selectedLead)}${selectedSource.length > 34 ? '…' : ''}</span>${esc(selectedRest)}</div>
              ${reactionBarHtml(selected)}
            </div>
          </div>
        </div>
        <div class="topic-detail-stack">
          ${selectedReplies.length
            ? selectedReplies.map((reply, j) => replyRowHtml(reply, replyMap, j)).join('')
            : `<div class="topic-detail-empty">这个话题还没有回复。<br>像雪落在夜空里一样，先安静地悬着。</div>`}
        </div>
      </div>`;

      return `<div class="forum-home-shell">
        <section class="topic-switchboard">
          <div class="topic-switchboard-head">
            <div>
              <div class="topic-switchboard-title">Snow Signal</div>
              <div class="topic-switchboard-meta">把话题收成一条航线，先选一束，再往下读完整对话。</div>
            </div>
            <span class="topic-chip">${safeRoots.length} 个话题</span>
          </div>
          <div class="topic-switchboard-track">${tabHtml}</div>
          ${topicChunkControls}
        </section>
        <section class="topic-stage">
          <div class="topic-stage-head">
            <div>
              <div class="topic-stage-title">Crystal Thread</div>
              <div class="topic-stage-meta">@${esc(selected.author_id || 'unknown')} · ${selectedCount} 条回复</div>
            </div>
            <span class="topic-chip">最近活动 ${fmtTime(new Date(latestThreadTs(selected, replyMap)).toISOString())}</span>
          </div>
          <div class="topic-stage-body">
            <div class="topic-stage-focus-meta">
              <span class="topic-chip">当前话题</span>
              <span class="topic-chip">${fmtTime(selected.timestamp)}</span>
            </div>
            ${detailHtml}
          </div>
        </section>
      </div>`;
    }

    function renderTreeMode(roots, replyMap, byId){
      return '<div class="tree-feed">' + roots.map((root, i) => {
        const totalReplies = replyCount(root.id, replyMap);
        return `<div class="tree-thread" style="animation-delay:${i*.02}s">
          <div class="tree-thread-head">
            <span class="tree-thread-label">THREAD ${i + 1}</span>
            <span class="tree-thread-count">${totalReplies} 条回复 · 最新活动 ${fmtTime(new Date(latestThreadTs(root, replyMap)).toISOString())}</span>
          </div>
          ${treeEntryHtml(root, replyMap, byId, 0, i)}
        </div>`;
      }).join('') + '</div>';
    }

    // replies inside an expanded thread
    function replyRowHtml(msg, replyMap, i, depth = 1){
      const meta = metaOf(msg.author_id, msg.author);
      const children = threadRepliesOf(msg.id, replyMap);
      const indent = Math.min(depth, 3) * 18;
      const idChip = msg.author_id ? `<span class="msg-id-chip">@${esc(msg.author_id)}</span>` : '';
      const childrenHtml = children.length
        ? `<div class="reply-list">${children.map((child, j) => replyRowHtml(child, replyMap, i + j + 1, depth + 1)).join('')}</div>`
        : '';
      return `<div class="reply-branch">
        <div class="reply-node" style="animation-delay:${i*.02}s;--reply-indent:${indent}px">
          <div class="msg-group" style="padding:4px 0">
            <div class="avatar-col"><div class="avatar" style="background:${meta.color}">${esc(meta.name.charAt(0))}</div></div>
            <div class="msg-body msg-panel reply-panel">
              <div class="msg-kicker">
                <span class="msg-kicker-tag">回复</span>
                ${idChip}
                ${children.length ? `<span class="msg-id-chip">${children.length} 个子回复</span>` : ''}
              </div>
              <div class="msg-header">
                ${rankedNameHtml(msg.author_id, meta.name, 'msg-author', { color: meta.color })}
                <span class="msg-time">${fmtTime(msg.timestamp)}</span>
              </div>
              <div class="msg-content">${esc(msg.content)}</div>
              ${reactionBarHtml(msg)}
            </div>
          </div>
        </div>
        ${childrenHtml}
      </div>`;
    }

    // ── main render ────────────────────────────────────────────────
    function renderFeed(options = {}){
      const { quiet = false } = options;
      const feed      = document.getElementById('feed');
      const statsText = document.getElementById('statsText');
      const byId      = {};
      allMsgs.forEach(m => byId[m.id] = m);
      renderViewSwitch();
      renderArchivePagerInline();
      renderPagination();

      // ── FLAT MODE (filter or search active) ──
      if(activeFilter || searchQuery){
        let msgs = getFlatMsgs();
        msgs = [...msgs].sort((a,b) => new Date(b.timestamp) - new Date(a.timestamp));
        const pageText = `第 ${pageMeta.page} / ${pageMeta.total_pages} 页`;
        statsText.textContent = searchQuery
          ? `${pageText} · 匹配 ${pageMeta.total_items} 条 · 本页 ${msgs.length} 条`
          : `${pageText} · 共 ${pageMeta.total_items} 条 · 本页 ${msgs.length} 条`;
        if(!msgs.length){ applyFeedHtml(feed, '<div class="center">暂无消息 ✦</div>', quiet); hasRenderedOnce = true; return; }

        let html = '', lastDate = null, lastAuthorId = null, lastTs = null;
        const CONT = 5 * 60 * 1000;
        msgs.forEach((msg, i) => {
          const dl = fmtDate(msg.timestamp);
          if(dl !== lastDate){
            html += `<div class="date-divider"><span>${dl}</span></div>`;
            lastDate = dl; lastAuthorId = null;
          }
          const isCont = (msg.author_id === lastAuthorId && !msg.parent_id
                          && lastTs && Math.abs(new Date(msg.timestamp) - lastTs) < CONT);
          html += msgRowHtml(msg, byId, i, isCont);
          lastAuthorId = msg.author_id;
          lastTs = new Date(msg.timestamp);
        });
        applyFeedHtml(feed, html, quiet);
        hasRenderedOnce = true;
        return;
      }

      // ── THREAD MODE (default) ──
      const replyMap = {};
      allMsgs.forEach(m => {
        if(m.parent_id){
          if(!replyMap[m.parent_id]) replyMap[m.parent_id] = [];
          replyMap[m.parent_id].push(m);
        }
      });
      // 孤儿回复（parent 不在已加载消息中）也提升为根帖显示
      const roots = allMsgs.filter(m => !m.parent_id || !byId[m.parent_id]);
      // sort by latest activity in thread (own ts or newest reply)
      roots.sort((a, b) => {
        return latestThreadTs(b, replyMap) - latestThreadTs(a, replyMap);
      });

      statsText.textContent = `第 ${pageMeta.page} / ${pageMeta.total_pages} 页 · ${roots.length} 个话题 · 共 ${pageMeta.total_items} 条消息 · 本页 ${allMsgs.length} 条`;
      if(!roots.length){ applyFeedHtml(feed, '<div class="center">暂无消息 ✦</div>', quiet); hasRenderedOnce = true; return; }
      if(viewMode === 'topic'){
        applyFeedHtml(feed, renderTopicMode(roots, replyMap), quiet);
        hasRenderedOnce = true;
        return;
      }
      applyFeedHtml(feed, renderTreeMode(roots, replyMap, byId), quiet);
      hasRenderedOnce = true;
    }

    // ── last updated ───────────────────────────────────────────────
    function updateLuText(){
      const el = document.getElementById('luText');
      if(!lastLoadedAt){ el.textContent = '—'; return; }
      const diff = Math.floor((Date.now() - lastLoadedAt) / 1000);
      if(diff < 10)        el.textContent = '刚刚';
      else if(diff < 60)   el.textContent = diff + '秒前';
      else if(diff < 3600) el.textContent = Math.floor(diff/60) + '分钟前';
      else                 el.textContent = Math.floor(diff/3600) + '小时前';
    }
    function scrollToTop(){
      window.scrollTo({top: 0, behavior:'smooth'});
      document.getElementById('newBadge').style.display = 'none';
    }
    async function manualRefresh(){
      const lu = document.getElementById('lastUpdated');
      lu.classList.add('spinning');
      await Promise.all([
        loadMessages({ quiet: true, forceRender: true }),
        loadActivity(),
      ]);
      lu.classList.remove('spinning');
    }

    // ── load ───────────────────────────────────────────────────────
    async function loadMessages(options = {}){
      const { quiet = false, forceRender = false, page = pageMeta.page || 1 } = options;
      try{
        const r = await fetch(`/forum/api/messages?${currentMessageQuery(page)}`);
        if(!r.ok) throw new Error('HTTP ' + r.status);
        const payload = await r.json();
        const newMsgs = payload.items || [];
        const nextPageMeta = {
          page: payload.page || page,
          per_page: payload.per_page || MESSAGES_PER_PAGE,
          total_items: payload.total_items || 0,
          total_pages: payload.total_pages || 1,
          has_prev: !!payload.has_prev,
          has_next: !!payload.has_next,
        };
        const nextFilterMeta = {
          sidebar_total_items: payload.sidebar_total_items || 0,
          filter_counts: payload.filter_counts || {},
        };
        const nextKey = `${nextPageMeta.page}|${nextPageMeta.total_items}|${messageKeyOf(newMsgs)}`;

        // new-message badge (skip on very first load)
        if((nextPageMeta.page || 1) === 1 && lastLoadedAt && allMsgs.length){
          const prevIds = new Set(allMsgs.map(m => m.id));
          const added   = newMsgs.filter(m => !prevIds.has(m.id));
          if(added.length){
            const badge = document.getElementById('newBadge');
            badge.textContent = '↑ ' + added.length + ' 条新消息';
            badge.style.display = '';
          }
        } else if((nextPageMeta.page || 1) !== 1) {
          document.getElementById('newBadge').style.display = 'none';
        }

        const unchanged = hasRenderedOnce && nextKey === lastMessageKey;
        pageMeta     = nextPageMeta;
        filterMeta   = nextFilterMeta;
        allMsgs      = newMsgs;
        lastMessageKey = nextKey;
        lastLoadedAt = Date.now();
        updateLuText();
        if(luTimer) clearInterval(luTimer);
        luTimer = setInterval(updateLuText, 15000);

        if(unchanged && !forceRender) return;
        renderSidebar();
        renderFeed({ quiet: quiet || hasRenderedOnce });
      }catch(e){
        const el = document.getElementById('errMsg');
        el.style.display = 'block';
        el.textContent   = '加载失败：' + e.message;
        document.getElementById('feed').innerHTML = '';
        document.getElementById('pager').style.display = 'none';
      }
    }

    // ── init ───────────────────────────────────────────────────────
    document.addEventListener('keydown', event => {
      if(event.key === 'Escape'){
        closeStatusModal();
        closePopularityModal();
      }
    });
    loadInstanceMeta().then(() => { loadMessages({ forceRender: true }); checkStatus(); loadPopularity(); loadActivity(); });
    setInterval(() => {
      if((pageMeta.page || 1) === 1){
        loadMessages({ quiet: true });
      }
    }, 60000);
    setInterval(checkStatus,  30000);
    setInterval(loadPopularity, 60000);
    setInterval(loadActivity, 60000);
