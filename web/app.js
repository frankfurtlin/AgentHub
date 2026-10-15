/* ============================================================================
   AgentHub 前端
   ----------------------------------------------------------------------------
   两个窗口共用这一份脚本，用 URL 上的 ?view=ball|panel 区分。

   数据是 Python 侧推过来的（window.AgentHub.receive），不是前端定时拉——
   主循环每 1.5 秒推一次，两边节奏一致，也不用担心前端轮询把桥打满。
   ========================================================================= */

(function () {
  'use strict';

  // 视图从 URL 的 hash 读（pywebview 靠文件路径加载页面，hash 比 query 稳）
  var hashView = (location.hash || '').replace(/^#/, '');
  var VIEW = (hashView || new URLSearchParams(location.search).get('view') || 'ball').toLowerCase();

  // ---------------------------------------------------------------- 主题
  // tokens.css 只认 <html data-theme="light|dark">，由这里写上去。
  // 配置里的 theme 可以是 auto / light / dark，auto 就跟着系统偏好走。

  var darkQuery = window.matchMedia('(prefers-color-scheme: dark)');
  var themePref = 'auto';

  function applyTheme() {
    var resolved = themePref === 'auto'
      ? (darkQuery.matches ? 'dark' : 'light')
      : themePref;
    document.documentElement.dataset.theme = resolved;
  }

  function setThemePref(value) {
    themePref = (value === 'light' || value === 'dark') ? value : 'auto';
    applyTheme();
  }

  // 配置要走一趟桥才知道，先按系统偏好上一版，别让首帧光着
  applyTheme();
  darkQuery.addEventListener('change', function () {
    if (themePref === 'auto') { applyTheme(); }
  });

  var state = {
    snapshot: null,
    icons: {},        // agent key -> data URI
    mounted: false,   // 视图 DOM 挂好了没
    themeDone: false, // 主题偏好取到了没
    dataDone: false,  // 首份快照取到了没
    nodes: {}         // 挂载后的 DOM 引用
  };

  // ---------------------------------------------------------------- 小工具

  function api() {
    return (window.pywebview && window.pywebview.api) || null;
  }

  function el(tag, cls, text) {
    var node = document.createElement(tag);
    if (cls) { node.className = cls; }
    if (text != null) { node.textContent = text; }
    return node;
  }

  function svgEl(tag, attrs) {
    var node = document.createElementNS('http://www.w3.org/2000/svg', tag);
    for (var key in attrs) { node.setAttribute(key, attrs[key]); }
    return node;
  }

  function escapeText(value) {
    return value == null ? '' : String(value);
  }

  /* 状态点 / 胶囊的颜色，和 style.css 里的变量一一对应 */
  function statusColor(agent) {
    if (!agent.running) { return 'var(--text-faint)'; }
    if (agent.level === 2) { return 'var(--warn-dot)'; }
    if (agent.level === 1) { return 'var(--unread-dot)'; }
    if (agent.status === '输出中') { return 'var(--run-dot)'; }
    return 'var(--idle-fg)';
  }

  /* 整体告警级别：2 有人在等 → 1 有新结果 → 0 平静 */
  function topLevel(totals) {
    if (!totals) { return 0; }
    if (totals.waiting) { return 2; }
    if (totals.unread) { return 1; }
    return 0;
  }

  // ---------------------------------------------------------------- 悬浮球

  /* 2×2 点阵：一个工具一个点，位置固定，颜色随状态变 */
  var DOT_POS = [[30.5, 30.5], [45.5, 30.5], [30.5, 45.5], [45.5, 45.5]];

  function mountBall() {
    var node = document.getElementById('tpl-ball').content.firstElementChild.cloneNode(true);
    document.body.appendChild(node);
    state.nodes.ball = node;
    bindBallDrag(node);
  }

  function renderBall(snapshot) {
    var node = state.nodes.ball;
    if (!node) { return; }

    var agents = snapshot.agents || [];
    var totals = snapshot.totals || {};
    var level = topLevel(totals);

    // 状态点
    var dots = node.querySelector('.ball__dots');
    dots.innerHTML = '';
    agents.forEach(function (agent, index) {
      var pos = DOT_POS[index] || [38, 38];
      dots.appendChild(svgEl('circle', {
        cx: pos[0], cy: pos[1], r: 5, fill: statusColor(agent)
      }));
    });

    node.dataset.level = String(level);
    node.dataset.pulse = level === 2 ? '1' : '0';

    // 环的颜色跟着最高级别走；平静时用 token 里的默认蓝灰
    if (level === 2) {
      node.style.setProperty('--ball-ring', 'var(--warn-dot)');
    } else if (level === 1) {
      node.style.setProperty('--ball-ring', 'var(--unread-dot)');
    } else {
      node.style.removeProperty('--ball-ring');
    }

    // 右上角徽标：有东西要处理才出现
    var badge = node.querySelector('.ball__badge');
    var count = totals.waiting || totals.unread || 0;
    badge.textContent = escapeText(count);
    badge.dataset.show = count > 0 ? '1' : '0';
    badge.dataset.tone = level === 2 ? 'warn' : 'unread';
  }

  /* 点击与拖动共存：按住后先不算拖动，位移超过阈值才交给系统去拖。
     这样轻点 = 展开面板，按住拖 = 挪位置，两者不打架。 */
  function bindBallDrag(node) {
    var press = null;

    node.addEventListener('mousedown', function (event) {
      if (event.button !== 0) { return; }
      press = { x: event.screenX, y: event.screenY, dragging: false, native: false };
      event.preventDefault();
    });

    window.addEventListener('mousemove', function (event) {
      if (!press || press.dragging) { return; }
      var dx = event.screenX - press.x;
      var dy = event.screenY - press.y;
      if (Math.sqrt(dx * dx + dy * dy) < 4) { return; }

      press.dragging = true;
      document.body.classList.add('is-dragging');

      var bridge = api();
      if (!bridge) { return; }
      // 交给系统接管最好：鼠标移出球体也不会丢事件
      Promise.resolve(bridge.begin_drag()).then(function (handled) {
        press.native = !!handled;
        if (!handled) { fallbackDrag(press); }
      }, function () { fallbackDrag(press); });
    });

    window.addEventListener('mouseup', function () {
      if (!press) { return; }
      var wasDrag = press.dragging;
      press = null;
      document.body.classList.remove('is-dragging');
      if (wasDrag) {
        var bridge = api();
        if (bridge) { bridge.save_ball_pos(); }
      } else {
        togglePanel();
      }
    });

    node.addEventListener('contextmenu', function (event) { event.preventDefault(); });
  }

  /* 系统不支持原生拖动时的退路：自己算位移。窗口小、手快会丢事件，
     所以只作兜底。 */
  var fallbackState = null;

  function fallbackDrag(press) {
    var bridge = api();
    if (!bridge || !bridge.ball_pos) { return; }
    Promise.resolve(bridge.ball_pos()).then(function (pos) {
      if (!pos) { return; }
      fallbackState = { originX: pos[0], originY: pos[1], press: press };

      function onMove(event) {
        if (!fallbackState) { return; }
        var dx = event.screenX - press.x;
        var dy = event.screenY - press.y;
        bridge.move_ball(fallbackState.originX + dx, fallbackState.originY + dy);
      }
      function onUp() {
        fallbackState = null;
        window.removeEventListener('mousemove', onMove);
        window.removeEventListener('mouseup', onUp);
      }
      window.addEventListener('mousemove', onMove);
      window.addEventListener('mouseup', onUp);
    });
  }

  function togglePanel() {
    var bridge = api();
    if (bridge) { bridge.toggle_panel(); }
  }

  // ---------------------------------------------------------------- 状态面板

  function mountPanel() {
    var node = document.getElementById('tpl-panel').content.firstElementChild.cloneNode(true);
    document.body.appendChild(node);

    state.nodes.panel = node;
    state.nodes.sub = node.querySelector('#sub');
    state.nodes.cards = node.querySelector('#cards');
    state.nodes.foot = node.querySelector('#foot');
    state.nodes.footText = node.querySelector('#foot-text');

    node.querySelector('#close').addEventListener('click', function () {
      var bridge = api();
      if (bridge) { bridge.hide_panel(); }
    });

    document.addEventListener('keydown', function (event) {
      if (event.key === 'Escape') {
        var bridge = api();
        if (bridge) { bridge.hide_panel(); }
      }
    });
  }

  function renderPanel(snapshot) {
    if (!state.nodes.panel) { return; }

    var agents = snapshot.agents || [];
    var totals = snapshot.totals || {};

    // 头部副标题
    var sub = state.nodes.sub;
    sub.innerHTML = '';
    sub.appendChild(document.createTextNode(
      (totals.running || 0) + '/' + agents.length + ' 运行中'));
    if (totals.waiting) {
      sub.appendChild(document.createTextNode(' · '));
      sub.appendChild(el('b', null, totals.waiting + ' 个待处理'));
    }

    // 卡片
    var list = state.nodes.cards;
    list.innerHTML = '';
    agents.forEach(function (agent) {
      list.appendChild(buildCard(agent));
    });

    renderFoot(agents, totals);
  }

  function buildCard(agent) {
    var card = el('div', 'card');
    card.dataset.key = agent.key;
    card.dataset.level = String(agent.level);
    card.tabIndex = 0;
    card.setAttribute('role', 'button');
    card.setAttribute('aria-label', agent.name + '，' + agent.status);
    if (!agent.running) { card.classList.add('card--off'); }

    // —— 图标：拿到真实图标就贴图，否则用品牌色 + 首字母兜底
    var icon = el('div', 'card__icon');
    icon.style.setProperty('--icon-fill', agent.fill || 'var(--idle-bg)');
    icon.style.setProperty('--icon-stroke', agent.stroke || 'var(--idle-fg)');
    icon.style.setProperty('--icon-fill-dark', agent.fillDark || agent.fill || 'var(--idle-bg)');
    icon.style.setProperty('--icon-stroke-dark', agent.strokeDark || agent.stroke || 'var(--idle-fg)');

    var uri = state.icons[agent.key];
    if (uri) {
      var img = el('img');
      img.src = uri;
      img.alt = '';
      icon.appendChild(img);
    } else {
      icon.textContent = agent.glyph || (agent.name || '?').slice(0, 1);
    }

    // —— 文字
    var body = el('div', 'card__body');
    var top = el('div', 'card__top');
    top.appendChild(el('span', 'card__name', agent.name));
    top.appendChild(el('span', 'pill',
      agent.status + (agent.hint ? ' · ' + agent.hint : '')));
    body.appendChild(top);

    var detail = el('div', 'card__detail', agent.detail || '');
    body.appendChild(detail);

    // —— 右箭头
    var arrow = el('div', 'card__arrow');
    arrow.innerHTML =
      '<svg width="7" height="11" viewBox="0 0 7 11" aria-hidden="true">' +
      '<path d="M1 1 L6 5.5 L1 10" fill="none" stroke="currentColor" ' +
      'stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg>';

    card.appendChild(icon);
    card.appendChild(body);
    card.appendChild(arrow);

    function open() {
      var bridge = api();
      if (!bridge || !agent.running) { return; }
      bridge.activate(agent.key);
      bridge.hide_panel();
    }

    card.addEventListener('click', open);
    card.addEventListener('keydown', function (event) {
      if (event.key === 'Enter' || event.key === ' ') {
        event.preventDefault();
        open();
      }
    });

    return card;
  }

  function renderFoot(agents, totals) {
    var foot = state.nodes.foot;
    var text = state.nodes.footText;

    if (totals.waiting) {
      text.textContent = '有 ' + totals.waiting + ' 个任务在等你确认，点卡片去处理';
      foot.dataset.tone = 'warn';
      return;
    }
    if (totals.unread) {
      text.textContent = '有 ' + totals.unread + ' 个工具出了新结果';
      foot.dataset.tone = 'unread';
      return;
    }

    var running = agents.filter(function (agent) { return agent.running; }).length;
    text.textContent = running ? '都没有在等你' : '四个工具都没启动';
    foot.dataset.tone = 'ok';
  }

  // ---------------------------------------------------------------- 渲染分发

  function render(snapshot) {
    if (!snapshot) { return; }
    if (VIEW === 'panel') { renderPanel(snapshot); } else { renderBall(snapshot); }
  }

  // ---------------------------------------------------------------- 与 Python 通信

  window.AgentHub = {
    /* Python 主循环推过来的入口 */
    receive: function (payload) {
      if (!payload) { return; }
      if (payload.snapshot) {
        state.snapshot = payload.snapshot;
        render(payload.snapshot);
        // 顺手补图标：首轮扫描可能还没就绪，loadIcons 自己会跳过已有的、并且有重试上限
        loadIcons(payload.snapshot.agents || []);
      }
      if (payload.alert) { onAlert(payload.alert); }
    },

    render: function () { render(state.snapshot); }
  };

  /* 告警：只做视觉。提示音交给 Python 侧发——WebView 的自动播放策略
     未必允许无交互发声，而且换平台时要分别处理。 */
  function onAlert(alert) {
    var node = state.nodes.ball;
    if (!node || !alert) { return; }
    node.dataset.pulse = '1';
    // 重放一次脉冲动画：先摘掉属性强制重排，再挂回去
    var pulse = node.querySelector('.ball__pulse');
    if (pulse) {
      pulse.style.animation = 'none';
      void pulse.offsetWidth;
      pulse.style.animation = '';
    }
  }

  // ---------------------------------------------------------------- 启动

  var iconTries = {};       // agent key -> 已经试过几次

  async function loadIcons(agents) {
    var bridge = api();
    if (!bridge) { return; }

    var changed = false;
    for (var i = 0; i < agents.length; i++) {
      var key = agents[i].key;
      if (state.icons[key]) { continue; }
      // 拿不到多半是「首轮扫描还没跑完、不知道这个工具的可执行文件在哪」，
      // 所以下轮推送再试；但也要有个上限，别为一个抠不出来的图标一直问下去。
      if ((iconTries[key] || 0) >= 5) { continue; }
      iconTries[key] = (iconTries[key] || 0) + 1;
      try {
        var uri = await bridge.icon(key, 64);
        if (uri) { state.icons[key] = uri; changed = true; }
      } catch (err) { /* 拿不到就用兜底字符，不阻断 */ }
    }
    if (changed) { render(state.snapshot); }
  }

  /* 视图挂载只做一次。跟「桥那边的东西取到了没有」是两件事：
     桥可能比 pywebviewready 事件晚到，也可能早到，两种时序都得扛住。 */
  function mountView() {
    if (state.mounted) { return; }
    state.mounted = true;
    document.body.dataset.view = VIEW;
    if (VIEW === 'panel') { mountPanel(); } else { mountBall(); }
  }

  async function boot() {
    mountView();

    // 判断桥「能不能用」不能只看向量在不在：pywebview 注入时 api.js 先把
    // window.pywebview 建出来（api 是个空对象），finish.js 才把方法挂上去。
    // 中间那一小段里 api() 返回的是个真值但不是能用的桥——拿它调 config()
    // 会抛 TypeError。踩过：那一下报错被 catch 吞了，而「已启动」的标志已经
    // 置位，于是主题偏好永远没应用上，界面一直跟着系统走。
    var bridge = api();
    if (!bridge || typeof bridge.config !== 'function') { return; }
    if (state.themeDone && state.dataDone) { return; }

    if (!state.themeDone) {
      try {
        setThemePref((await bridge.config()).theme);
        state.themeDone = true;
      } catch (err) { /* 桥还没完全就绪，下一轮再来 */ }
    }

    if (!state.dataDone) {
      try {
        state.snapshot = await bridge.snapshot();
        state.dataDone = true;
      } catch (err) { /* 同上 */ }
    }

    if (!state.snapshot) {
      state.snapshot = { agents: [], totals: { running: 0, waiting: 0, unread: 0 } };
    }

    render(state.snapshot);
    if (state.dataDone) { loadIcons(state.snapshot.agents || []); }
  }

  function booted() {
    return state.themeDone && state.dataDone;
  }

  window.addEventListener('pywebviewready', boot);

  /* 兜底轮询。

     光靠 pywebviewready 不够：它可能在桥就绪之前就派发完，也可能那一刻本页
     还没拿到 pywebview 对象。改成轮询到一个上限为止——每一项各自认自己的
     完成标志，所以即使中间某轮失败，下一轮也会把没做完的补上。 */
  var bootTries = 0;
  (function bootPoll() {
    boot();
    if (!booted() && ++bootTries < 60) { window.setTimeout(bootPoll, 150); }
  })();
  // 桥还没就绪时的兜底，避免页面空白
  window.setTimeout(boot, 400);
})();
